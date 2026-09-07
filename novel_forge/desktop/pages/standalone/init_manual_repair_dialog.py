"""Manual repair dialog for failed long-init readiness gates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.app_service.init_manual_repair import (
    ManualInitRepairConflictError,
    ManualInitRepairError,
    load_manual_init_repair,
    save_manual_init_repair,
)
from novel_forge.desktop.components.dialogs import show_warning_message
from novel_forge.desktop.widgets import ActionButton
from novel_forge.persistence.filesystem import FileSystemStorage


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _compact(value: object, *, limit: int = 260) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


_STAGE_ARTIFACT_HINTS = {
    "blueprint_coherence": "blueprint",
    "outline_inheritance": "outline",
    "contract_coherence": "chapter_contracts",
    "claim_contract_coverage": "chapter_contracts",
    "source_artifacts": "chapter_contracts",
}
_ARTIFACT_LABELS = {
    "blueprint": "叙事蓝图",
    "outline": "章节大纲",
    "chapter_contracts": "章节契约",
}
_CHAPTER_RANGE_START_KEYS = ("chapter_start", "start_chapter", "from_chapter")
_CHAPTER_RANGE_END_KEYS = ("chapter_end", "end_chapter", "to_chapter")
_DIRECT_CHAPTER_KEYS = (
    "chapter",
    "chapter_number",
    "cognitive_chapter",
    "introduce_chapter",
    "resolve_chapter",
)
_CHAPTER_LIST_KEYS = (
    "chapters",
    "chapter_numbers",
    "foreshadow_chapters",
    "payoff_chapters",
    "setup_chapters",
)


@dataclass(frozen=True)
class ManualRepairLocation:
    """Resolved edit location for one blocking init issue."""

    pointer: str
    label: str
    confidence: str
    start: int = -1
    end: int = -1
    excerpt: str = ""


def _normalize_artifact(value: object) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "narrative_blueprint": "blueprint",
        "plans/narrative_blueprint.json": "blueprint",
        "outline.json": "outline",
        "story_outline": "outline",
        "contracts": "chapter_contracts",
        "plans/chapter_contracts.json": "chapter_contracts",
    }
    return aliases.get(raw, raw)


def _artifact_path(project_dir: Path, artifact: str) -> Path:
    if artifact == "blueprint":
        return project_dir / "plans" / "narrative_blueprint.json"
    if artifact == "chapter_contracts":
        return project_dir / "plans" / "chapter_contracts.json"
    return project_dir / "outline.json"


def _issue_artifacts(issue: dict[str, Any]) -> list[str]:
    artifacts: list[str] = []

    def add(value: object) -> None:
        artifact = _normalize_artifact(value)
        if artifact and artifact not in artifacts:
            artifacts.append(artifact)

    scopes = [scope for scope in _as_list(issue.get("repair_scope")) if isinstance(scope, dict)]
    for scope in scopes:
        add(scope.get("artifact"))
    if not artifacts:
        add(_STAGE_ARTIFACT_HINTS.get(str(issue.get("stage") or "").strip(), ""))
    return artifacts


def _readiness_issues(readiness: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        issue for issue in _as_list(readiness.get("remaining_issues")) if isinstance(issue, dict)
    ]


def _readiness_artifacts(readiness: dict[str, Any]) -> list[str]:
    artifacts: list[str] = []
    for action in _as_list(readiness.get("recovery_actions")):
        if not isinstance(action, dict) or action.get("kind") != "manual_repair":
            continue
        for artifact in _as_list(action.get("artifacts")):
            normalized = _normalize_artifact(artifact)
            if normalized and normalized not in artifacts:
                artifacts.append(normalized)
    for issue in _readiness_issues(readiness):
        for artifact in _issue_artifacts(issue):
            if artifact not in artifacts:
                artifacts.append(artifact)
    return artifacts


def _selected_manual_artifact(project_dir: Path) -> str:
    readiness = _load_json_object(project_dir / "reports" / "init_readiness.json")
    for artifact in _readiness_artifacts(readiness):
        if _artifact_path(project_dir, artifact).exists():
            return artifact
    return "outline" if (project_dir / "outline.json").exists() else ""


def _issue_id(issue: dict[str, Any]) -> str:
    for key in ("id", "issue_id", "claim_id", "candidate_id"):
        value = str(issue.get(key) or "").strip()
        if value:
            return value
    return ""


def _scope_text(issue: dict[str, Any]) -> str:
    scopes = _as_list(issue.get("repair_scope"))
    if not scopes:
        raw_scope = issue.get("scope") or issue.get("repair_target") or issue.get("location")
        return _compact(raw_scope, limit=180)

    parts: list[str] = []
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        artifact = str(scope.get("artifact") or "outline")
        chapters = ", ".join(str(ch) for ch in _as_list(scope.get("chapters")) if str(ch))
        fields = ", ".join(str(field) for field in _as_list(scope.get("fields")) if str(field))
        fragment = artifact
        if chapters:
            fragment += f" · 第 {chapters} 章"
        if fields:
            fragment += f" · 字段 {fields}"
        parts.append(fragment)
    return "；".join(parts)


def _issue_block(issue: dict[str, Any], index: int) -> str:
    title_parts = [f"[{index}]"]
    issue_key = _issue_id(issue)
    if issue_key:
        title_parts.append(issue_key)
    severity = str(issue.get("severity") or "").strip()
    if severity:
        title_parts.append(f"严重级别：{severity}")

    lines = ["  ".join(title_parts)]
    for key, label in (
        ("description", "矛盾点"),
        ("summary", "摘要"),
        ("evidence", "证据"),
        ("suggestion", "修复建议"),
        ("recommendation", "建议"),
        ("repair_hint", "修复提示"),
        ("expected", "期望状态"),
        ("actual", "当前状态"),
    ):
        value = issue.get(key)
        if value:
            lines.append(f"{label}：{_compact(value)}")
    scope = _scope_text(issue)
    if scope:
        lines.append(f"建议修改范围：{scope}")
    return "\n".join(lines)


def _issue_title(issue: dict[str, Any], index: int) -> str:
    issue_key = _issue_id(issue) or f"issue_{index}"
    severity = str(issue.get("severity") or "").strip()
    summary = str(issue.get("description") or issue.get("summary") or "").strip()
    parts = [f"[{index}] {issue_key}"]
    if severity:
        parts.append(severity)
    if summary:
        parts.append(_compact(summary, limit=76))
    return " · ".join(parts)


def _issues_for_artifact(
    readiness: dict[str, Any],
    report: dict[str, Any],
    artifact: str,
) -> list[dict[str, Any]]:
    issues = [
        issue
        for issue in _readiness_issues(readiness)
        if artifact in _issue_artifacts(issue) or not _issue_artifacts(issue)
    ]
    if not issues:
        issues = [issue for issue in _as_list(report.get("issues")) if isinstance(issue, dict)]
    return issues


def _scope_chapters(scope: dict[str, Any]) -> set[int]:
    values = _as_list(scope.get("chapters"))
    chapters: set[int] = set()
    for value in values:
        try:
            chapter = int(value)
        except (TypeError, ValueError):
            continue
        if chapter > 0:
            chapters.add(chapter)
    return chapters


def _scope_fields(scope: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for value in _as_list(scope.get("fields")):
        field = str(value or "").strip()
        if field and field not in fields:
            fields.append(field)
    return fields


def _normalize_pointer(value: object) -> str:
    pointer = str(value or "").strip()
    if not pointer:
        return ""
    if pointer.startswith("#/"):
        pointer = pointer[1:]
    if pointer.startswith("/"):
        return pointer
    if "." in pointer:
        return "/" + "/".join(part for part in pointer.split(".") if part)
    return "/" + pointer


def _scope_explicit_pointers(scope: dict[str, Any]) -> list[str]:
    pointers: list[str] = []
    for key in ("json_paths", "candidate_json_paths", "paths", "json_pointer", "json_path"):
        raw = scope.get(key)
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            pointer = _normalize_pointer(value)
            if pointer and pointer not in pointers:
                pointers.append(pointer)
    return pointers


def _json_pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _pointer_join(*parts: object) -> str:
    clean = [_json_pointer_escape(str(part)) for part in parts if str(part) != ""]
    return "/" + "/".join(clean)


def _positive_int(value: object) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _chapter_range_intersects(item: dict[str, Any], chapters: set[int]) -> bool:
    if not chapters:
        return False
    start = 0
    end = 0
    for key in _CHAPTER_RANGE_START_KEYS:
        start = _positive_int(item.get(key))
        if start:
            break
    for key in _CHAPTER_RANGE_END_KEYS:
        end = _positive_int(item.get(key))
        if end:
            break
    if start and not end:
        end = start
    if end and not start:
        start = end
    return bool(start and end and any(start <= chapter <= end for chapter in chapters))


def _item_mentions_chapter(value: Any, chapters: set[int]) -> bool:
    if not chapters:
        return False
    if isinstance(value, dict):
        if _chapter_range_intersects(value, chapters):
            return True
        for key in _DIRECT_CHAPTER_KEYS:
            if _positive_int(value.get(key)) in chapters:
                return True
        for key in _CHAPTER_LIST_KEYS:
            for item in _as_list(value.get(key)):
                if _positive_int(item) in chapters:
                    return True
        return any(_item_mentions_chapter(item, chapters) for item in value.values())
    if isinstance(value, list):
        return any(_item_mentions_chapter(item, chapters) for item in value)
    return _positive_int(value) in chapters


def _append_pointer(pointers: list[str], pointer: str) -> None:
    if pointer and pointer not in pointers:
        pointers.append(pointer)


def _derive_scope_pointers(
    artifact: str,
    payload: dict[str, Any],
    scope: dict[str, Any],
) -> list[str]:
    explicit = _scope_explicit_pointers(scope)
    if explicit:
        return explicit

    pointers: list[str] = []
    chapters = _scope_chapters(scope)
    fields = _scope_fields(scope)

    if artifact == "outline" and chapters:
        chapter_items = payload.get("chapters")
        if isinstance(chapter_items, list):
            for index, item in enumerate(chapter_items):
                if not isinstance(item, dict) or _positive_int(item.get("chapter_number")) not in chapters:
                    continue
                if fields:
                    for field in fields:
                        _append_pointer(
                            pointers,
                            _pointer_join("chapters", index, field if field in item else ""),
                        )
                else:
                    _append_pointer(pointers, _pointer_join("chapters", index))

    if artifact == "chapter_contracts" and chapters:
        contract_items = payload.get("chapter_contracts")
        if isinstance(contract_items, list):
            for index, item in enumerate(contract_items):
                if not isinstance(item, dict) or _positive_int(item.get("chapter_number")) not in chapters:
                    continue
                if fields:
                    for field in fields:
                        _append_pointer(
                            pointers,
                            _pointer_join("chapter_contracts", index, field if field in item else ""),
                        )
                else:
                    _append_pointer(pointers, _pointer_join("chapter_contracts", index))

    for field in fields:
        if field not in payload:
            continue
        value = payload.get(field)
        if isinstance(value, list) and chapters:
            matched = False
            for index, item in enumerate(value):
                if _item_mentions_chapter(item, chapters):
                    _append_pointer(pointers, _pointer_join(field, index))
                    matched = True
            if matched:
                continue
        _append_pointer(pointers, _pointer_join(field))
    return pointers


def _issue_candidate_pointers(
    artifact: str,
    payload: dict[str, Any],
    issue: dict[str, Any],
) -> list[str]:
    pointers: list[str] = []
    scopes = [scope for scope in _as_list(issue.get("repair_scope")) if isinstance(scope, dict)]
    for scope in scopes:
        scope_artifact = _normalize_artifact(scope.get("artifact"))
        if scope_artifact and scope_artifact != artifact:
            continue
        for pointer in _derive_scope_pointers(artifact, payload, scope):
            _append_pointer(pointers, pointer)
    for pointer in _scope_explicit_pointers(issue):
        _append_pointer(pointers, pointer)
    return pointers


def _json_pointer_unescape(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _pointer_segments(pointer: str) -> list[str]:
    if pointer in {"", "/"}:
        return []
    return [_json_pointer_unescape(part) for part in pointer.lstrip("/").split("/")]


def _skip_ws(text: str, pos: int, end: int) -> int:
    while pos < end and text[pos].isspace():
        pos += 1
    return pos


def _matching_bracket(text: str, start: int) -> int:
    pairs = {"{": "}", "[": "]"}
    close = pairs.get(text[start])
    if close is None:
        return start + 1
    depth = 0
    in_string = False
    escape = False
    for pos in range(start, len(text)):
        char = text[pos]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == text[start]:
            depth += 1
        elif char == close:
            depth -= 1
            if depth == 0:
                return pos + 1
    return len(text)


def _string_end(text: str, start: int) -> int:
    escape = False
    for pos in range(start + 1, len(text)):
        char = text[pos]
        if escape:
            escape = False
        elif char == "\\":
            escape = True
        elif char == '"':
            return pos + 1
    return len(text)


def _value_span(text: str, start: int, end: int) -> tuple[int, int]:
    value_start = _skip_ws(text, start, end)
    if value_start >= end:
        return value_start, value_start
    char = text[value_start]
    if char in "{[":
        return value_start, _matching_bracket(text, value_start)
    if char == '"':
        return value_start, _string_end(text, value_start)
    pos = value_start
    while pos < end and text[pos] not in ",\n]}":
        pos += 1
    return value_start, pos


def _find_key_value_span(text: str, key: str, start: int, end: int) -> tuple[int, int, int]:
    token = json.dumps(key, ensure_ascii=False)
    key_pos = text.find(token, start, end)
    if key_pos < 0:
        return -1, -1, -1
    colon = text.find(":", key_pos + len(token), end)
    if colon < 0:
        return -1, -1, -1
    value_start, value_end = _value_span(text, colon + 1, end)
    return key_pos, value_start, value_end


def _array_item_span(text: str, start: int, end: int, index: int) -> tuple[int, int]:
    array_start = text.find("[", start, end)
    if array_start < 0:
        return -1, -1
    pos = array_start + 1
    current = 0
    while pos < end:
        item_start = _skip_ws(text, pos, end)
        if item_start >= end or text[item_start] == "]":
            return -1, -1
        item_value_start, item_end = _value_span(text, item_start, end)
        del item_value_start
        if current == index:
            return item_start, item_end
        comma = text.find(",", item_end, end)
        if comma < 0:
            return -1, -1
        pos = comma + 1
        current += 1
    return -1, -1


def _line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    if start < 0:
        return -1, -1
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", max(start, end))
    if line_end < 0:
        line_end = len(text)
    return line_start, line_end


def _line_number(text: str, offset: int) -> int:
    if offset < 0:
        return 0
    return text.count("\n", 0, offset) + 1


def _excerpt(text: str, start: int, end: int, *, context_lines: int = 3) -> str:
    if start < 0:
        return "未能从当前 JSON 精确定位该范围，请按上方字段和章节提示人工搜索。"
    lines = text.splitlines()
    line_index = max(0, _line_number(text, start) - 1)
    first = max(0, line_index - context_lines)
    last = min(len(lines), line_index + context_lines + 1)
    return "\n".join(f"{idx + 1}: {lines[idx]}" for idx in range(first, last))


def _locate_json_pointer(
    text: str,
    payload: dict[str, Any],
    pointer: str,
) -> tuple[int, int]:
    value: Any = payload
    start = 0
    end = len(text)
    selected_start = -1
    selected_end = -1
    for segment in _pointer_segments(pointer):
        if isinstance(value, dict):
            key_pos, value_start, value_end = _find_key_value_span(text, segment, start, end)
            if key_pos < 0 or segment not in value:
                return -1, -1
            selected_start, selected_end = _line_bounds(text, key_pos, value_end)
            start, end = value_start, value_end
            value = value[segment]
            continue
        if isinstance(value, list):
            try:
                index = int(segment)
            except ValueError:
                return -1, -1
            if index < 0 or index >= len(value):
                return -1, -1
            item_start, item_end = _array_item_span(text, start, end, index)
            if item_start < 0:
                return -1, -1
            selected_start, selected_end = _line_bounds(text, item_start, item_end)
            start, end = item_start, item_end
            value = value[index]
            continue
        return -1, -1
    if selected_start < 0:
        selected_start, selected_end = 0, min(len(text), text.find("\n") if "\n" in text else len(text))
    return selected_start, selected_end


def _build_issue_locations(
    payload: dict[str, Any],
    text: str,
    artifact: str,
    issue: dict[str, Any],
) -> list[ManualRepairLocation]:
    pointers = _issue_candidate_pointers(artifact, payload, issue)
    locations: list[ManualRepairLocation] = []
    for pointer in pointers:
        start, end = _locate_json_pointer(text, payload, pointer)
        confidence = "exact" if start >= 0 else "weak"
        line_text = f"第 {_line_number(text, start)} 行" if start >= 0 else "未定位"
        label = f"{pointer} · {line_text}"
        locations.append(
            ManualRepairLocation(
                pointer=pointer,
                label=label,
                confidence=confidence,
                start=start,
                end=end,
                excerpt=_excerpt(text, start, end),
            )
        )
    if locations:
        return locations
    return [
        ManualRepairLocation(
            pointer="",
            label="未能自动定位 JSON 路径",
            confidence="weak",
            excerpt=_excerpt(text, -1, -1),
        )
    ]


def build_init_manual_repair_location_cards(
    project_dir: Path,
    *,
    artifact: str | None = None,
) -> list[dict[str, Any]]:
    """Return issue cards with JSON pointers and excerpts for the manual repair UI."""

    readiness = _load_json_object(project_dir / "reports" / "init_readiness.json")
    report = _load_json_object(project_dir / "reports" / "outline_inheritance.json")
    selected_artifact = artifact or _selected_manual_artifact(project_dir)
    payload = _load_json_object(_artifact_path(project_dir, selected_artifact))
    text = _json_text(payload)
    cards: list[dict[str, Any]] = []
    for index, issue in enumerate(_issues_for_artifact(readiness, report, selected_artifact), 1):
        locations = _build_issue_locations(payload, text, selected_artifact, issue)
        cards.append(
            {
                "title": _issue_title(issue, index),
                "issue": issue,
                "summary": _issue_block(issue, index),
                "locations": [
                    {
                        "pointer": location.pointer,
                        "label": location.label,
                        "confidence": location.confidence,
                        "start": location.start,
                        "end": location.end,
                        "excerpt": location.excerpt,
                    }
                    for location in locations
                ],
            }
        )
    return cards


def init_manual_repair_available(project_dir: Path) -> bool:
    """Return True when the project has enough artifacts for manual init repair."""
    try:
        return load_manual_init_repair(
            FileSystemStorage(project_dir.parent),
            project_dir.name,
        ).available
    except (ManualInitRepairError, OSError):
        return False


def build_init_manual_repair_summary(project_dir: Path, *, artifact: str | None = None) -> str:
    """Build a readable issue/suggestion summary for the manual repair dialog."""
    readiness = _load_json_object(project_dir / "reports" / "init_readiness.json")
    report = _load_json_object(project_dir / "reports" / "outline_inheritance.json")
    selected_artifact = artifact or _selected_manual_artifact(project_dir)
    issues = [
        issue
        for issue in _readiness_issues(readiness)
        if isinstance(issue, dict)
        and (
            not selected_artifact
            or selected_artifact in _issue_artifacts(issue)
            or not _issue_artifacts(issue)
        )
    ]
    if not issues:
        issues = [issue for issue in _as_list(report.get("issues")) if isinstance(issue, dict)]

    sections: list[str] = []
    summary = str(readiness.get("summary") or report.get("summary") or "").strip()
    if summary:
        sections.append(f"准入结论：{summary}")
    if selected_artifact:
        sections.append(
            "当前修复产物："
            f"{_ARTIFACT_LABELS.get(selected_artifact, selected_artifact)}"
            f"（{_artifact_path(project_dir, selected_artifact).relative_to(project_dir)}）"
        )
    verdict = str(report.get("verdict") or "").strip()
    if verdict:
        sections.append(f"继承裁判：{verdict}")

    if issues:
        sections.append(
            "\n\n".join(_issue_block(issue, idx) for idx, issue in enumerate(issues, 1))
        )
    else:
        sections.append("未能从报告中解析出结构化问题，请查看运行日志和 outline_inheritance.json。")

    repair_effectiveness = readiness.get("repair_effectiveness")
    if isinstance(repair_effectiveness, dict):
        unresolved = _as_list(repair_effectiveness.get("unresolved_issue_ids"))
        if unresolved:
            sections.append("自动修复未解决：" + ", ".join(str(item) for item in unresolved))
    return "\n\n".join(section for section in sections if section)


class InitManualRepairDialog(QDialog):
    """Show blocking init issues and let the user edit the scoped artifact."""

    saved = Signal(str)  # artifact path

    def __init__(self, project_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("appDialog")
        self._project_dir = project_dir
        self._storage = FileSystemStorage(project_dir.parent)
        self._project_id = project_dir.name
        try:
            snapshot = load_manual_init_repair(self._storage, self._project_id)
        except ManualInitRepairError:
            snapshot = None
        self._artifact = (snapshot.artifact if snapshot is not None else "") or "outline"
        self._artifact_path = _artifact_path(project_dir, self._artifact)
        self._payload = (
            snapshot.payload
            if snapshot is not None and snapshot.payload is not None
            else _load_json_object(self._artifact_path)
        )
        self._revision = snapshot.revision if snapshot is not None else ""
        self._cards = build_init_manual_repair_location_cards(project_dir, artifact=self._artifact)
        self._saved = False
        artifact_label = _ARTIFACT_LABELS.get(self._artifact, self._artifact)
        self.setWindowTitle(f"人工修复 · {artifact_label}")
        self.setModal(True)
        self.setMinimumSize(1080, 780)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel(f"人工修复{artifact_label}问题")
        title.setObjectName("dlgTitle")
        layout.addWidget(title)

        hint = QLabel(
            f"先根据矛盾点修改下方 {self._artifact_path.relative_to(project_dir)}。"
            "保存后系统会重新提交初始化收尾复审；"
            "复审通过后才会继续生成契约与规范状态。"
        )
        hint.setObjectName("dlgHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        issue_row = QHBoxLayout()
        issue_label = QLabel("问题定位")
        issue_label.setObjectName("fieldLabel")
        issue_row.addWidget(issue_label)
        self._issue_selector = QComboBox()
        self._issue_selector.setObjectName("initManualRepairIssueSelector")
        self._issue_selector.setMinimumWidth(420)
        issue_row.addWidget(self._issue_selector, 1)
        self._location_selector = QComboBox()
        self._location_selector.setObjectName("initManualRepairLocationSelector")
        self._location_selector.setMinimumWidth(320)
        issue_row.addWidget(self._location_selector, 1)
        locate_btn = ActionButton("定位到修改点", variant="secondary")
        locate_btn.clicked.connect(self._jump_to_selected_location)
        issue_row.addWidget(locate_btn)
        layout.addLayout(issue_row)

        for card in self._cards:
            self._issue_selector.addItem(str(card.get("title") or "未命名问题"))
        if not self._cards:
            self._issue_selector.addItem("未解析到结构化问题")

        self._issue_detail = QTextEdit()
        self._issue_detail.setObjectName("taskFlowErrorLogText")
        self._issue_detail.setReadOnly(True)
        self._issue_detail.setMinimumHeight(128)
        self._issue_detail.setMaximumHeight(180)
        layout.addWidget(self._issue_detail)

        self._location_hint = QLabel("")
        self._location_hint.setObjectName("dlgHint")
        self._location_hint.setWordWrap(True)
        layout.addWidget(self._location_hint)

        self._snippet = QTextEdit()
        self._snippet.setObjectName("taskFlowErrorLogText")
        self._snippet.setReadOnly(True)
        self._snippet.setMinimumHeight(116)
        self._snippet.setMaximumHeight(170)
        layout.addWidget(self._snippet)

        editor_label = QLabel(f"待修改产物：{self._artifact_path.relative_to(project_dir)}")
        editor_label.setObjectName("fieldLabel")
        layout.addWidget(editor_label)

        self._editor = QTextEdit()
        self._editor.setObjectName("workflowFieldDialogText")
        self._editor.setAcceptRichText(False)
        self._editor.setPlainText(_json_text(self._payload))
        layout.addWidget(self._editor, 1)
        self._issue_selector.currentIndexChanged.connect(self._on_issue_changed)
        self._location_selector.currentIndexChanged.connect(self._on_location_changed)
        self._on_issue_changed(0)

        row = QHBoxLayout()
        row.addStretch(1)
        save_btn = ActionButton("保存并复审", variant="primary")
        save_btn.clicked.connect(self._on_save)
        row.addWidget(save_btn)
        cancel_btn = ActionButton("取消", variant="secondary")
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(cancel_btn)
        layout.addLayout(row)

    @property
    def saved_changes(self) -> bool:
        return self._saved

    def _selected_card(self) -> dict[str, Any]:
        index = self._issue_selector.currentIndex()
        if 0 <= index < len(self._cards):
            card = self._cards[index]
            return card if isinstance(card, dict) else {}
        return {}

    def _selected_location(self) -> dict[str, Any]:
        card = self._selected_card()
        locations = [item for item in _as_list(card.get("locations")) if isinstance(item, dict)]
        index = self._location_selector.currentIndex()
        if 0 <= index < len(locations):
            return locations[index]
        return {}

    def _on_issue_changed(self, index: int) -> None:
        del index
        card = self._selected_card()
        if not card:
            self._issue_detail.setPlainText(
                build_init_manual_repair_summary(self._project_dir, artifact=self._artifact)
            )
            self._location_selector.clear()
            self._location_selector.addItem("未能自动定位 JSON 路径")
            self._location_hint.setText("当前报告没有可执行定位坐标，请按摘要人工搜索。")
            self._snippet.setPlainText("")
            return
        self._issue_detail.setPlainText(str(card.get("summary") or ""))
        self._location_selector.blockSignals(True)
        self._location_selector.clear()
        locations = [item for item in _as_list(card.get("locations")) if isinstance(item, dict)]
        for location in locations:
            self._location_selector.addItem(str(location.get("label") or "未命名位置"))
        self._location_selector.blockSignals(False)
        self._location_selector.setCurrentIndex(0 if locations else -1)
        self._on_location_changed(self._location_selector.currentIndex())

    def _on_location_changed(self, index: int) -> None:
        del index
        location = self._selected_location()
        if not location:
            self._location_hint.setText("未能自动定位 JSON 路径，请按问题摘要人工搜索。")
            self._snippet.setPlainText("")
            self._editor.setExtraSelections([])
            return
        confidence = str(location.get("confidence") or "weak")
        confidence_text = "精准定位" if confidence == "exact" else "候选定位"
        pointer = str(location.get("pointer") or "")
        self._location_hint.setText(f"{confidence_text}：{pointer or '无 JSON 路径'}")
        self._snippet.setPlainText(str(location.get("excerpt") or ""))

    def _current_editor_payload(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._editor.toPlainText())
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _current_location_span(self, location: dict[str, Any]) -> tuple[int, int]:
        pointer = str(location.get("pointer") or "")
        if pointer:
            payload = self._current_editor_payload()
            if payload is not None:
                start, end = _locate_json_pointer(self._editor.toPlainText(), payload, pointer)
                if start >= 0:
                    return start, end
        return int(location.get("start") or -1), int(location.get("end") or -1)

    def _jump_to_selected_location(self) -> None:
        location = self._selected_location()
        if not location:
            show_warning_message(self, "无法定位", "当前问题没有可执行的定位坐标。")
            return
        start, end = self._current_location_span(location)
        if start < 0:
            show_warning_message(
                self,
                "无法精准定位",
                "未能在当前 JSON 中找到该路径，请按上方章节和字段提示人工搜索。",
            )
            return
        cursor = self._editor.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(max(start, end), QTextCursor.MoveMode.KeepAnchor)
        highlight = QTextCharFormat()
        highlight.setBackground(QColor("#fff2b8"))
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = highlight
        self._editor.setExtraSelections([selection])
        self._editor.setTextCursor(cursor)
        self._editor.ensureCursorVisible()
        text = self._editor.toPlainText()
        self._snippet.setPlainText(_excerpt(text, start, end))

    def _on_save(self) -> None:
        try:
            payload = json.loads(self._editor.toPlainText())
        except json.JSONDecodeError as exc:
            show_warning_message(self, "JSON 格式错误", f"请先修正 JSON：{exc}")
            return
        if not isinstance(payload, dict):
            show_warning_message(self, "无法保存", "待修复产物顶层必须是 JSON 对象。")
            return
        try:
            saved = save_manual_init_repair(
                self._storage,
                self._project_id,
                artifact=self._artifact,
                payload=payload,
                expected_revision=self._revision,
            )
        except ManualInitRepairConflictError as exc:
            show_warning_message(self, "产物已更新", str(exc))
            return
        except ManualInitRepairError as exc:
            show_warning_message(self, "产物结构未通过校验", str(exc))
            return
        except OSError as exc:
            show_warning_message(self, "保存失败", str(exc))
            return
        self._revision = saved.revision
        self._saved = True
        self.saved.emit(str(self._artifact_path))
        self.accept()
