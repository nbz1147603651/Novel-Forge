"""Comic domain layer: shared schemas and constants.

定位：漫画管线本质上是**分镜头（storyboard）级拆解能力**，为影视（film）
模块铺路 —— ``ComicPanel``（格）镜像 ``FilmShot``（镜头），``ComicPage``
（页/条）镜像 Track；身份锁与场景上下文共享 ``ProductionBible``，图像
providers/QC 复用 film 链。后续 film 分镜能力应与本层对齐并复用。
"""

from __future__ import annotations

from .schemas import (
    PAGE_ASPECT,
    PAGE_PANEL_RANGE,
    WEBTOON_ASPECT,
    WEBTOON_PANEL_RANGE,
    ComicFormat,
    ComicPage,
    ComicPanel,
    ComicProjectState,
    SpeechBubble,
    audit_comic_layout,
)

__all__ = [
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
]
