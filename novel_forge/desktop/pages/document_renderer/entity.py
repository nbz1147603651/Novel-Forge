"""Sub-module of novel_forge.desktop.pages.document_renderer.

Auto-generated in the M3.2 split. Contains entity.py renderers.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import (
    QTextBrowser,
    QWidget,
)

# Late imports — placed after stdlib imports to avoid circular imports
# between sibling sub-modules.
from novel_forge.desktop.pages.document_renderer.character_graph import (
    _role_label_for_display,
    _status_label_for_display,
)
from novel_forge.desktop.pages.document_renderer.relationship_matrix import (
    _as_dict_list,
    _metric_tiles,
    _relationship_health_audit,
    _relationship_health_metrics,
    _render_audit_items,
    _render_relation_table,
    _shorten_text,
    _tag_html,
    _unique_entities,
)
from novel_forge.desktop.pages.document_renderer.story_artifacts._common import (
    _format_age_display,
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
from novel_forge.desktop.pages.standalone.renderer_html import (
    nl2br as _nl2br,
)

JsonDict = dict[str, Any]
FeedbackMarker = tuple[float, float, float, JsonDict, JsonDict]
WeaveLine = tuple[float, float, float, JsonDict, JsonDict]
DocumentRenderer = Callable[[dict[str, Any]], QWidget]

# Constants extracted from the original monolithic document_renderers.py.
_TIME_LAYER_LABELS: dict[str, str] = {
    "default": "默认",
    "modern": "今生",
    "past": "前世",
    "memory": "记忆层",
    "memory_only": "记忆层",
    "cross_temporal": "跨时层",
}

_ENTITY_TYPE_LABELS: dict[str, str] = {
    "character": "角色",
    "location": "地点",
    "item": "物件",
    "organization": "组织",
    "concept": "概念",
    "world_rule": "规则",
    "other": "其他",
}


def render_character_system(data: JsonDict) -> QTextBrowser:
    """Render init-time character system as readable dossiers and relation tables."""
    profiles = _as_dict_list(data.get("profiles"))
    roster = _as_dict_list(data.get("roster"))
    relationship_edges = _as_dict_list(data.get("relationship_edges"))
    identity_links = _as_dict_list(data.get("identity_links"))
    audit = _as_dict_list(data.get("audit"))

    def _profile_priority(profile: JsonDict) -> int:
        priority = next(
            (r.get("priority", 0) for r in roster if r.get("name") == profile.get("name")),
            0,
        )
        try:
            return int(priority or 0)
        except (TypeError, ValueError):
            return 0

    role_counts: dict[str, int] = {}
    for profile in profiles:
        role = str(profile.get("role") or "supporting")
        role_label = _role_label_for_display(role, profile.get("gender", ""))
        role_counts[role_label] = role_counts.get(role_label, 0) + 1
    role_summary = (
        "、".join(
            f"{role_label} {count}"
            for role_label, count in sorted(role_counts.items(), key=lambda item: item[0])
        )
        or "暂无角色"
    )

    health_audit = _relationship_health_audit(audit)
    parts = [
        _metric_tiles(
            [
                ("角色档案", len(profiles), role_summary),
                ("名册条目", len(roster), "按优先级与时层索引"),
                ("身份链接", len(identity_links), "误认、转世、别名等指代"),
                *_relationship_health_metrics(
                    relationship_edges,
                    roster=roster,
                    audit=health_audit,
                ),
            ]
        )
    ]
    if health_audit:
        parts.append(
            '<div class="hint-block"><b>关系健康提示</b><br>'
            f"检测到 {_esc(str(len(health_audit)))} 项关系审计问题；"
            "请检查未知人物名、孤立关键人物或无效关系条目。</div>"
        )

    if profiles:
        parts.append("<h2>角色档案</h2>")
        for profile in sorted(
            profiles,
            key=lambda item: (-_profile_priority(item), str(item.get("name") or "")),
        ):
            name = str(profile.get("name") or "未命名")
            role = str(profile.get("role") or "supporting")
            role_label = _role_label_for_display(role, profile.get("gender", ""))
            time_layer = str(profile.get("time_layer") or "default")
            time_label = _TIME_LAYER_LABELS.get(time_layer, time_layer)
            age = _format_age_display(profile.get("age"))
            gender = str(profile.get("gender") or "").strip()
            status = str(profile.get("status") or "").strip()
            social_status = str(profile.get("social_status") or "").strip()
            abilities = str(profile.get("abilities") or "").strip()
            chips = [_tag_html(role_label), _tag_html(time_label, muted=True)]
            if age:
                chips.append(_tag_html(age, muted=True))
            if gender:
                chips.append(_tag_html(gender, muted=True))
            status_label = _status_label_for_display(status)
            if status_label:
                chips.append(_tag_html(status_label, muted=True))

            rels = profile.get("relationships")
            relation_html = ""
            if isinstance(rels, dict) and rels:
                rel_rows = "".join(
                    "<tr>"
                    f"<td>{_esc(str(target))}</td>"
                    f"<td>{_esc(_shorten_text(desc, 140))}</td>"
                    "</tr>"
                    for target, desc in rels.items()
                )
                relation_html = (
                    '<div class="kv-row"><span class="kv-label">关系：</span>'
                    f"<table><tbody>{rel_rows}</tbody></table></div>"
                )

            parts.append(
                '<div class="section">'
                f"<h3>{_esc(name)} {' '.join(chips)}</h3>"
                + (
                    f'<div class="kv-row"><span class="kv-label">身份：</span>'
                    f'<span class="kv-value">{_esc(_shorten_text(social_status, 180))}</span></div>'
                    if social_status
                    else ""
                )
                + (
                    f'<div class="kv-row"><span class="kv-label">能力：</span>'
                    f'<span class="kv-value">{_esc(_shorten_text(abilities, 180))}</span></div>'
                    if abilities
                    else ""
                )
                + f'<div class="kv-row"><span class="kv-label">外貌：</span>'
                f'<span class="kv-value">{_esc(_shorten_text(profile.get("appearance"), 260))}</span></div>'
                f'<div class="kv-row"><span class="kv-label">性格：</span>'
                f'<span class="kv-value">{_esc(_shorten_text(profile.get("personality"), 260))}</span></div>'
                f'<div class="kv-row"><span class="kv-label">背景：</span>'
                f'<span class="kv-value">{_esc(_shorten_text(profile.get("backstory"), 260))}</span></div>'
                f'<div class="kv-row"><span class="kv-label">弧光：</span>'
                f'<span class="kv-value">{_esc(_shorten_text(profile.get("arc"), 260))}</span></div>'
                + relation_html
                + (
                    f'<div class="hint-block">{_nl2br(str(profile.get("notes") or ""))}</div>'
                    if str(profile.get("notes") or "").strip()
                    else ""
                )
                + "</div>"
            )

    parts.append(_render_relation_table(relationship_edges, "人物关系边"))
    parts.append(_render_relation_table(identity_links, "身份与指代链接"))
    parts.append(_render_audit_items(audit))
    return _make_browser(_html_wrap("\n".join(part for part in parts if part), "角色系统"))


def _render_entity_document(data: JsonDict, title: str, *, include_links: bool) -> QTextBrowser:
    raw_entities = _as_dict_list(data.get("entities"))
    entities, duplicates = _unique_entities(raw_entities)
    links = _as_dict_list(data.get("entity_links"))
    audit = _as_dict_list(data.get("audit"))

    type_counts: dict[str, int] = {}
    alias_total = 0
    for entity in entities:
        entity_type = str(entity.get("entity_type") or "other")
        type_counts[entity_type] = type_counts.get(entity_type, 0) + 1
        aliases = entity.get("aliases")
        if isinstance(aliases, list):
            alias_total += len(aliases)
    type_summary = (
        "、".join(
            f"{_ENTITY_TYPE_LABELS.get(kind, kind)} {count}"
            for kind, count in sorted(type_counts.items(), key=lambda item: item[0])
        )
        or "暂无实体"
    )

    if include_links:
        metric_items = [
            ("节点", len(entities), type_summary),
            ("关系边", len(links), "实体间身份/语义关系"),
            ("别名", alias_total, "用于指代归一"),
            ("审计", len(audit), "图谱构建提示"),
        ]
    else:
        source_count = len(
            {str(entity.get("source") or "").strip() for entity in entities if entity.get("source")}
        )
        metric_items = [
            ("实体", len(entities), type_summary),
            ("别名", alias_total, "用于指代归一"),
            ("来源", source_count, "实体抽取/派生来源"),
            ("去重", duplicates, "同 ID 重复条目已折叠"),
        ]

    parts = [_metric_tiles(metric_items)]

    if duplicates:
        parts.append(
            f'<div class="hint-block">检测到 {duplicates} 条重复实体，已在本视图中按 '
            "entity_id / 类型+名称折叠显示，源文件保持不变。</div>"
        )

    if include_links:
        if links:
            parts.append(_render_relation_table(links, "实体关系边"))
        else:
            parts.append(
                '<div class="hint-block">当前实体图谱已生成实体节点，但尚无身份/语义关系边；'
                "实体清单请查看「实体注册表」。</div>"
            )

    if entities:
        rows: list[str] = []
        for entity in sorted(
            entities,
            key=lambda item: (
                str(item.get("entity_type") or ""),
                str(item.get("name") or ""),
            ),
        ):
            entity_type = str(entity.get("entity_type") or "other")
            aliases = entity.get("aliases")
            alias_html = ""
            if isinstance(aliases, list) and aliases:
                alias_html = " ".join(_tag_html(alias, muted=True) for alias in aliases[:8])
            else:
                alias_html = '<span style="color:[[nf:text.disabled]];">（无）</span>'
            rows.append(
                "<tr>"
                f"<td><b>{_esc(str(entity.get('name') or '未命名'))}</b><br>"
                f'<span style="color:[[nf:text.muted]];font-size: 11pt;">{_esc(str(entity.get("entity_id") or ""))}</span></td>'
                f"<td>{_tag_html(_ENTITY_TYPE_LABELS.get(entity_type, entity_type))}</td>"
                f"<td>{alias_html}</td>"
                f"<td>{_esc(str(entity.get('source') or ''))}</td>"
                f"<td>{_esc(_shorten_text(entity.get('notes'), 240))}</td>"
                "</tr>"
            )
        section_title = "图谱节点" if include_links else "实体索引"
        parts.append(
            f"<h2>{section_title}</h2>"
            "<table><thead><tr><th>实体</th><th>类型</th><th>别名</th>"
            "<th>来源</th><th>说明</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )

    if include_links:
        parts.append(_render_audit_items(audit))

    return _make_browser(_html_wrap("\n".join(part for part in parts if part), title))


def render_entity_graph(data: JsonDict) -> QTextBrowser:
    """Render entity graph with deduplicated entities and explicit links."""
    return _render_entity_document(data, "实体图谱", include_links=True)


def render_entity_registry(data: JsonDict) -> QTextBrowser:
    """Render entity registry as a compact searchable-style index."""
    return _render_entity_document(data, "实体注册表", include_links=False)

