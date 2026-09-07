"""Sub-module of novel_forge.desktop.pages.document_renderer.

Auto-generated in the M3.2 split. Contains relationship_matrix.py renderers.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Callable

from PySide6.QtWidgets import (
    QTextBrowser,
    QWidget,
)

from novel_forge.desktop.pages.standalone.renderer_html import (
    esc as _esc,
)
from novel_forge.desktop.pages.standalone.renderer_html import (
    html_wrap as _html_wrap,
)
from novel_forge.desktop.pages.standalone.renderer_html import (
    make_browser as _make_browser,
)

JsonDict = dict[str, Any]
FeedbackMarker = tuple[float, float, float, JsonDict, JsonDict]
WeaveLine = tuple[float, float, float, JsonDict, JsonDict]
DocumentRenderer = Callable[[dict[str, Any]], QWidget]

# Constants extracted from the original monolithic document_renderers.py.
_RELATION_TYPE_LABELS: dict[str, str] = {
    "relationship": "关系",
    "romantic_tension": "情感张力",
    "antagonism": "对立",
    "family": "亲缘",
    "mentor_student": "师生",
    "professional": "职业",
    "alliance": "同盟",
    "rivalry": "竞争",
    "community": "群体",
    "identity_link": "身份链接",
    "mistaken_as": "误认",
    "alias_of": "别名",
    "reincarnation_of": "转世",
    "related_to": "相关",
}

_RELATION_HEALTH_CODES = {
    "isolated_active_character",
    "relationship_matrix_unknown_character",
    "invalid_relationship_matrix_item",
    "self_relationship",
    "compound_relationship_key",
    "non_character_relationship_target",
}


def _shorten_text(value: object, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _as_dict_list(value: object) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _entity_key(entity: JsonDict) -> str:
    entity_id = str(entity.get("entity_id") or entity.get("id") or "").strip()
    if entity_id:
        return entity_id
    return "|".join(
        (
            str(entity.get("entity_type") or "").strip(),
            str(entity.get("name") or "").strip(),
        )
    )


def _unique_entities(entities: list[JsonDict]) -> tuple[list[JsonDict], int]:
    seen: set[str] = set()
    unique: list[JsonDict] = []
    duplicates = 0
    for entity in entities:
        key = _entity_key(entity)
        if key and key in seen:
            duplicates += 1
            continue
        if key:
            seen.add(key)
        unique.append(entity)
    return unique, duplicates


def _tag_html(text: object, *, muted: bool = False) -> str:
    cls = "tag tag-muted" if muted else "tag"
    return f'<span class="{cls}">{_esc(str(text or ""))}</span>'


def _metric_tiles(items: Sequence[tuple[str, object, str]]) -> str:
    cells = []
    for label, value, hint in items:
        cells.append(
            '<td style="background:rgba([[nf:bg.surface]], 0.74);'
            "border:1px solid rgba([[nf:border.default]], 0.12);"
            'border-radius:8px;padding:10px 12px;">'
            f'<div style="font-size: 20pt;font-weight:800;color:[[nf:accent.deep]];">{_esc(str(value))}</div>'
            f'<div style="font-size: 12pt;font-weight:700;color:[[nf:text.body.alt]];">{_esc(label)}</div>'
            f'<div style="font-size: 11pt;color:[[nf:text.muted]];">{_esc(hint)}</div>'
            "</td>"
        )
    return (
        '<table style="border-collapse:separate;border-spacing:8px;table-layout:fixed;'
        'margin:4px -8px 10px -8px;"><tr>' + "".join(cells) + "</tr></table>"
    )


def _render_relation_table(edges: list[JsonDict], title: str) -> str:
    if not edges:
        return ""
    rows: list[str] = []
    for edge in edges:
        source = edge.get("source_name") or edge.get("character_a") or edge.get("source_id") or "?"
        target = edge.get("target_name") or edge.get("character_b") or edge.get("target_id") or "?"
        relation = edge.get("relation_type") or edge.get("link_type") or "relationship"
        label = _RELATION_TYPE_LABELS.get(str(relation), str(relation))
        confidence = edge.get("confidence")
        confidence_html = ""
        if isinstance(confidence, (int, float)):
            confidence_html = f"{float(confidence):.0%}"
        description = _shorten_text(edge.get("description") or edge.get("label"), 180)
        rows.append(
            "<tr>"
            f"<td><b>{_esc(str(source))}</b> → <b>{_esc(str(target))}</b></td>"
            f"<td>{_tag_html(label)}</td>"
            f"<td>{_esc(description)}</td>"
            f"<td>{_esc(confidence_html)}</td>"
            "</tr>"
        )
    return (
        f"<h2>{_esc(title)}</h2>"
        "<table><thead><tr><th>端点</th><th>类型</th><th>说明</th><th>置信</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _render_audit_items(items: list[JsonDict]) -> str:
    if not items:
        return ""
    rendered: list[str] = []
    for item in items:
        severity = str(item.get("severity") or "info")
        code = str(item.get("code") or "audit")
        message = _shorten_text(item.get("message"), 240)
        action = _shorten_text(item.get("suggested_action"), 180)
        rendered.append(
            '<div class="hint-block">'
            f"{_tag_html(severity)} {_tag_html(code, muted=True)}"
            f'<div style="margin-top:6px;">{_esc(message)}</div>'
            + (f'<div style="color:[[nf:text.muted]];margin-top:4px;">{_esc(action)}</div>' if action else "")
            + "</div>"
        )
    return f"<h2>审计提示</h2>{''.join(rendered)}"


def _relationship_health_metrics(
    edges: list[JsonDict],
    *,
    roster: list[JsonDict] | None = None,
    audit: list[JsonDict] | None = None,
) -> list[tuple[str, object, str]]:
    type_counts: dict[str, int] = {}
    connected: set[str] = set()
    for edge in edges:
        source = str(edge.get("source_name") or edge.get("character_a") or "").strip()
        target = str(edge.get("target_name") or edge.get("character_b") or "").strip()
        relation = str(edge.get("relation_type") or edge.get("type") or "relationship")
        if source and target:
            connected.update((source, target))
        type_counts[relation] = type_counts.get(relation, 0) + 1
    isolated_from_audit = {
        str(item.get("character_name") or "").strip()
        for item in (audit or [])
        if item.get("code") == "isolated_active_character"
        and str(item.get("character_name") or "").strip()
    }
    isolated_from_roster = {
        str(item.get("name") or "").strip()
        for item in (roster or [])
        if str(item.get("name") or "").strip()
        and str(item.get("role") or "")
        in {"protagonist", "deuteragonist", "antagonist", "supporting"}
        and str(item.get("status") or "active") != "retired"
        and str(item.get("name") or "").strip() not in connected
    }
    isolated = isolated_from_audit or isolated_from_roster
    type_summary = (
        "、".join(
            f"{_RELATION_TYPE_LABELS.get(key, key)} {value}"
            for key, value in sorted(type_counts.items(), key=lambda item: item[0])
        )
        or "暂无类型"
    )
    return [
        ("关系边", len(edges), type_summary),
        ("连接角色", len(connected), "至少拥有一条人物关系边"),
        ("关系类型", len(type_counts), "最终矩阵中的关系分类"),
        ("孤立关键人物", len(isolated), "主角/反派/重要配角无关系边"),
    ]


def _relationship_health_audit(audit: list[JsonDict]) -> list[JsonDict]:
    return [item for item in audit if str(item.get("code") or "") in _RELATION_HEALTH_CODES]


def render_character_relationship_matrix(
    data: JsonDict,
    *,
    character_system: JsonDict | None = None,
) -> QTextBrowser:
    """Render the final init-time character relationship matrix."""
    relationship_edges = _as_dict_list(data.get("relationship_matrix"))
    seed_edges = _as_dict_list(data.get("relationship_seed_matrix"))
    evidence = _as_dict_list(data.get("relationship_candidate_evidence"))
    system = character_system or {}
    roster = _as_dict_list(system.get("roster"))
    audit = _as_dict_list(system.get("audit"))
    health_audit = _relationship_health_audit(audit)

    phase = str(data.get("relationship_generation_phase") or "final")
    phase_label = "最终矩阵" if phase == "final" else "关系种子"
    parts = [
        _metric_tiles(
            _relationship_health_metrics(
                relationship_edges,
                roster=roster,
                audit=health_audit,
            )
        ),
        f'<div class="hint-block">当前产物：{_tag_html(phase_label)}'
        f" 候选证据 {_esc(str(len(evidence)))} 条"
        + (f"；种子矩阵 {_esc(str(len(seed_edges)))} 条" if seed_edges else "")
        + "</div>",
    ]
    if health_audit:
        names = "、".join(
            sorted(
                {
                    str(item.get("character_name") or "").strip()
                    for item in health_audit
                    if str(item.get("character_name") or "").strip()
                }
            )
        )
        parts.append(
            '<div class="hint-block">'
            f"<b>关系健康提示</b><br>检测到 {_esc(str(len(health_audit)))} 项关系审计问题"
            + (f"：{_esc(names)}" if names else "")
            + "。请优先检查未知名、孤立关键人物或无效关系条目。</div>"
        )
    if relationship_edges:
        parts.append(_render_relation_table(relationship_edges, "最终人物关系矩阵"))
    else:
        parts.append(
            '<div class="hint-block">当前关系矩阵为空。若角色设定已包含互动、亏欠、同盟、'
            "敌对或情感牵连，应重新生成角色关系矩阵。</div>"
        )
    if evidence:
        rows = "".join(
            "<tr>"
            f"<td><b>{_esc(str(item.get('source') or ''))}</b> → "
            f"<b>{_esc(str(item.get('target') or ''))}</b></td>"
            f"<td>{_tag_html(str(item.get('field') or ''))}</td>"
            f"<td>{_esc(_shorten_text(item.get('snippet'), 180))}</td>"
            "</tr>"
            for item in evidence[:80]
        )
        parts.append(
            "<h2>候选证据</h2>"
            "<table><thead><tr><th>端点</th><th>字段</th><th>摘录</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    parts.append(_render_audit_items(health_audit))
    return _make_browser(_html_wrap("\n".join(part for part in parts if part), "角色关系矩阵"))

