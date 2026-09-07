"""Mechanical evidence indexing helpers.

This module deliberately avoids narrative/semantic judgement.  It only splits
text into paragraphs and locates exact or whitespace-normalized quotes.
"""

from __future__ import annotations

import re

from novel_forge.narrative_state.schemas import EvidenceSpan

_QUOTE_TRANSLATION_TABLE = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "「": '"',
        "」": '"',
        "『": '"',
        "』": '"',
        "‘": "'",
        "’": "'",
    }
)


def split_paragraphs(text: str) -> list[str]:
    """Split prose into paragraph-like chunks while preserving readable context."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    if parts:
        return parts
    return [line.strip() for line in normalized.splitlines() if line.strip()]


class EvidenceIndex:
    """Mechanical index over one chapter's text."""

    def __init__(self, chapter_number: int, text: str) -> None:
        self.chapter_number = chapter_number
        self.text = str(text or "")
        self.paragraphs = split_paragraphs(self.text)

    @staticmethod
    def _squash_ws(value: str) -> str:
        return re.sub(r"\s+", "", value or "")

    @staticmethod
    def _normalize_quote_marks(value: str) -> str:
        return str(value or "").translate(_QUOTE_TRANSLATION_TABLE)

    def locate_quote(self, quote: str, *, prefer_last: bool = False) -> EvidenceSpan:
        """Locate quote in chapter text without semantic interpretation."""
        clean_quote = str(quote or "").strip()
        if not clean_quote:
            return EvidenceSpan(quote="", chapter_number=self.chapter_number, found=False)

        start = self.text.rfind(clean_quote) if prefer_last else self.text.find(clean_quote)
        if start >= 0:
            return self._span_for_offsets(clean_quote, start, start + len(clean_quote), True)

        normalized_text = self._normalize_quote_marks(self.text)
        normalized_quote = self._normalize_quote_marks(clean_quote)
        if normalized_quote != clean_quote or normalized_text != self.text:
            start = (
                normalized_text.rfind(normalized_quote)
                if prefer_last
                else normalized_text.find(normalized_quote)
            )
            if start >= 0:
                return self._span_for_offsets(clean_quote, start, start + len(clean_quote), True)

        squashed_quote = self._squash_ws(clean_quote)
        if squashed_quote:
            squashed_text = self._squash_ws(self.text)
            squashed_index = squashed_text.find(squashed_quote)
            if squashed_index >= 0:
                return EvidenceSpan(
                    quote=clean_quote,
                    chapter_number=self.chapter_number,
                    found=True,
                    context=self._context_for_quote(clean_quote),
                )
            normalized_squashed_quote = self._squash_ws(normalized_quote)
            normalized_squashed_text = self._squash_ws(normalized_text)
            if (
                normalized_squashed_quote
                and normalized_squashed_quote != squashed_quote
                and normalized_squashed_text.find(normalized_squashed_quote) >= 0
            ):
                return EvidenceSpan(
                    quote=clean_quote,
                    chapter_number=self.chapter_number,
                    found=True,
                    context=self._context_for_quote(clean_quote),
                )

        return EvidenceSpan(
            quote=clean_quote,
            chapter_number=self.chapter_number,
            found=False,
            context=self._context_for_quote(clean_quote),
        )

    def _span_for_offsets(self, quote: str, start: int, end: int, found: bool) -> EvidenceSpan:
        para_index = None
        cursor = 0
        for idx, para in enumerate(self.paragraphs):
            para_start = self.text.find(para, cursor)
            if para_start < 0:
                continue
            para_end = para_start + len(para)
            if para_start <= start <= para_end:
                para_index = idx
                break
            cursor = para_end

        return EvidenceSpan(
            quote=quote,
            chapter_number=self.chapter_number,
            paragraph_index=para_index,
            start_offset=start,
            end_offset=end,
            found=found,
            context=self._context_for_offset(start),
        )

    def _context_for_offset(self, offset: int, radius: int = 120) -> str:
        if offset < 0:
            return ""
        start = max(0, offset - radius)
        end = min(len(self.text), offset + radius)
        return self.text[start:end].strip()

    def _context_for_quote(self, quote: str, radius: int = 120) -> str:
        normalized_quote = self._normalize_quote_marks(quote)
        for para in self.paragraphs:
            if quote and quote in para:
                return para[: radius * 2].strip()
            if normalized_quote and normalized_quote in self._normalize_quote_marks(para):
                return para[: radius * 2].strip()
        return ""
