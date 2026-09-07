"""Sub-module of novel_forge.desktop.pages.document_renderer.story_artifacts.

Migration P2-step (cluster B): all init-coherence renderers and helpers now
live here. Previously the helpers and 4 of the 6 render functions lived in
the parent ``__init__.py``, with the other 2 (``profile``, ``claims``,
``claim_ledger``) re-exported here. The reverse-coupling
``from .. import _coherence_*`` lines have been removed; this module now
owns the helpers outright.

Six render functions:

- ``render_init_coherence_profile`` (already here)
- ``render_init_coherence_claims``
- ``render_init_coherence_claim_ledger``
- ``render_init_coherence_index`` (newly migrated from ``__init__``)
- ``render_init_conflict_candidates``
- ``render_init_conflict_adjudication``
- ``render_init_claim_contract_coverage``
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from PySide6.QtWidgets import QTextBrowser

from novel_forge.desktop.pages.document_renderer.story_artifacts._common import (
    _fragment_table,
)
from novel_forge.desktop.pages.document_renderer_reports import generic_key_label
from novel_forge.desktop.pages.standalone.renderer_html import esc as _esc
from novel_forge.desktop.pages.standalone.renderer_html import html_wrap as _html_wrap
from novel_forge.desktop.pages.standalone.renderer_html import make_browser as _make_browser
from novel_forge.desktop.pages.standalone.renderer_html import nl2br as _nl2br

JsonDict = dict[str, Any]

__all__ = [
    "render_init_coherence_profile",
    "render_init_coherence_claims",
    "render_init_coherence_claim_ledger",
    "render_init_coherence_index",
    "render_init_conflict_candidates",
    "render_init_conflict_adjudication",
    "render_init_claim_contract_coverage",
]


# ── Cluster B helpers & label dictionaries ──────────────────────────
# (Originally in story_artifacts/__init__.py, now authoritative here.)

_CLAIM_TYPE_LABELS: dict[str, str] = {
    "state": "状态",
    "event": "事件",
    "payoff": "兑现",
    "dependency": "依赖",
    "relationship": "关系",
    "world_rule": "世界规则",
    "knowledge": "认知",
    "promise": "承诺",
    "other": "其他",
}

_INIT_COHERENCE_STAGE_LABELS: dict[str, str] = {
    "blueprint_coherence": "蓝图一致性",
    "outline_inheritance": "大纲继承",
    "contract_coherence": "契约一致性",
    "claim_contract_coverage": "契约覆盖",
    "init_readiness": "立项准入",
}

_INIT_COHERENCE_VERDICT_LABELS: dict[str, str] = {
    "accept": "通过",
    "defer": "延后观察",
    "ambiguous": "需复核",
    "needs_repair": "需修复",
    "reject": "阻断",
    "warn": "提醒",
}

_INIT_COHERENCE_ARTIFACT_LABELS: dict[str, str] = {
    "blueprint": "叙事蓝图",
    "outline": "章节大纲",
    "chapter_contracts": "章节契约",
    "narrative_contract": "叙事契约",
    "story_bible": "世界观",
    "character_bible": "角色设定",
}

_INIT_COHERENCE_SEVERITY_LABELS: dict[str, str] = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}


def _coherence_text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _coherence_items(value: Any, *, limit: int = 10) -> list[str]:
    if value in (None, "", [], {}):
        return []
    raw_items = value if isinstance(value, list) else [value]
    items: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            text = (
                item.get("label")
                or item.get("name")
                or item.get("id")
                or item.get("description")
                or item.get("summary")
                or ""
            )
        else:
            text = item
        cleaned = _coherence_text(text)
        if cleaned and cleaned not in items:
            items.append(cleaned)
    if len(items) > limit:
        return [*items[:limit], f"另有 {len(items) - limit} 项"]
    return items


def _coherence_join(value: Any, *, limit: int = 10, empty: str = "—") -> str:
    items = _coherence_items(value, limit=limit)
    return "、".join(items) if items else empty


def _coherence_count_row(label: str, value: Any) -> str:
    if isinstance(value, (dict, list)):
        count = len(value)
    else:
        count = int(bool(value))
    return f"<td>{_esc(label)}</td><td><b>{count}</b></td>"


def _coherence_stage_label(value: Any) -> str:
    raw = _coherence_text(value)
    return _INIT_COHERENCE_STAGE_LABELS.get(raw, raw or "未标记阶段")


def _coherence_artifact_label(value: Any) -> str:
    raw = _coherence_text(value)
    return _INIT_COHERENCE_ARTIFACT_LABELS.get(raw, raw or "未知产物")


def _coherence_tag(text: Any, *, tone: str = "muted") -> str:
    color, bg, border = {
        "ok": ("[[nf:status.success.warm]]", "rgba([[nf:status.success.warm]], 0.08)", "rgba([[nf:status.success.warm]], 0.16)"),
        "warn": ("[[nf:text.blueprint.badge]]", "rgba([[nf:status.warning.alt]], 0.10)", "rgba([[nf:status.warning.alt]], 0.18)"),
        "danger": ("[[nf:status.danger.deep]]", "rgba([[nf:status.danger.deep]], 0.09)", "rgba([[nf:status.danger.deep]], 0.18)"),
        "strong": ("[[nf:accent.deep]]", "rgba([[nf:accent.primary]], 0.10)", "rgba([[nf:accent.primary]], 0.25)"),
        "muted": ("[[nf:text.chapter.rail]]", "rgba([[nf:border.default]], 0.08)", "rgba([[nf:border.default]], 0.18)"),
    }.get(tone, ("[[nf:text.chapter.rail]]", "rgba([[nf:border.default]], 0.08)", "rgba([[nf:border.default]], 0.18)"))
    return (
        '<span class="tag-muted tag" '
        f'style="color:{color}; background:{bg}; border-color:{border};">'
        f"{_esc(str(text))}</span>"
    )


def _coherence_severity_tone(value: Any) -> str:
    severity = _coherence_text(value).lower()
    if severity in {"critical", "high"}:
        return "danger"
    if severity == "medium":
        return "warn"
    return "ok"


def _coherence_verdict_tone(value: Any, *, blocked: bool = False) -> str:
    verdict = _coherence_text(value).lower()
    if blocked or verdict == "reject":
        return "danger"
    if verdict in {"needs_repair", "ambiguous"}:
        return "warn"
    return "ok"


def _claim_chapter_label(raw: dict[str, Any]) -> str:
    chapter_range = raw.get("chapter_range")
    if isinstance(chapter_range, dict):
        start = chapter_range.get("start") or chapter_range.get("chapter_start")
        end = chapter_range.get("end") or chapter_range.get("chapter_end")
        if start and end and str(start) != str(end):
            return f"Ch.{start}-{end}"
        if start or end:
            return f"Ch.{start or end}"
    chapters = raw.get("chapter_numbers")
    if isinstance(chapters, list) and chapters:
        numbers = [str(number) for number in chapters[:8] if str(number).strip()]
        if numbers:
            suffix = "…" if len(chapters) > 8 else ""
            return "Ch." + "、".join(numbers) + suffix
    return "全局"


def _claim_subject(raw: dict[str, Any]) -> str:
    subject = _coherence_text(raw.get("subject_text"))
    if subject:
        return subject
    return _coherence_join(raw.get("subject_ids"), limit=5, empty="未标记主体")


def _claim_card(raw: dict[str, Any], *, compact: bool = False) -> str:
    claim_id = _coherence_text(raw.get("claim_id"), "claim")
    claim_type = _coherence_text(raw.get("claim_type"), "other")
    label = _CLAIM_TYPE_LABELS.get(claim_type, claim_type)
    artifact = _coherence_artifact_label(raw.get("artifact"))
    axis = _coherence_text(raw.get("axis"))
    confidence = raw.get("confidence")
    confidence_text = ""
    if isinstance(confidence, (int, float)):
        confidence_text = f"{float(confidence):.2f}"
    tags = [
        _coherence_tag(label, tone="strong"),
        _coherence_tag(artifact),
        _coherence_tag(_claim_chapter_label(raw)),
    ]
    if axis:
        tags.append(_coherence_tag(f"轴：{axis}"))
    if raw.get("irreversible"):
        tags.append(_coherence_tag("不可逆", tone="warn"))
    if confidence_text:
        tags.append(_coherence_tag(f"置信 {confidence_text}"))
    temporality = _coherence_text(raw.get("temporality"))
    if temporality and temporality != "unknown":
        tags.append(_coherence_tag(temporality))

    source = " / ".join(
        item
        for item in (
            _coherence_text(raw.get("source_path")),
            _coherence_text(raw.get("source_field")),
        )
        if item
    )
    body = [
        '<div class="rule-item">',
        '<div style="margin-bottom:4px;">',
        f"<b>{_esc(_claim_subject(raw))}</b>",
        f'<span style="margin-left:6px;">{"".join(tags)}</span>',
        "</div>",
        f'<div class="kv-value">{_nl2br(_coherence_text(raw.get("claim_text")))}</div>',
    ]
    if not compact:
        before_after = " → ".join(
            item
            for item in (
                _coherence_text(raw.get("state_before")),
                _coherence_text(raw.get("state_after")),
            )
            if item
        )
        if before_after:
            body.append(
                '<div class="kv-row"><span class="kv-label">状态变化</span> '
                f"{_esc(before_after)}</div>"
            )
        evidence = _coherence_text(raw.get("evidence"))
        if evidence:
            body.append(f'<div class="hint-block">{_nl2br(evidence)}</div>')
    body.append(
        '<div style="color:[[nf:text.muted]];font-size: 11pt;margin-top:5px;">'
        f"{_esc(claim_id)}" + (f" · {_esc(source)}" if source else "") + "</div>"
    )
    body.append("</div>")
    return "\n".join(body)


def _stage_payloads(data: JsonDict) -> list[tuple[str, JsonDict]]:
    stages = data.get("stages")
    if not isinstance(stages, dict):
        stage = _coherence_text(data.get("stage") or data.get("latest_stage"), "latest")
        return [(stage, data)]
    latest = _coherence_text(data.get("latest_stage"))
    ordered: list[tuple[str, JsonDict]] = []
    if latest and isinstance(stages.get(latest), dict):
        ordered.append((latest, stages[latest]))
    for stage, payload in stages.items():
        if stage == latest or not isinstance(payload, dict):
            continue
        ordered.append((str(stage), payload))
    return ordered


def render_init_coherence_profile(data: JsonDict) -> QTextBrowser:
    ...


def render_init_coherence_claims(claims: list[JsonDict]) -> QTextBrowser:
    ...


def render_init_coherence_claim_ledger(data: JsonDict) -> QTextBrowser:
    ...


def render_init_coherence_index(data: JsonDict) -> QTextBrowser:
    """Render init coherence retrieval index metadata."""
    structured = data.get("structured")
    memory = data.get("memory")
    ledger = data.get("ledger")
    structured = structured if isinstance(structured, dict) else {}
    memory = memory if isinstance(memory, dict) else {}
    ledger = ledger if isinstance(ledger, dict) else {}

    claims_by_id = structured.get("claims_by_id")
    subject_axis = structured.get("by_subject_axis")
    payoff = structured.get("by_payoff")
    claims_by_id = claims_by_id if isinstance(claims_by_id, dict) else {}
    subject_axis = subject_axis if isinstance(subject_axis, dict) else {}
    payoff = payoff if isinstance(payoff, dict) else {}

    parts: list[str] = []
    summary = _coherence_text(data.get("summary"))
    if summary:
        parts.append(f'<div class="hint-block">{_nl2br(summary)}</div>')
    rows = [
        ["一致性 Claims", str(len(claims_by_id))],
        ["主体+状态轴", str(len(subject_axis))],
        ["兑现索引", str(len(payoff))],
        ["向量降级", "是" if structured.get("degraded_memory") else "否"],
        ["账本活跃", str(ledger.get("active_claim_count", 0))],
        ["最新阶段", _coherence_stage_label(ledger.get("latest_stage"))],
    ]
    parts.append(_fragment_table(["指标", "值"], [[_esc(a), _esc(b)] for a, b in rows]))

    if subject_axis:
        rows = [
            [_esc(str(key)), _esc(str(len(value if isinstance(value, list) else [])))]
            for key, value in list(subject_axis.items())[:24]
        ]
        parts.append("<h2>主体状态轴索引</h2>")
        parts.append(_fragment_table(["索引键", "一致性 Claims"], rows))

    memory_meta = {
        key: value
        for key, value in memory.items()
        if key in {"backend", "count", "vector_count", "embedding_model", "degraded"}
    }
    if memory_meta:
        rows = [
            [_esc(generic_key_label(str(key))), _esc(str(value))]
            for key, value in memory_meta.items()
        ]
        parts.append("<h2>向量召回</h2>")
        parts.append(_fragment_table(["字段", "值"], rows))

    return _make_browser(_html_wrap("\n".join(parts), "一致性索引"))


def render_init_conflict_candidates(data: JsonDict) -> QTextBrowser:
    """Render retrieved init-coherence conflict candidates."""
    parts: list[str] = []
    for stage, payload in _stage_payloads(data):
        candidates = payload.get("candidates")
        candidates = candidates if isinstance(candidates, list) else []
        degraded = bool(payload.get("degraded_memory"))
        claims_count = payload.get("claims_count", 0)
        parts.append(
            '<div class="section" style="text-align:center; padding:16px;">'
            f'<div style="font-size: 20pt;font-weight:700;">{_esc(_coherence_stage_label(stage))}</div>'
            f'<div style="margin-top:6px;">{_coherence_tag(f"候选 {len(candidates)}", tone="strong")}'
            f"{_coherence_tag(f'一致性 Claims {claims_count}')}"
            f"{_coherence_tag('向量召回降级', tone='warn') if degraded else ''}</div>"
            "</div>"
        )
        summary = _coherence_text(payload.get("summary"))
        if summary:
            parts.append(f'<div class="hint-block">{_nl2br(summary)}</div>')
        if not candidates:
            parts.append('<div class="hint-block">未检索到冲突候选。</div>')
            continue
        for candidate in candidates[:80]:
            if not isinstance(candidate, dict):
                continue
            severity = _coherence_text(candidate.get("severity_hint"), "medium")
            tags = [
                _coherence_tag(
                    _INIT_COHERENCE_SEVERITY_LABELS.get(severity, severity),
                    tone=_coherence_severity_tone(severity),
                ),
                _coherence_tag(_coherence_text(candidate.get("candidate_type"), "candidate")),
                _coherence_tag(f"置信 {float(candidate.get('confidence', 0.0) or 0.0):.2f}"),
            ]
            claim_ids = _coherence_join(candidate.get("claim_ids"), limit=8)
            parts.append(
                '<div class="section">'
                f"<h3>{_esc(_coherence_text(candidate.get('candidate_id'), '候选'))}"
                f'<span style="margin-left:6px;">{"".join(tags)}</span></h3>'
                f'<div class="kv-row"><span class="kv-label">原因</span> '
                f"{_nl2br(_coherence_text(candidate.get('reason')))}</div>"
                f'<div class="kv-row"><span class="kv-label">Claims</span> {_esc(claim_ids)}</div>'
                f'<div class="kv-row"><span class="kv-label">召回来源</span> '
                f"{_esc(_coherence_join(candidate.get('retrieval_sources'), limit=8))}</div>"
                "</div>"
            )
            claims = candidate.get("claims")
            if isinstance(claims, list) and claims:
                parts.extend(
                    _claim_card(claim, compact=True)
                    for claim in claims[:4]
                    if isinstance(claim, dict)
                )
        if len(candidates) > 80:
            parts.append(
                f'<div class="hint-block">另有 {len(candidates) - 80} 个候选未展开。</div>'
            )

    if not parts:
        parts.append('<div class="hint-block">暂无冲突候选报告。</div>')
    return _make_browser(_html_wrap("\n".join(parts), "冲突候选报告"))


def _render_repair_scope(scopes: Any) -> str:
    if not isinstance(scopes, list) or not scopes:
        return '<span class="kv-value" style="color:[[nf:text.muted]];">未指定</span>'
    rows: list[list[str]] = []
    for scope in scopes[:24]:
        if not isinstance(scope, dict):
            continue
        rows.append(
            [
                _esc(_coherence_artifact_label(scope.get("artifact"))),
                _esc(_coherence_join(scope.get("chapters"), limit=10)),
                _esc(_coherence_join(scope.get("fields"), limit=10)),
                _esc(_coherence_join(scope.get("issue_ids"), limit=6)),
            ]
        )
    return _fragment_table(["产物", "章节", "字段", "问题"], rows)


def render_init_conflict_adjudication(data: JsonDict) -> QTextBrowser:
    """Render LLM adjudication for init-coherence conflict candidates."""
    parts: list[str] = []
    for stage, payload in _stage_payloads(data):
        verdict = _coherence_text(payload.get("verdict"), "accept").lower()
        blocked = bool(payload.get("blocked"))
        tone = _coherence_verdict_tone(verdict, blocked=blocked)
        verdict_label = _INIT_COHERENCE_VERDICT_LABELS.get(verdict, verdict)
        issues = payload.get("issues")
        issues = issues if isinstance(issues, list) else []
        repair_scope = payload.get("repair_scope")
        candidate_count = payload.get("candidate_count", 0)
        parts.append(
            '<div class="section" style="text-align:center; padding:18px;">'
            f'<div style="font-size: 22pt;font-weight:700;">{_esc(_coherence_stage_label(stage))}</div>'
            f'<div style="margin-top:8px;">{_coherence_tag(verdict_label, tone=tone)}'
            f"{_coherence_tag('阻断立项', tone='danger') if blocked else ''}"
            f"{_coherence_tag(f'问题 {len(issues)}')}"
            f"{_coherence_tag(f'候选 {candidate_count}')}</div>"
            "</div>"
        )
        summary = _coherence_text(payload.get("summary"))
        if summary:
            parts.append(f'<div class="hint-block">{_nl2br(summary)}</div>')
        change_intent = _coherence_text(payload.get("change_intent"))
        if change_intent:
            parts.append(
                '<div class="section"><h3>修复意图</h3>'
                f'<div class="kv-value">{_nl2br(change_intent)}</div></div>'
            )

        if issues:
            parts.append("<h2>风险项</h2>")
        for issue in issues:
            if not isinstance(issue, dict):
                parts.append(f'<div class="rule-item">{_nl2br(str(issue))}</div>')
                continue
            severity = _coherence_text(issue.get("severity"), "medium")
            title = _coherence_text(issue.get("id"), "issue")
            description = _coherence_text(
                issue.get("description") or issue.get("summary") or issue.get("reason")
            )
            tags = [
                _coherence_tag(
                    _INIT_COHERENCE_SEVERITY_LABELS.get(severity, severity),
                    tone=_coherence_severity_tone(severity),
                ),
                _coherence_tag(_coherence_join(issue.get("candidate_ids"), limit=5)),
            ]
            parts.append(
                '<div class="rule-item">'
                f'<div><b>{_esc(title)}</b><span style="margin-left:6px;">{"".join(tags)}</span></div>'
                f'<div class="kv-value">{_nl2br(description)}</div>'
                f'<div class="kv-row"><span class="kv-label">一致性 Claims</span> '
                f"{_esc(_coherence_join(issue.get('claim_ids'), limit=8))}</div>"
                "</div>"
            )
            issue_scope = issue.get("repair_scope")
            if isinstance(issue_scope, list) and issue_scope:
                parts.append(_render_repair_scope(issue_scope))

        parts.append("<h2>修复范围</h2>")
        parts.append(_render_repair_scope(repair_scope))
        preserve = _coherence_items(payload.get("preserve"), limit=12)
        if preserve:
            parts.append("<h2>需保留内容</h2>")
            parts.extend(f'<div class="theme-item">· {_nl2br(item)}</div>' for item in preserve)

    if not parts:
        parts.append('<div class="hint-block">暂无冲突裁决报告。</div>')
    return _make_browser(_html_wrap("\n".join(parts), "冲突裁决报告"))


def render_init_claim_contract_coverage(data: JsonDict) -> QTextBrowser:
    """Render Claim-to-contract coverage audit report."""
    items = data.get("items")
    items = items if isinstance(items, list) else []
    issues = data.get("issues")
    issues = issues if isinstance(issues, list) else []
    verdict = _coherence_text(data.get("verdict"), "accept").lower()
    blocked = bool(data.get("blocked"))
    tone = _coherence_verdict_tone(verdict, blocked=blocked)
    verdict_label = _INIT_COHERENCE_VERDICT_LABELS.get(verdict, verdict)
    status_counts = Counter(
        _coherence_text(item.get("coverage_status"), "unknown")
        for item in items
        if isinstance(item, dict)
    )
    priority_counts = Counter(
        _coherence_text(item.get("priority"), "P2") for item in items if isinstance(item, dict)
    )
    total_claims = data.get("total_claims", 0)
    uncovered_p0_p1 = data.get("uncovered_p0_p1", 0)

    parts: list[str] = [
        '<div class="section" style="text-align:center; padding:18px;">'
        '<div style="font-size: 22pt;font-weight:700;">一致性 Claims 契约覆盖</div>'
        f'<div style="margin-top:8px;">{_coherence_tag(verdict_label, tone=tone)}'
        f"{_coherence_tag('阻断立项', tone='danger') if blocked else ''}"
        f"{_coherence_tag(f'一致性 Claims {total_claims}')}"
        f"{_coherence_tag(f'未覆盖 P0/P1 {uncovered_p0_p1}', tone='warn')}"
        f"{_coherence_tag('降级', tone='warn') if data.get('degraded') else ''}</div>"
        "</div>"
    ]
    summary = _coherence_text(data.get("summary"))
    if summary:
        parts.append(f'<div class="hint-block">{_nl2br(summary)}</div>')

    metric_rows = [
        ["覆盖", str(data.get("covered_claims", 0))],
        ["未覆盖", str(data.get("uncovered_claims", 0))],
        ["过期/无效", str(data.get("stale_claims", 0))],
        ["P0 阻断", "是" if data.get("block_p0") else "否"],
        ["P1 阻断", "是" if data.get("block_p1") else "否"],
    ]
    parts.append(_fragment_table(["指标", "值"], [[_esc(a), _esc(b)] for a, b in metric_rows]))

    distribution_rows = [
        [
            "覆盖状态",
            " / ".join(f"{_coverage_status_label(k)} {v}" for k, v in status_counts.items()),
        ],
        ["优先级", " / ".join(f"{k} {v}" for k, v in priority_counts.items())],
    ]
    parts.append(
        _fragment_table(["维度", "分布"], [[_esc(a), _esc(b)] for a, b in distribution_rows])
    )

    if issues:
        parts.append("<h2>未覆盖风险</h2>")
        for issue in issues[:40]:
            if not isinstance(issue, dict):
                continue
            severity = _coherence_text(issue.get("severity"), "medium")
            parts.append(
                '<div class="rule-item">'
                f"<div><b>{_esc(_coherence_text(issue.get('id'), 'coverage_issue'))}</b>"
                f'<span style="margin-left:6px;">'
                f"{_coherence_tag(severity, tone=_coherence_severity_tone(severity))}"
                f"{_coherence_tag(issue.get('priority', ''))}</span></div>"
                f'<div class="kv-value">{_nl2br(_coherence_text(issue.get("description")))}</div>'
                "</div>"
            )

    visible_items = [item for item in items if isinstance(item, dict)]
    if visible_items:
        parts.append("<h2>覆盖明细</h2>")
    for item in visible_items[:120]:
        status = _coherence_text(item.get("coverage_status"), "unknown")
        status_tone = _coverage_status_tone(status)
        claim_type = _coherence_text(item.get("claim_type"), "other")
        refs = item.get("matched_contract_refs")
        refs = refs if isinstance(refs, list) else []
        tags = [
            _coherence_tag(_coverage_status_label(status), tone=status_tone),
            _coherence_tag(_CLAIM_TYPE_LABELS.get(claim_type, claim_type), tone="strong"),
            _coherence_tag(_coherence_text(item.get("priority"), "P2")),
            _coherence_tag(_coherence_stage_label(item.get("stage"))),
            _coherence_tag(_coherence_join(item.get("chapter_scope"), limit=8, empty="全局")),
        ]
        parts.append(
            '<div class="rule-item">'
            f"<div><b>{_esc(_coherence_text(item.get('claim_id'), 'claim'))}</b>"
            f'<span style="margin-left:6px;">{"".join(tags)}</span></div>'
            f'<div class="kv-value">{_nl2br(_coherence_text(item.get("claim_text")))}</div>'
            f'<div class="kv-row"><span class="kv-label">原因</span> '
            f"{_nl2br(_coherence_text(item.get('reason')))}</div>"
            "</div>"
        )
        if refs:
            rows = [
                [
                    _esc(str(ref.get("chapter_number", ""))),
                    _esc(_coherence_text(ref.get("field"))),
                    _nl2br(_coherence_text(ref.get("text"))),
                ]
                for ref in refs[:8]
                if isinstance(ref, dict)
            ]
            parts.append(_fragment_table(["章节", "契约字段", "覆盖文本"], rows))
        suggested = _coherence_join(item.get("suggested_contract_fields"), limit=8)
        if suggested:
            parts.append(f'<div class="hint-block">建议补入章节契约字段：{_esc(suggested)}</div>')

    if len(visible_items) > 120:
        parts.append(
            f'<div class="hint-block">另有 {len(visible_items) - 120} 条覆盖明细未展开。</div>'
        )
    if not visible_items:
        parts.append('<div class="hint-block">暂无覆盖审计明细。</div>')
    return _make_browser(_html_wrap("\n".join(parts), "契约覆盖"))


def _coverage_status_label(value: Any) -> str:
    status = _coherence_text(value).lower()
    return {
        "covered": "已覆盖",
        "uncovered": "未覆盖",
        "deferred": "延后",
        "not_applicable": "不适用",
    }.get(status, status or "未知")


def _coverage_status_tone(value: Any) -> str:
    status = _coherence_text(value).lower()
    if status == "covered":
        return "ok"
    if status == "uncovered":
        return "danger"
    if status == "deferred":
        return "warn"
    return "muted"
