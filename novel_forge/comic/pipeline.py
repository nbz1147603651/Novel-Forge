"""漫画管线编排：剧本投影 → 页/格规划 → 审计门禁 → 导出包。

P3 comic pipeline: consumes the Batch-2 screenplay intermediate artifacts and
the locked production bible from the film studio (both read-only), plans
pages/panels deterministically (LLM only advises pacing via
``COMIC_PANEL_LAYOUT``), audits against the hard panel budget and exports a
delivery package registered on the unified ``DeliveryManifest``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.film.schemas import DeliveryManifest, DeliveryMediaType
from novel_forge.film.store import FilmProjectStore
from novel_forge.gateway.types import ModelRequest
from novel_forge.persistence.filesystem import atomic_write_json, atomic_write_text
from novel_forge.persistence.models import ProjectLayout
from novel_forge.prompts.registry import PromptRegistry

from .layout import plan_comic_pages
from .schemas import (
    ComicFormat,
    ComicProjectState,
    audit_comic_layout,
    panel_aspect,
    panel_budget,
)
from .store import ComicProjectStore

StepCallback = Callable[[str, dict[str, Any]], None]


def _noop_step(_step: str, _payload: dict[str, Any]) -> None:
    return


def _json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}


class ComicPipeline:
    """Orchestrates screenplay projection → page/panel layout → audit → export."""

    def __init__(
        self,
        *,
        project_id: str,
        layout: ProjectLayout,
        router: Any | None = None,
        on_step: StepCallback | None = None,
    ) -> None:
        self.project_id = project_id
        self.layout = layout
        self.router = router
        self.on_step = on_step or _noop_step
        self.store = ComicProjectStore(layout)
        self.film_store = FilmProjectStore(layout)

    # ------------------------------------------------------------------ state

    def get_or_bootstrap(
        self, *, title: str = "", fmt: ComicFormat = ComicFormat.PAGE
    ) -> ComicProjectState:
        state = self.store.load()
        if state is None:
            state = ComicProjectState(project_id=self.project_id, title=title, format=fmt)
            state = self.store.save(state)
        return state

    def _film_sources(self) -> tuple[Any, Any] | None:
        """(screenplay, production_bible) from the film workbench, read-only."""
        film_state = self.film_store.load()
        if film_state is None:
            return None
        if not film_state.screenplay.scenes:
            return None
        return film_state.screenplay, film_state.production_bible

    # ------------------------------------------------------------------- plan

    async def plan_pages(self, *, fmt: ComicFormat) -> ComicProjectState:
        sources = self._film_sources()
        if sources is None:
            raise RuntimeError("缺少成稿回流剧本：请先在映界工作台完成剧本投影")
        screenplay, bible = sources
        low, high = panel_budget(fmt)
        pacing = await self._run_layout_task(screenplay, fmt)
        pages = plan_comic_pages(screenplay, bible, fmt, pacing_targets=pacing)
        for page in pages:
            if pacing and pacing.get(page.page_number):
                page.rhythm_note = f"LLM 节奏：目标 {pacing[page.page_number]} 格"
        state = ComicProjectState(
            project_id=self.project_id,
            title=screenplay.title,
            format=fmt,
            pages=pages,
            source_revision=f"screenplay_v{screenplay.version}",
        )
        state = self.store.save(state)
        self.on_step(
            "comic_layout_planned",
            {
                "format": fmt.value,
                "pages": len(pages),
                "panels": state.total_panels,
                "bubbles": state.total_bubbles,
                "budget": [low, high],
            },
        )
        return state

    async def _run_layout_task(self, screenplay: Any, fmt: ComicFormat) -> dict[int, int]:
        """LLM pacing hint (page → target panel count); {} = deterministic."""
        if self.router is None:
            return {}
        low, high = panel_budget(fmt)
        scenes = [
            {
                "scene_id": scene.scene_id,
                "heading": scene.heading,
                "dialogue_count": sum(
                    1 for line in scene.lines if (line.kind or "") == "dialogue"
                ),
                "action_count": sum(
                    1 for line in scene.lines if (line.kind or "") != "dialogue"
                ),
            }
            for scene in screenplay.scenes
        ]
        try:
            prompt = PromptRegistry().render(
                TaskType.COMIC_PANEL_LAYOUT,
                title=screenplay.title,
                format=fmt.value,
                aspect=panel_aspect(fmt),
                page_budget_low=low,
                page_budget_high=high,
                scenes=scenes,
            )
        except Exception:
            self.on_step("comic_layout_fallback", {"reason": "prompt_unavailable"})
            return {}
        request = ModelRequest(
            task_type=TaskType.COMIC_PANEL_LAYOUT,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.4,
            response_schema_name="comic_panel_layout",
        )
        try:
            response = await self.router.route(request)
            payload = _json_object(response.content)
        except RuntimeError:
            self.on_step("comic_layout_fallback", {"reason": "invalid_or_unavailable"})
            return {}
        raw_pages = payload.get("pages")
        if not isinstance(raw_pages, list):
            return {}
        targets: dict[int, int] = {}
        for item in raw_pages:
            if not isinstance(item, dict):
                continue
            raw_number = item.get("page_number")
            raw_count = item.get("panel_count")
            if raw_number is None or raw_count is None:
                continue
            try:
                number = int(raw_number)
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if number >= 1:
                targets[number] = min(max(count, low), high)
        return targets

    # ------------------------------------------------------------------ audit

    def audit(self, state: ComicProjectState) -> list[str]:
        return audit_comic_layout(state)

    # ----------------------------------------------------------------- export

    def export_package(self, state: ComicProjectState) -> dict[str, Any]:
        """Write the delivery package and register it on the unified manifest."""
        export_dir = self.store.export_dir
        export_dir.mkdir(parents=True, exist_ok=True)
        pages_path = export_dir / "comic_pages.json"
        atomic_write_json(pages_path, state.model_dump(mode="json"))
        preview_path = export_dir / "comic_preview.md"
        atomic_write_text(preview_path, _preview_markdown(state))
        manifest = DeliveryManifest(project_id=state.project_id, title=state.title).register_artifact(
            DeliveryMediaType.COMIC,
            str(pages_path),
            item_count=state.total_panels,
            note=f"{state.format.value} comic · {len(state.pages)} pages",
        )
        manifest_path = export_dir / "delivery_manifest.json"
        atomic_write_json(manifest_path, manifest.model_dump(mode="json"))
        issues = audit_comic_layout(state)
        self.on_step(
            "comic_package_exported",
            {"pages": len(state.pages), "panels": state.total_panels, "issues": len(issues)},
        )
        return {
            "export_dir": str(export_dir),
            "pages_path": str(pages_path),
            "preview_path": str(preview_path),
            "manifest_path": str(manifest_path),
            "panel_count": state.total_panels,
            "audit_issues": issues,
        }


def _preview_markdown(state: ComicProjectState) -> str:
    unit = "页" if state.format is ComicFormat.PAGE else "条"
    lines = [f"# {state.title or state.project_id} · 漫画预览", ""]
    for page in state.pages:
        lines.append(f"## 第 {page.page_number} {unit}")
        if page.rhythm_note:
            lines.append(f"> {page.rhythm_note}")
        for panel in page.panels:
            lines.append(f"### 格 {panel.panel_number}（{panel.beat}）")
            if panel.action:
                lines.append(f"- 画面：{panel.action}")
            for bubble in panel.bubbles:
                speaker = f"{bubble.speaker}：" if bubble.speaker else ""
                lines.append(f"- 气泡（{bubble.kind}）：{speaker}{bubble.text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["ComicPipeline"]
