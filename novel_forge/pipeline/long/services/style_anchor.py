"""P1-4: Style anchor service — prevents style drift across chapters.

Extracts representative style sample paragraphs from early high-scoring chapters
and injects them as few-shot anchors into the DRAFT generation context.

This is NOT an AI-flavor constraint — it's a style consistency mechanism.
The style anchors preserve the author's voice established in early chapters
and prevent the gradual style erosion observed (Style: 10.0 → 7.5 over 9 chapters).

Design:
- Extract 2-3 representative paragraphs from the benchmark chapter (default: Ch1)
- Paragraphs are selected by: sensory density, dialogue integration, sentence variety
- Anchors are injected into GenerateContext.style_anchors for DRAFT prompt
- Anchors are read-only references, never modified by downstream stages
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from novel_forge.obs.logger import get_logger

_logger = get_logger("pipeline.style_anchor")

# Maximum number of anchor paragraphs to extract
_MAX_ANCHORS = 3
# Minimum paragraph length to be considered as anchor candidate
_MIN_PARAGRAPH_LENGTH = 80
# Maximum paragraph length (avoid overly long blocks)
_MAX_PARAGRAPH_LENGTH = 400


@dataclass(frozen=True)
class StyleAnchor:
    """A single style anchor paragraph with metadata."""

    text: str
    """The anchor paragraph text."""

    chapter_number: int
    """Source chapter number."""

    paragraph_index: int
    """Paragraph index within the source chapter."""

    sensory_score: float = 0.0
    """Sensory density score (0-1)."""

    dialogue_ratio: float = 0.0
    """Dialogue character ratio within the paragraph."""


@dataclass(frozen=True)
class StyleAnchorSet:
    """Collection of style anchors for a project."""

    anchors: list[StyleAnchor] = field(default_factory=list)
    """Extracted style anchor paragraphs."""

    source_chapter: int = 1
    """Chapter number the anchors were extracted from."""

    @property
    def is_empty(self) -> bool:
        return len(self.anchors) == 0

    def to_prompt_context(self) -> list[dict[str, Any]]:
        """Format anchors for injection into DRAFT prompt context."""
        return [
            {
                "text": anchor.text,
                "source_chapter": anchor.chapter_number,
                "sensory_score": round(anchor.sensory_score, 2),
            }
            for anchor in self.anchors
        ]


# ── Sensory detection patterns ─────────────────────────────────────────────────

_SENSORY_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Olfactory
    re.compile(r"气味|味道|闻|嗅|香|臭|腥|膻|芬芳|馨|霉|腐"),
    # Tactile
    re.compile(r"触|摸|蹭|滑|粗糙|温|凉|冰|烫|柔软|硬|湿|黏"),
    # Visual
    re.compile(r"光|影|色|暗|亮|闪|映|透|朦胧|昏|明"),
    # Auditory
    re.compile(r"声|响|鸣|嗡|咔|嗒|哗|嘶|低语|呢喃|嘈杂|寂静"),
    # Gustatory
    re.compile(r"甜|苦|酸|辣|咸|淡|涩|鲜|腻"),
)

_DIALOGUE_RE = re.compile(r"[「""][^」""]*[」""]")


def _compute_sensory_score(paragraph: str) -> float:
    """Compute sensory density score for a paragraph (0-1)."""
    if not paragraph:
        return 0.0
    char_count = max(len(paragraph), 1)
    sensory_hits = 0
    for pattern in _SENSORY_PATTERNS:
        sensory_hits += len(pattern.findall(paragraph))
    # Normalize: ~5 sensory words per 100 chars = high density
    return min(1.0, sensory_hits / (char_count / 100.0 * 5.0))


def _compute_dialogue_ratio(paragraph: str) -> float:
    """Compute dialogue character ratio within a paragraph."""
    dialogue_chars = sum(len(m.group()) for m in _DIALOGUE_RE.finditer(paragraph))
    return dialogue_chars / max(len(paragraph), 1)


def _compute_sentence_variety(paragraph: str) -> float:
    """Compute sentence length variety (higher = better pacing)."""
    sentences = re.split(r"[。！？]+", paragraph)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 2]
    if len(sentences) < 3:
        return 0.0
    lengths = [len(s) for s in sentences]
    mean = sum(lengths) / len(lengths)
    if mean == 0:
        return 0.0
    variance = sum((x - mean) ** 2 for x in lengths) / len(lengths)
    # Normalize: variance > 200 is high variety
    return min(1.0, variance / 200.0)


# ── Main extraction logic ──────────────────────────────────────────────────────


def extract_style_anchors(
    chapter_text: str,
    chapter_number: int = 1,
    *,
    max_anchors: int = _MAX_ANCHORS,
    min_length: int = _MIN_PARAGRAPH_LENGTH,
    max_length: int = _MAX_PARAGRAPH_LENGTH,
) -> StyleAnchorSet:
    """Extract representative style anchor paragraphs from a chapter.

    Selection criteria (weighted):
    - Sensory density (40%): rich sensory language
    - Sentence variety (30%): varied sentence lengths
    - Dialogue integration (30%): natural dialogue-narration blend

    Args:
        chapter_text: Full chapter text to extract from.
        chapter_number: Source chapter number for metadata.
        max_anchors: Maximum number of anchors to extract.
        min_length: Minimum paragraph length to consider.
        max_length: Maximum paragraph length to consider.

    Returns:
        StyleAnchorSet with the best-scoring paragraphs.
    """
    paragraphs = chapter_text.split("\n\n")
    candidates: list[tuple[float, int, str]] = []

    for idx, para in enumerate(paragraphs):
        para = para.strip()
        if len(para) < min_length or len(para) > max_length:
            continue
        # Skip pure dialogue or pure narration extremes
        dialogue_ratio = _compute_dialogue_ratio(para)
        sensory_score = _compute_sensory_score(para)
        variety_score = _compute_sentence_variety(para)

        # Weighted composite score
        composite = (
            sensory_score * 0.4
            + variety_score * 0.3
            + min(dialogue_ratio * 2, 1.0) * 0.3  # Reward some dialogue
        )

        # Penalize paragraphs that are too uniform
        if dialogue_ratio > 0.9 or dialogue_ratio < 0.05:
            composite *= 0.7

        candidates.append((composite, idx, para))

    # Sort by composite score descending, take top N
    candidates.sort(key=lambda x: x[0], reverse=True)
    selected = candidates[:max_anchors]

    anchors = [
        StyleAnchor(
            text=para,
            chapter_number=chapter_number,
            paragraph_index=idx,
            sensory_score=_compute_sensory_score(para),
            dialogue_ratio=_compute_dialogue_ratio(para),
        )
        for _score, idx, para in selected
    ]

    if anchors:
        _logger.info(
            "style_anchor_extracted | chapter=%d | anchors=%d | top_sensory=%.2f",
            chapter_number,
            len(anchors),
            anchors[0].sensory_score if anchors else 0.0,
        )

    return StyleAnchorSet(anchors=anchors, source_chapter=chapter_number)


def load_style_anchors_for_chapter(
    storage: Any,
    layout: Any,
    current_chapter: int,
    *,
    benchmark_chapter: int = 1,
    max_anchors: int = _MAX_ANCHORS,
) -> StyleAnchorSet:
    """Load style anchors from the benchmark chapter for use in current chapter.

    Best-effort: returns empty StyleAnchorSet on any failure.

    Args:
        storage: Storage backend with read_text/exists methods.
        layout: Project layout with chapter_path method.
        current_chapter: Current chapter being generated (skip if == benchmark).
        benchmark_chapter: Chapter to extract anchors from (default: 1).
        max_anchors: Maximum anchors to extract.

    Returns:
        StyleAnchorSet with anchors from the benchmark chapter.
    """
    if current_chapter <= benchmark_chapter:
        return StyleAnchorSet(source_chapter=benchmark_chapter)

    try:
        chapter_path_fn = getattr(layout, "chapter_path", None)
        if not callable(chapter_path_fn):
            return StyleAnchorSet(source_chapter=benchmark_chapter)

        path = chapter_path_fn(benchmark_chapter)
        if not storage.exists(path):
            _logger.debug(
                "style_anchor_skip | benchmark_chapter=%d not archived yet",
                benchmark_chapter,
            )
            return StyleAnchorSet(source_chapter=benchmark_chapter)

        text = storage.read_text(path)
        if not isinstance(text, str) or len(text) < 500:
            return StyleAnchorSet(source_chapter=benchmark_chapter)

        return extract_style_anchors(
            text, benchmark_chapter, max_anchors=max_anchors
        )
    except Exception as exc:
        _logger.debug("style_anchor_load_failed | error=%s", exc)
        return StyleAnchorSet(source_chapter=benchmark_chapter)
