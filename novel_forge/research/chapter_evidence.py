"""One-shot, cacheable external evidence routing for chapter generation.

The provider is called only here, before semantic chapter stages start.  The
resulting pack is a run-local projection: factual cards may support validation,
while inspiration cards are deliberately abstract and can never enter canon.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Callable, Literal

from novel_forge.narrative_state.evidence_contracts import (
    RetrievalEvidenceCard,
    RetrievalEvidencePack,
)
from novel_forge.pipeline.artifact_manifest import ArtifactManifest
from novel_forge.pipeline.context_governance import estimate_json_tokens, select_ranked_evidence
from novel_forge.research.contracts import (
    ResearchDossier,
    ResearchQuery,
    ResearchReport,
)
from novel_forge.research.evidence import MissingMandatoryResearchEvidence
from novel_forge.research.providers import NoopResearchProvider, provider_from_settings
from novel_forge.research.service import (
    build_research_brief,
    build_research_runtime_config,
    research_runtime_fingerprint,
)

ChapterResearchMode = Literal["long", "short"]
_INSTRUCTION_PATTERNS = (
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?",
        r"(?:system|assistant|developer)\s*(?:prompt|message|instruction)?\s*:",
        r"(?:follow|execute|obey)\s+(?:these|the following|my)\s+instructions?",
        r"忽略(?:以上|之前|前面).{0,12}(?:指令|要求|提示词)",
        r"(?:系统|开发者|助手)指令\s*[:：]",
        r"你现在是.{0,30}(?:助手|模型|角色)",
    )
)
_ACCURACY_TERMS = (
    "考据",
    "准确",
    "专业",
    "史实",
    "现实",
    "事实",
    "查证",
    "fact check",
    "accurate",
    "professional",
)
_INSPIRATION_TERMS = ("灵感", "多样", "避免重复", "inspiration", "variety")
_SCENE_MECHANISMS: dict[str, tuple[str, ...]] = {
    "dialogue_standoff": ("说", "问", "回答", "沉默", "对话", "争执"),
    "movement_transition": ("走", "跑", "车", "赶到", "离开", "回到"),
    "object_clue": ("发现", "线索", "物件", "查看", "翻开", "拿起"),
    "observation_wait": ("等待", "注视", "观察", "望着", "听见", "看见"),
    "procedural_obstacle": ("流程", "手续", "权限", "检查", "操作", "失败"),
}


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _hash_payload(value: Any) -> str:
    raw = json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _bounded_text(value: Any, *, limit: int) -> str:
    text = _clean(value)
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _sanitize_untrusted_excerpt(value: Any, *, fallback: str = "") -> str:
    """Remove instruction-like fragments before any provider text reaches a prompt."""

    fragments = re.split(r"[\r\n]+|(?<=[。！？!?.])\s+", str(value or ""))
    safe: list[str] = []
    for fragment in fragments:
        cleaned = _clean(fragment)
        if not cleaned or any(pattern.search(cleaned) for pattern in _INSTRUCTION_PATTERNS):
            continue
        safe.append(cleaned)
    return _bounded_text(" ".join(safe) or fallback, limit=300)


def _extract_texts(value: Any, *, keys: tuple[str, ...]) -> list[str]:
    payload = _plain(value)
    if not isinstance(payload, dict):
        return []
    result: list[str] = []
    for key in keys:
        raw = payload.get(key)
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            if isinstance(item, dict):
                item = item.get("text") or item.get("description") or item.get("note")
            text = _bounded_text(item, limit=220)
            if text and text not in result:
                result.append(text)
    return result


def _chapter_instruction(user_intent: dict[str, Any]) -> str:
    chapter_card = user_intent.get("chapter_instruction")
    if isinstance(chapter_card, dict):
        return _clean(chapter_card.get("value") or chapter_card.get("instruction"))
    return _clean(chapter_card)


def _explicitly_requests(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def _load_outline_fact_risks(storage: Any, layout: Any, chapter_number: int) -> list[str]:
    path = layout.reports_dir / "outline_research_grounding.json"
    if not storage.exists(path):
        return []
    try:
        payload = storage.load_json(path)
    except Exception:
        return []
    result: list[str] = []
    for item in payload.get("chapter_notes", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or int(item.get("chapter_number", 0) or 0) != chapter_number:
            continue
        for value in item.get("fact_risks", []) or []:
            text = _bounded_text(value, limit=220)
            if text and text not in result:
                result.append(text)
    return result


def _recent_scene_signature(layout: Any, chapter_number: int) -> tuple[str, bool]:
    signatures: list[list[str]] = []
    for number in range(max(1, chapter_number - 3), chapter_number):
        path = layout.chapter_path(number)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        scores = {
            mechanism: sum(text.count(keyword) for keyword in keywords)
            for mechanism, keywords in _SCENE_MECHANISMS.items()
        }
        signatures.append(
            [
                name
                for name, score in sorted(scores.items(), key=lambda item: -item[1])
                if score > 0
            ][:2]
        )
    if not signatures:
        return "none", False
    common = set(signatures[0])
    for signature in signatures[1:]:
        common.intersection_update(signature)
    repeated = len(signatures) >= 3 and bool(common)
    return _hash_payload(signatures), repeated


def _last_inspiration_chapter(layout: Any, chapter_number: int) -> int:
    for number in range(chapter_number - 1, 0, -1):
        path = layout.reports_dir / f"chapter_{number:03d}_research_evidence.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("inspiration_hashes"):
            return number
    return 0


def _prior_inspiration_hashes(storage: Any, layout: Any, chapter_number: int) -> set[str]:
    """Load the existing report ledger instead of creating a second dedup store."""

    hashes: set[str] = set()
    for number in range(1, chapter_number):
        path = layout.reports_dir / f"chapter_{number:03d}_research_evidence.json"
        if not storage.exists(path):
            continue
        try:
            payload = storage.load_json(path)
        except Exception:
            continue
        for value in payload.get("inspiration_hashes", []) if isinstance(payload, dict) else []:
            content_hash = _clean(value)
            if content_hash:
                hashes.add(content_hash)
    return hashes


def _drop_repeated_inspiration(
    pack: RetrievalEvidencePack,
    *,
    prior_hashes: set[str],
) -> tuple[RetrievalEvidencePack, int]:
    if not prior_hashes:
        return pack, 0
    selected: list[RetrievalEvidenceCard] = []
    omitted = 0
    for card in pack.evidence_cards:
        if card.kind == "external_inspiration" and card.content_hash in prior_hashes:
            omitted += 1
            continue
        selected.append(card)
    if not omitted:
        return pack, 0
    used_tokens = sum(
        estimate_json_tokens(card.model_dump(mode="json")) for card in selected
    )
    total_omitted = pack.omitted_candidate_count_lower_bound + omitted
    return (
        pack.model_copy(
            update={
                "source_hashes": list(dict.fromkeys(card.content_hash for card in selected)),
                "evidence_cards": selected,
                "estimated_evidence_tokens": used_tokens,
                "omitted_candidate_count_lower_bound": total_omitted,
                "has_more_evidence": total_omitted > 0,
            }
        ),
        omitted,
    )


def _inspiration_mechanism(title: str, snippet: str, *, index: int) -> str:
    source = f"{title} {snippet}".casefold()
    signals: list[str] = []
    signal_groups = (
        ("流程阻力", ("流程", "手续", "procedure", "queue", "wait")),
        ("空间限制", ("空间", "走廊", "room", "space", "route")),
        ("环境干扰", ("天气", "噪声", "weather", "noise", "light")),
        ("角色分工", ("协作", "职责", "team", "staff", "role")),
        ("物件反馈", ("工具", "设备", "device", "tool", "material")),
    )
    for label, keywords in signal_groups:
        if any(keyword.casefold() in source for keyword in keywords):
            signals.append(label)
    if not signals:
        fallbacks = ("时限压力", "信息不对称", "环境反作用", "角色协作失配")
        signals.append(fallbacks[index % len(fallbacks)])
    return (
        "只取抽象实现机制：将"
        + "、".join(signals[:2])
        + "变成角色当下决策的阻力与反馈；重新设计人物、情节和表达，不复用来源原文。"
    )


def _build_queries(
    *,
    spec: Any,
    chapter_number: int,
    chapter_outline: Any,
    fact_gaps: list[str],
    query_hint: str,
    request_fact: bool,
    request_inspiration: bool,
) -> list[ResearchQuery]:
    spec_payload = _plain(spec)
    outline_payload = _plain(chapter_outline)
    if not isinstance(spec_payload, dict):
        spec_payload = {}
    if not isinstance(outline_payload, dict):
        outline_payload = {}
    genre = _clean(spec_payload.get("genre"))
    world = _bounded_text(spec_payload.get("world_hint"), limit=140)
    chapter_focus = _bounded_text(
        outline_payload.get("summary")
        or outline_payload.get("goal")
        or outline_payload.get("title")
        or spec_payload.get("theme"),
        limit=160,
    )
    queries: list[ResearchQuery] = []
    if request_fact:
        focus = query_hint or "；".join(fact_gaps[:2]) or chapter_focus
        queries.append(
            ResearchQuery(
                query=_bounded_text(
                    f"{focus} {world} {genre} 可核验事实 专业资料",
                    limit=320,
                ),
                rationale=f"第 {chapter_number} 章存在必须校准的事实缺口。",
                intent="external_fact",
                priority="must",
                risk_if_missing="无法可靠支撑本章必须准确的事实。",
            )
        )
    if request_inspiration:
        queries.append(
            ResearchQuery(
                query=_bounded_text(
                    f"{chapter_focus} {world} {genre} 生活细节 场景阻力 互动机制 感官通道",
                    limit=320,
                ),
                rationale=f"为第 {chapter_number} 章补充不改变主线的抽象场景机制。",
                intent="external_inspiration",
                priority="nice",
            )
        )
    return queries[:2]


def _cards_from_report(
    report: ResearchReport,
) -> tuple[list[RetrievalEvidenceCard], ResearchDossier]:
    wants_fact = any(query.intent == "external_fact" for query in report.queries)
    wants_inspiration = any(query.intent == "external_inspiration" for query in report.queries)
    cards: list[RetrievalEvidenceCard] = []
    facts: list[str] = []
    inspirations: list[str] = []
    refs: list[str] = []
    for index, source in enumerate(report.sources):
        source_ref = _clean(source.url) or f"research:{report.provider}:{index + 1}"
        if source_ref not in refs:
            refs.append(source_ref)
        if wants_fact:
            excerpt = _sanitize_untrusted_excerpt(source.snippet, fallback=source.title)
            if excerpt:
                facts.append(excerpt)
                cards.append(
                    RetrievalEvidenceCard(
                        card_id=f"chapter-research:{_hash_payload(['fact', source_ref, excerpt])[:20]}",
                        kind="external_fact",
                        source_ref=source_ref,
                        excerpt=excerpt,
                        authority="supporting",
                    )
                )
        if wants_inspiration and len(inspirations) < 3:
            mechanism = _inspiration_mechanism(source.title, source.snippet, index=index)
            inspirations.append(mechanism)
            cards.append(
                RetrievalEvidenceCard(
                    card_id=(
                        f"chapter-research:{_hash_payload(['inspiration', source_ref, mechanism])[:20]}"
                    ),
                    kind="external_inspiration",
                    source_ref=source_ref,
                    excerpt=mechanism,
                    authority="supporting",
                )
            )
    dossier = ResearchDossier(
        enabled=report.enabled,
        provider=report.provider,
        status=report.status,
        summary=_bounded_text(report.brief.summary, limit=800),
        real_world_constraints=facts[:5],
        inspiration_notes=inspirations[:3],
        uncertainty_notes=list(report.warnings)[:6],
        source_refs=refs[:8],
        warnings=list(report.warnings)[:8],
        created_at=report.created_at,
        spec_fingerprint=report.spec_fingerprint,
        research_report_fingerprint=_hash_payload(report.model_dump(mode="json")),
        config_fingerprint=report.config_fingerprint,
    )
    return cards, dossier


def _build_pack(
    *, request_fingerprint: str, queries: list[ResearchQuery], cards: list[RetrievalEvidenceCard]
) -> RetrievalEvidencePack:
    mandatory = (
        [card for card in cards if card.kind == "external_fact"][:1]
        if any(query.priority == "must" for query in queries)
        else []
    )
    selection = select_ranked_evidence(
        cards,
        mandatory_items=mandatory,
        token_budget=1200,
        estimate_tokens=lambda card: estimate_json_tokens(card.model_dump(mode="json")),
        identity=lambda card: card.card_id,
    )
    selected = list(selection.selected)
    return RetrievalEvidencePack(
        pack_id=f"chapter-research:{request_fingerprint[:20]}",
        purpose="chapter_generation",
        query="；".join(query.query for query in queries),
        source_hashes=list(dict.fromkeys(card.content_hash for card in selected)),
        evidence_cards=selected,
        candidate_limit=len(cards),
        evidence_token_budget=1200,
        estimated_evidence_tokens=selection.used_tokens,
        retrieved_candidate_count=len(cards),
        omitted_candidate_count_lower_bound=selection.omitted_count,
        has_more_evidence=selection.omitted_count > 0,
    )


@dataclass(frozen=True)
class ChapterResearchEvidence:
    pack: RetrievalEvidencePack | None
    uncertainty: tuple[str, ...] = ()
    cache_hit: bool = False
    query_count: int = 0
    inspiration_hashes: tuple[str, ...] = ()
    inspiration_duplicates_omitted: int = 0

    def prompt_context(self) -> dict[str, Any]:
        return {
            "research_evidence_pack": (
                self.pack.model_dump(mode="json") if self.pack is not None else {}
            ),
            "research_uncertainty": list(self.uncertainty),
        }


async def prepare_chapter_research_evidence(
    *,
    storage: Any,
    layout: Any,
    settings: Any,
    spec: Any,
    chapter_number: int,
    chapter_outline: Any,
    chapter_contract: Any,
    user_intent: dict[str, Any],
    enabled: bool,
    provider_name: str = "auto",
    query_hint: str = "",
    mode: ChapterResearchMode,
    phase_boundary: bool = False,
    on_step: Callable[[str, Any], None] | None = None,
) -> ChapterResearchEvidence:
    """Build or restore exactly one bounded external evidence pack for a run."""

    if not enabled:
        return ChapterResearchEvidence(pack=None)
    instruction = _chapter_instruction(user_intent)
    fact_gaps = _extract_texts(
        chapter_contract,
        keys=("fact_risks", "must_facts", "grounding_notes", "research_grounding"),
    )
    if mode == "long":
        fact_gaps = list(
            dict.fromkeys([*fact_gaps, *_load_outline_fact_risks(storage, layout, chapter_number)])
        )
    explicit_accuracy = bool(_clean(query_hint)) or _explicitly_requests(
        instruction, _ACCURACY_TERMS
    )
    refresh_enabled = bool(getattr(settings, "chapter_research_refresh_enabled", False))
    inspiration_enabled = bool(
        getattr(settings, "chapter_research_inspiration_enabled", False)
    )
    request_fact = bool(refresh_enabled and (fact_gaps or explicit_accuracy))
    diversity_fingerprint, repeated_mechanism = _recent_scene_signature(layout, chapter_number)
    cooldown = max(1, int(getattr(settings, "chapter_research_inspiration_cooldown", 3) or 3))
    last_inspiration = _last_inspiration_chapter(layout, chapter_number) if mode == "long" else 0
    explicit_inspiration = _explicitly_requests(instruction, _INSPIRATION_TERMS)
    cooled_down = not last_inspiration or chapter_number - last_inspiration >= cooldown
    request_inspiration = bool(
        inspiration_enabled
        and (phase_boundary or repeated_mechanism or explicit_inspiration)
        and (cooled_down or phase_boundary or explicit_inspiration)
    )
    queries = _build_queries(
        spec=spec,
        chapter_number=chapter_number,
        chapter_outline=chapter_outline,
        fact_gaps=fact_gaps,
        query_hint=_clean(query_hint),
        request_fact=request_fact,
        request_inspiration=request_inspiration,
    )
    if not queries:
        if on_step is not None and (refresh_enabled or inspiration_enabled):
            inspiration_waiting = bool(
                inspiration_enabled
                and repeated_mechanism
                and not cooled_down
                and not phase_boundary
                and not explicit_inspiration
            )
            reason = "inspiration_cooldown" if inspiration_waiting else "no_research_gap"
            reason_label = (
                f"低频灵感仍在 {cooldown} 章冷却期内"
                if inspiration_waiting
                else "本章无必须核实的事实缺口，也未触发多样性补充"
            )
            on_step(
                "chapter_research_skipped",
                {
                    "chapter": chapter_number,
                    "reason": reason,
                    "reason_label": reason_label,
                    "cooldown": cooldown,
                },
            )
        return ChapterResearchEvidence(pack=None)

    config_fingerprint = research_runtime_fingerprint(settings, provider_name)
    request_payload = {
        "schema_version": 1,
        "mode": mode,
        "chapter_number": chapter_number,
        "spec": _plain(spec),
        "chapter_outline": _plain(chapter_outline),
        "chapter_contract": _plain(chapter_contract),
        "instruction": instruction,
        "query_hint": _clean(query_hint),
        "queries": [query.model_dump(mode="json") for query in queries],
        "diversity_fingerprint": diversity_fingerprint,
        "provider": provider_name,
        "config_fingerprint": config_fingerprint,
    }
    request_fingerprint = _hash_payload(request_payload)
    stem = "short" if mode == "short" else f"chapter_{chapter_number:03d}"
    evidence_path = layout.reports_dir / f"{stem}_research_evidence.json"
    report_path = layout.reports_dir / f"{stem}_research_report.json"
    dossier_path = layout.reports_dir / f"{stem}_research_dossier.json"
    if storage.exists(evidence_path):
        try:
            cached = storage.load_json(evidence_path)
            if cached.get("request_fingerprint") == request_fingerprint:
                if cached.get("status") == "blocked":
                    raise MissingMandatoryResearchEvidence(
                        _clean(cached.get("error")) or "must research evidence missing"
                    )
                pack_payload = cached.get("pack")
                pack = (
                    RetrievalEvidencePack.model_validate(pack_payload)
                    if isinstance(pack_payload, dict) and pack_payload.get("pack_id")
                    else None
                )
                cached_result = ChapterResearchEvidence(
                    pack=pack,
                    uncertainty=tuple(cached.get("uncertainty", []) or []),
                    cache_hit=True,
                    query_count=len(queries),
                    inspiration_hashes=tuple(cached.get("inspiration_hashes", []) or []),
                    inspiration_duplicates_omitted=int(
                        cached.get("inspiration_duplicates_omitted", 0) or 0
                    ),
                )
                if on_step is not None:
                    on_step(
                        "chapter_research_cache_hit",
                        {
                            "chapter": chapter_number,
                            "queries": len(queries),
                            "path": str(evidence_path),
                            "inspiration_hashes": list(cached_result.inspiration_hashes),
                            "inspiration_duplicates_omitted": (
                                cached_result.inspiration_duplicates_omitted
                            ),
                        },
                    )
                return cached_result
        except MissingMandatoryResearchEvidence:
            raise
        except Exception:
            pass

    if on_step is not None:
        on_step(
            "chapter_research_start",
            {"chapter": chapter_number, "queries": len(queries), "provider": provider_name},
        )
    provider = provider_from_settings(settings, provider_name)
    created_at = datetime.now(UTC).isoformat()
    warnings: list[str] = []
    if isinstance(provider, NoopResearchProvider):
        search_result = await provider.search(queries)
        warnings.append("no research provider configured")
    else:
        try:
            search_result = await provider.search(queries)
        except Exception as exc:  # noqa: BLE001 - converted into typed must/optional handling
            search_result = None
            warnings.append(f"{type(exc).__name__}: {exc}")
    sources = list(search_result.sources) if search_result is not None else []
    warnings.extend(list(search_result.warnings) if search_result is not None else [])
    status = "succeeded" if sources else ("failed" if warnings else "empty")
    report = ResearchReport(
        enabled=True,
        provider=(search_result.provider if search_result is not None else provider.name),
        status=status,
        config=build_research_runtime_config(settings, provider_name),
        config_fingerprint=config_fingerprint,
        queries=queries,
        sources=sources,
        brief=build_research_brief(search_result) if search_result is not None else {},
        warnings=list(dict.fromkeys(warnings)),
        created_at=created_at,
        spec_fingerprint=_hash_payload(_plain(spec)),
    )
    cards, dossier = _cards_from_report(report)
    storage.save_json(report_path, report.model_dump(mode="json"))
    storage.save_json(dossier_path, dossier.model_dump(mode="json"))
    has_must = any(query.priority == "must" for query in queries)
    fact_cards = [card for card in cards if card.kind == "external_fact"]
    if has_must and not fact_cards:
        error = "must research evidence missing: " + "；".join(
            query.query for query in queries if query.priority == "must"
        )
        storage.save_json(
            evidence_path,
            {
                "schema_version": 1,
                "status": "blocked",
                "request_fingerprint": request_fingerprint,
                "error": error,
                "pack": {},
                "uncertainty": list(dict.fromkeys(warnings)),
                "inspiration_hashes": [],
            },
        )
        ArtifactManifest(storage, layout).record_failure(
            artifact=f"{stem}:research_evidence",
            workflow="run_short" if mode == "short" else "run_chapter",
            step="chapter_research",
            status="blocked",
            input_hashes={"request": request_fingerprint},
            paths={"report": str(report_path), "evidence": str(evidence_path)},
            metadata={"queries": len(queries), "error": error},
            reusable_failure=True,
        )
        raise MissingMandatoryResearchEvidence(error)

    pack = _build_pack(request_fingerprint=request_fingerprint, queries=queries, cards=cards)
    inspiration_duplicates_omitted = 0
    if mode == "long":
        pack, inspiration_duplicates_omitted = _drop_repeated_inspiration(
            pack,
            prior_hashes=_prior_inspiration_hashes(storage, layout, chapter_number),
        )
    inspiration_hashes = tuple(
        card.content_hash for card in pack.evidence_cards if card.kind == "external_inspiration"
    )
    uncertainty = tuple(dict.fromkeys([*warnings, *dossier.uncertainty_notes]))
    storage.save_json(
        evidence_path,
        {
            "schema_version": 1,
            "status": "succeeded" if pack.evidence_cards else "empty",
            "request_fingerprint": request_fingerprint,
            "pack": pack.model_dump(mode="json"),
            "uncertainty": list(uncertainty),
            "inspiration_hashes": list(inspiration_hashes),
            "inspiration_duplicates_omitted": inspiration_duplicates_omitted,
        },
    )
    ArtifactManifest(storage, layout).record_success(
        artifact=f"{stem}:research_evidence",
        workflow="run_short" if mode == "short" else "run_chapter",
        step="chapter_research",
        input_hashes={"request": request_fingerprint},
        output_hashes={"pack": _hash_payload(pack.model_dump(mode="json"))},
        paths={
            "report": str(report_path),
            "dossier": str(dossier_path),
            "evidence": str(evidence_path),
        },
        metadata={
            "queries": len(queries),
            "cards": len(pack.evidence_cards),
            "inspiration_hashes": list(inspiration_hashes),
            "inspiration_duplicates_omitted": inspiration_duplicates_omitted,
        },
        reuse_policy="same_input",
    )
    if on_step is not None:
        on_step(
            "chapter_research_ready",
            {
                "chapter": chapter_number,
                "queries": len(queries),
                "cards": len(pack.evidence_cards),
                "cache_hit": False,
                "inspiration_cards": len(inspiration_hashes),
                "inspiration_hashes": list(inspiration_hashes),
                "inspiration_duplicates_omitted": inspiration_duplicates_omitted,
            },
        )
    return ChapterResearchEvidence(
        pack=pack,
        uncertainty=uncertainty,
        cache_hit=False,
        query_count=len(queries),
        inspiration_hashes=inspiration_hashes,
        inspiration_duplicates_omitted=inspiration_duplicates_omitted,
    )


__all__ = ["ChapterResearchEvidence", "prepare_chapter_research_evidence"]
