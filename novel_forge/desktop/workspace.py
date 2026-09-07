"""Desktop-oriented workspace snapshot and view-model helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from novel_forge.core.infra.async_runner import run_async_coro
from novel_forge.core.utils.text_validation import display_word_count
from novel_forge.desktop.files import ProjectFileService
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.contracts import ChapterWorkspaceSnapshot
from novel_forge.workspace.projects import ProjectDetail, ProjectInspector, WorkspaceOverview
from novel_forge.workspace.runtime import RuntimeServices, create_runtime_services

_PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "kimi": "Kimi",
    "mock": "Mock",
    "openai": "OpenAI",
    "tencent": "腾讯混元",
    "tongyi": "Tongyi",
    "tongyi_coding": "Tongyi Coding Plan",
    "tongyi_token_plan": "Tongyi Token Plan",
}

_PROVIDER_SETTING_KEYS = {
    "anthropic": "anthropic_api_key",
    "deepseek": "deepseek_api_key",
    "kimi": "kimi_api_key",
    "openai": "openai_api_key",
    "tencent": "tencent_api_key",
    "tongyi": "tongyi_api_key",
    "tongyi_coding": "tongyi_coding_api_key",
    "tongyi_token_plan": "tongyi_token_plan_api_key",
}


@dataclass(frozen=True)
class ProviderStatus:
    provider_id: str
    label: str
    ready: bool
    configured: bool
    is_default: bool
    detail: str


@dataclass(frozen=True)
class DesktopProjectItem:
    project_id: str
    title: str
    mode: str
    mode_label: str
    status: str
    status_label: str
    progress_label: str
    progress_percent: int
    last_updated_label: str
    headline: str
    next_action: str
    genre: str
    tone: str
    completed_chapters: int
    total_chapters: int | None
    next_chapter: int | None
    has_outline: bool
    has_canon: bool
    init_resume_available: bool = False
    init_resume_step_label: str = ""
    project_state: str = ""
    project_state_label: str = ""
    allowed_operations: tuple[str, ...] = ()
    headline_full: str = ""


@dataclass(frozen=True)
class DesktopWorkspaceMetrics:
    total_projects: int
    total_chapters: int
    total_words: int
    configured_providers: int


@dataclass(frozen=True)
class DesktopWorkspaceSnapshot:
    storage_root: Path
    default_provider: str
    overview: WorkspaceOverview
    metrics: DesktopWorkspaceMetrics
    providers: list[ProviderStatus]
    projects: list[DesktopProjectItem]
    featured_project: DesktopProjectItem | None
    details: dict[str, ProjectDetail]


def _format_updated_at(value: str | None) -> str:
    if not value:
        return "未记录"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _short_story_word_count(detail: ProjectDetail, layout: ProjectLayout) -> int:
    short_story_path = layout.chapters_dir / "short_story.md"
    if not short_story_path.exists():
        return 0
    try:
        text = short_story_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    return display_word_count(text)


def _project_status(detail: ProjectDetail) -> tuple[str, str, str, int, int | None]:
    if detail.mode == "short":
        has_story = detail.artifact_counts.get("chapters", 0) > 0
        if has_story:
            return "completed", "已完稿", "短篇完稿", 100, None
        return "planning", "待起稿", "等待生成正文", 0, None

    total = detail.total_chapters or 0
    completed = detail.completed_chapters
    if detail.project_state == "archived":
        return "archived", "已归档", detail.project_state_label or "已归档", 100, None
    if detail.project_state == "paused":
        progress = int((completed / total) * 100) if total else min(88, 24 + max(completed, 1) * 8)
        next_chapter = None if total and completed >= total else completed + 1
        return (
            "paused",
            "已暂停",
            f"停在第 {next_chapter or completed or 1} 章前",
            progress,
            next_chapter,
        )
    if detail.project_state == "init_failed":
        return (
            "planning",
            "立项失败",
            detail.init_resume_progress_label or "初始化失败，待重试",
            max(detail.init_resume_progress_percent, 12),
            1,
        )
    if detail.project_state == "initializing" and detail.init_resume_available:
        return (
            "planning",
            "待续立项",
            detail.init_resume_progress_label or f"待续 {detail.init_resume_step_label}",
            detail.init_resume_progress_percent,
            1,
        )
    if detail.project_state == "outline_ready":
        return (
            "planning",
            "待起章",
            "大纲与 Canon 已就绪",
            max(18, detail.init_resume_progress_percent),
            1,
        )
    if total and completed >= total:
        return "completed", "已完结", f"{completed}/{total}", 100, None
    if completed > 0:
        progress = int((completed / total) * 100) if total else min(90, 24 + completed * 8)
        return "writing", "连载中", f"{completed}/{total or '?'}", progress, completed + 1
    if detail.init_resume_available:
        return (
            "planning",
            "待续立项",
            detail.init_resume_progress_label or f"待续 {detail.init_resume_step_label}",
            detail.init_resume_progress_percent,
            1,
        )
    progress = 18 if detail.has_outline or detail.has_canon else 6
    return "planning", "筹备中", f"待起章 / 预计 {total or '?'} 章", progress, 1


_CLAUSE_BOUNDARIES = frozenset("。，、；！？\n\r")


def _truncate_at_clause(text: str, *, limit: int = 80) -> str:
    """智能截断文本：在中文标点边界优先截断，避免半句被砍。

    - 先按 ``" ".join(text.split())`` 折叠空白，再判断长度。
    - 长度 <= ``limit`` 时原样返回（保留完整语义）。
    - 超出时，从 ``limit`` 向前回退到最近的 ``_CLAUSE_BOUNDARIES`` 字符
      （含字符本身），让截断点落在句子/分句末尾；若范围内没有标点，
      回退到 ``limit - 1`` 字符级硬截断（与 ``_truncate`` 行为一致）。
    - 末尾统一追加 ``"…"``，便于视觉识别截断。
    """
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    boundary = max(
        (idx for idx, ch in enumerate(value[:limit]) if ch in _CLAUSE_BOUNDARIES),
        default=-1,
    )
    if boundary >= 0:
        cut = boundary + 1  # 包含标点
    else:
        cut = max(limit - 1, 0)
    return value[:cut].rstrip() + "…"


def _project_headline(detail: ProjectDetail) -> str:
    """生成"在库卷册"卡片上展示的简短描述。

    取 ``detail.preview`` / ``detail.premise`` / 题材+语气，按中文标点
    智能截断到 ``limit`` 字符内，避免窄卡片中被换行截断。
    """
    for candidate in (detail.preview, detail.premise, f"{detail.genre} · {detail.tone}"):
        text = candidate.strip()
        if text:
            return _truncate_at_clause(text, limit=80)
    return "素笺待笔，尚未落字。"


def _project_headline_full(detail: ProjectDetail) -> str:
    """卡片 tooltip 使用的完整描述（不截断）。"""
    for candidate in (detail.preview, detail.premise, f"{detail.genre} · {detail.tone}"):
        text = candidate.strip()
        if text:
            return text
    return "素笺待笔，尚未落字。"


def _project_next_action(detail: ProjectDetail, next_chapter: int | None, status: str) -> str:
    allowed_operations = set(detail.allowed_operations or ())
    if detail.mode == "short":
        return "查看正文与评估结果" if status == "completed" else "前往机杼页生成短篇"
    if status == "archived":
        return "查看归档项目与历史产物"
    if status == "paused":
        return f"恢复并继续第 {next_chapter or detail.completed_chapters + 1} 章"
    if status == "completed":
        return (
            "回看章节与质量报告"
            if "resume" not in allowed_operations
            else "回看章节与质量报告，或继续进入创作"
        )
    if detail.project_state == "init_failed" or "retry" in allowed_operations:
        return "检查初始化状态并重试立项"
    if detail.init_resume_available and detail.completed_chapters == 0:
        if detail.init_resume_step == "plan_outline" and detail.init_resume_next_chapter:
            return f"继续立项，从第 {detail.init_resume_next_chapter} 章大纲接着写"
        if detail.init_resume_step_label:
            return f"继续立项，接着完成{detail.init_resume_step_label}"
        return "继续立项，接着完成当前停靠步骤"
    if detail.completed_chapters == 0:
        return (
            "开始第 1 章，验证世界与角色是否站稳"
            if "start_writing" in allowed_operations
            else "先写第 1 章，验证世界与角色是否站稳"
        )
    return f"继续推进第 {next_chapter or detail.completed_chapters + 1} 章"


class DesktopWorkspaceService:
    """Build desktop-friendly snapshots from the shared runtime layer."""

    def __init__(self, runtime: RuntimeServices) -> None:
        self.runtime = runtime
        self.inspector = ProjectInspector(runtime.storage)

    @classmethod
    def from_settings(cls, *, mock: bool = False) -> DesktopWorkspaceService:
        return cls(create_runtime_services(mock=mock))

    def build_snapshot(self) -> DesktopWorkspaceSnapshot:
        overview = self.inspector.overview(self.runtime.router)
        details = self._collect_details()
        project_items = [self._build_project_item(detail) for detail in details.values()]
        featured_project = project_items[0] if project_items else None
        providers = self._build_provider_statuses()

        total_words = 0
        total_chapters = 0
        for project_id, detail in details.items():
            layout = ProjectLayout(self.runtime.storage.root / project_id)
            if detail.mode == "short":
                word_count = _short_story_word_count(detail, layout)
                total_words += word_count
                total_chapters += 1 if word_count else 0
                continue
            total_words += sum(chapter.word_count for chapter in detail.chapters)
            total_chapters += detail.completed_chapters

        metrics = DesktopWorkspaceMetrics(
            total_projects=len(project_items),
            total_chapters=total_chapters,
            total_words=total_words,
            configured_providers=len(overview.providers),
        )
        return DesktopWorkspaceSnapshot(
            storage_root=self.runtime.storage.root,
            default_provider=overview.default_provider,
            overview=overview,
            metrics=metrics,
            providers=providers,
            projects=project_items,
            featured_project=featured_project,
            details=details,
        )

    def get_project_detail(self, project_id: str) -> ProjectDetail:
        return self.inspector.get_project_detail(project_id)

    def get_chapter_workspace_snapshot(
        self,
        project_id: str,
        chapter_number: int,
        *,
        project_detail: ProjectDetail | None = None,
    ) -> ChapterWorkspaceSnapshot:
        return self.inspector.get_chapter_workspace_snapshot(
            project_id,
            chapter_number,
            project_detail=project_detail,
        )

    def delete_project(self, project_id: str) -> bool:
        """Permanently delete a project directory. Returns True on success."""
        service = ProjectFileService(self.runtime.storage.root)
        result = service.delete_project(project_id)
        if result.deleted:
            # 清理运行时内存上下文缓存，避免重建同名项目时读到旧的脏数据
            evicted = self.runtime.memory_contexts.pop(project_id, None)
            if evicted is not None:
                close_hook = getattr(evicted, "shutdown", None)
                if callable(close_hook):
                    try:
                        close_hook()
                    except Exception:  # noqa: BLE001
                        pass
        return result.deleted

    def shutdown(self) -> None:
        runtime = self.runtime
        try:
            # Keep the close-event budget tight. The registered desktop runner
            # cancels the submitted future on timeout, so this remains a
            # best-effort cleanup rather than holding the UI open. Workers and
            # HTTP clients were already cancelled/drained by JobManager and
            # the desktop thread-pool shutdown.
            run_async_coro(runtime.shutdown(), timeout=0.2)
        except Exception:
            pass

    def _collect_details(self) -> dict[str, ProjectDetail]:
        details: dict[str, ProjectDetail] = {}
        for item in self.inspector.list_projects():
            if isinstance(item, ProjectDetail):
                details[item.project_id] = item
                continue
            details[item.project_id] = self.inspector.get_project_detail(item.project_id)
        return details

    def _build_project_item(self, detail: ProjectDetail) -> DesktopProjectItem:
        (
            status,
            status_label,
            progress_label,
            progress_percent,
            next_chapter,
        ) = _project_status(detail)
        return DesktopProjectItem(
            project_id=detail.project_id,
            title=detail.title or detail.project_id,
            mode=detail.mode,
            mode_label="长篇" if detail.mode == "long" else "短篇",
            status=status,
            status_label=status_label,
            progress_label=progress_label,
            progress_percent=progress_percent,
            last_updated_label=_format_updated_at(detail.updated_at),
            headline=_project_headline(detail),
            headline_full=_project_headline_full(detail),
            next_action=_project_next_action(detail, next_chapter, status),
            genre=detail.genre,
            tone=detail.tone,
            completed_chapters=detail.completed_chapters,
            total_chapters=detail.total_chapters,
            next_chapter=next_chapter,
            has_outline=detail.has_outline,
            has_canon=detail.has_canon,
            init_resume_available=detail.init_resume_available,
            init_resume_step_label=detail.init_resume_step_label,
            project_state=detail.project_state,
            project_state_label=detail.project_state_label,
            allowed_operations=tuple(detail.allowed_operations),
        )

    def _build_provider_statuses(self) -> list[ProviderStatus]:
        statuses: list[ProviderStatus] = []
        adapters = self.runtime.router.adapters
        settings = self.runtime.settings
        loaded_provider_ids = {
            getattr(adapter, "provider_name", key.split(":", 1)[0])
            for key, adapter in adapters.items()
        }
        ordered_ids = sorted(set(_PROVIDER_LABELS) | loaded_provider_ids)
        for provider_id in ordered_ids:
            loaded = provider_id in loaded_provider_ids
            configured = False
            if provider_id == "mock":
                configured = loaded
            else:
                setting_name = _PROVIDER_SETTING_KEYS.get(provider_id)
                configured = bool(getattr(settings, setting_name, "")) if setting_name else False

            if loaded:
                detail = "当前已载入"
            elif configured:
                detail = "已配置密钥，但本次运行未载入"
            else:
                detail = "尚未配置或不可用"

            statuses.append(
                ProviderStatus(
                    provider_id=provider_id,
                    label=_PROVIDER_LABELS.get(provider_id, provider_id.title()),
                    ready=loaded,
                    configured=configured,
                    is_default=provider_id == self.runtime.router.default_provider,
                    detail=detail,
                )
            )
        return statuses
