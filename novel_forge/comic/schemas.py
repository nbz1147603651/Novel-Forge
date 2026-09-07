"""漫画（comic）domain schemas.

P3 comic pipeline: reuses the Batch-2 screenplay intermediate artifacts
(``Screenplay`` scenes / ``ScreenplayLine`` dialogue) and the locked
production bible.  Terminology mirrors the film Track/Clip model:

- Clip  = ``ComicPanel``（格）— one generated image with speech bubbles;
- Track = ``ComicPage``（页/条）— page manga page or one webtoon strip.

Hard, verifiable layout standards are encoded as constants + an audit
function instead of prose guidance:

- 页漫：每页 5-7 格，画幅 16:9；
- 条漫：每条 3-5 格，画幅 9:16；
- 对白气泡必须来自 ``ScreenplayLine``（kind 映射 speech/thought/narration）；
- 出场角色的身份锁锚点必须进入分格 prompt。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ComicFormat(str, Enum):
    PAGE = "page"  # 页漫：横版页，Clip=格，Track=页
    WEBTOON = "webtoon"  # 条漫：竖滑条，Clip=格，Track=条


# Hard layout standards (deterministic, testable).
PAGE_PANEL_RANGE = (5, 7)
WEBTOON_PANEL_RANGE = (3, 5)
PAGE_ASPECT = "16:9"
WEBTOON_ASPECT = "9:16"

BUBBLE_KIND_SPEECH = "speech"
BUBBLE_KIND_THOUGHT = "thought"
BUBBLE_KIND_NARRATION = "narration"
BUBBLE_KIND_SFX = "sfx"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SpeechBubble(BaseModel):
    """One speech bubble, always traceable to a ``ScreenplayLine``."""

    model_config = ConfigDict(extra="forbid")

    speaker: str = ""
    text: str
    kind: str = Field(
        default=BUBBLE_KIND_SPEECH,
        pattern=r"^(speech|thought|narration|sfx)$",
    )
    source_line_ref: str = ""


class ComicPanel(BaseModel):
    """Clip: one panel = one generated image plus its bubbles."""

    model_config = ConfigDict(extra="forbid")

    panel_id: str
    page_number: int = Field(ge=1)
    panel_number: int = Field(ge=1)
    scene_id: str = ""
    beat: str = ""
    action: str = ""
    character_ids: list[str] = Field(default_factory=list)
    bubbles: list[SpeechBubble] = Field(default_factory=list)
    prompt: str = ""
    negative_prompt: str = ""
    aspect: str = PAGE_ASPECT
    provider_id: str = "bailian"
    model_id: str = "wan2.7-image-pro"
    qc_status: str = "pending"
    locked: bool = False
    image_path: str = ""


class ComicPage(BaseModel):
    """Track: one page (页漫) or one vertical strip (条漫)."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    rhythm_note: str = ""
    panels: list[ComicPanel] = Field(default_factory=list)

    @property
    def panel_count(self) -> int:
        return len(self.panels)


class ComicProjectState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    project_id: str
    title: str = ""
    format: ComicFormat = ComicFormat.PAGE
    pages: list[ComicPage] = Field(default_factory=list)
    source_revision: str = ""
    updated_at: str = Field(default_factory=utc_now_iso)

    @property
    def total_panels(self) -> int:
        return sum(page.panel_count for page in self.pages)

    @property
    def total_bubbles(self) -> int:
        return sum(
            len(panel.bubbles) for page in self.pages for panel in page.panels
        )


def panel_budget(fmt: ComicFormat) -> tuple[int, int]:
    return PAGE_PANEL_RANGE if fmt is ComicFormat.PAGE else WEBTOON_PANEL_RANGE


def panel_aspect(fmt: ComicFormat) -> str:
    return PAGE_ASPECT if fmt is ComicFormat.PAGE else WEBTOON_ASPECT


def audit_comic_layout(state: ComicProjectState) -> list[str]:
    """Deterministic delivery gate: returns stable, machine-readable codes."""
    issues: list[str] = []
    low, high = panel_budget(state.format)
    seen: set[str] = set()
    for page in state.pages:
        count = page.panel_count
        if not low <= count <= high:
            issues.append(f"panel_count_out_of_range:{page.page_number}:{count}")
        for panel in page.panels:
            if panel.panel_id in seen:
                issues.append(f"duplicate_panel_id:{panel.panel_id}")
            seen.add(panel.panel_id)
            if panel.panel_number > count:
                issues.append(f"panel_number_overflow:{panel.panel_id}")
            if not panel.prompt.strip():
                issues.append(f"empty_panel_prompt:{panel.panel_id}")
            for bubble in panel.bubbles:
                if not bubble.text.strip():
                    issues.append(f"empty_bubble_text:{panel.panel_id}")
    return issues


__all__ = [
    "BUBBLE_KIND_NARRATION",
    "BUBBLE_KIND_SPEECH",
    "BUBBLE_KIND_SFX",
    "BUBBLE_KIND_THOUGHT",
    "PAGE_ASPECT",
    "PAGE_PANEL_RANGE",
    "WEBTOON_ASPECT",
    "WEBTOON_PANEL_RANGE",
    "ComicFormat",
    "ComicPage",
    "ComicPanel",
    "ComicProjectState",
    "SpeechBubble",
    "audit_comic_layout",
    "panel_aspect",
    "panel_budget",
    "utc_now_iso",
]
