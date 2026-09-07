"""Regression: ensure the 山风与归人2 project's chapters remain clear of
formatting collapses after the first-phase fixes.

This regression test pins down the end-state we reached on 2026-06-30:
- All high-severity default formatting hits (双句号 / 重复字) are zero.
- All Chinese quote pairs remain balanced when the explicit quote-balance
  check is enabled.
- Length-floor failures (Ch18, Ch20) remain because they require manual
  rewriting, not auto-fix.
- Critical hits are exactly the documented set: Ch18, Ch20 in 山风与归人2
  and Ch6 in 山风与归人.

If a future regeneration introduces a 双句号 / 重复字 anywhere in
these projects, this test will fail with a precise hit listing.
If a future cleanup removes closing dialogue quotes, the quote-balance
tests below will fail with the damaged chapter number.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.pipeline.steps.chapter_quality_prescreen import (
    ChapterQualityPrescreen,
    PrescreenHit,
    PrescreenReport,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SHANFENG_2 = _REPO_ROOT / "data" / "山风与归人2"
SHANFENG_1 = _REPO_ROOT / "data" / "山风与归人"


def _load_chapter(project_dir: Path, chapter_number: int) -> str:
    path = project_dir / "chapters" / f"chapter_{chapter_number:03d}.md"
    if not path.exists():
        pytest.skip(f"chapter not present: {path}")
    return path.read_text(encoding="utf-8")


def _formatting_hits(report: PrescreenReport) -> list[PrescreenHit]:
    """Filter out length and tail warnings; keep chapter-format regressions."""
    formatting = {"double_punctuation", "repeated_char"}
    return [h for h in report.hits if h.kind in formatting]


def _quote_hits(report: PrescreenReport) -> list[PrescreenHit]:
    """Return explicit quote-balance hits."""
    return [h for h in report.hits if h.kind == "unbalanced_quote"]


# ---------------------------------------------------------------------------
# 山风与归人2 — every chapter must be free of auto-fixable formatting hits
# ---------------------------------------------------------------------------


class TestShanfeng2NoFormattingHits:
    """Every chapter in 山风与归人2 must have zero default chapter-format hits
    after the 2026-06-30 cleanup."""

    @pytest.mark.parametrize("chapter_number", list(range(1, 25)))
    def test_chapter_has_no_formatting_hits(
        self, chapter_number: int
    ) -> None:
        text = _load_chapter(SHANFENG_2, chapter_number)
        report = ChapterQualityPrescreen().prescreen(
            text, chapter_number=chapter_number
        )
        offenders = _formatting_hits(report)
        assert not offenders, (
            f"Ch{chapter_number:03d} has {len(offenders)} formatting hit(s) "
            f"that should have been auto-fixed:\n"
            + "\n".join(
                f"  [{h.severity}] {h.kind}: {h.quote!r}"
                for h in offenders[:10]
            )
        )


# ---------------------------------------------------------------------------
# 山风与归人2 — quote pairs must stay balanced after the cleanup
# ---------------------------------------------------------------------------


class TestShanfeng2QuoteBalance:
    """The 2026-06-30 cleanup once removed sentence-final closing quotes.

    Keep this check explicit because the default prescreen intentionally leaves
    quote-balance opt-in for prose styles that use open-quote dialogue markers.
    Project regression data should still be balanced.
    """

    @pytest.mark.parametrize("chapter_number", list(range(1, 25)))
    def test_chapter_has_balanced_quotes(self, chapter_number: int) -> None:
        text = _load_chapter(SHANFENG_2, chapter_number)
        report = ChapterQualityPrescreen(check_quote_balance=True).prescreen(
            text, chapter_number=chapter_number
        )
        offenders = _quote_hits(report)
        assert not offenders, (
            f"Ch{chapter_number:03d} has {len(offenders)} unbalanced quote hit(s):\n"
            + "\n".join(
                f"  [{h.severity}] {h.quote!r}: {h.fix_suggestion}"
                for h in offenders[:10]
            )
        )


# ---------------------------------------------------------------------------
# 山风与归人2 — known length-floor failures must remain critical (the
# cleanup cannot rewrite chapters; that's a manual task)
# ---------------------------------------------------------------------------


class TestShanfeng2KnownLengthCollapses:
    EXPECTED_LENGTH_FLOOR_CHAPTERS = {18, 20}

    @pytest.mark.parametrize(
        "chapter_number", sorted(EXPECTED_LENGTH_FLOOR_CHAPTERS)
    )
    def test_length_floor_still_flagged(
        self, chapter_number: int
    ) -> None:
        """These chapters must continue to fail length_floor until they are
        manually rewritten. If they ever stop failing, either the threshold
        was loosened or the chapter was rewritten — both are good outcomes;
        update this parametrize set when that happens."""
        text = _load_chapter(SHANFENG_2, chapter_number)
        report = ChapterQualityPrescreen().prescreen(
            text, chapter_number=chapter_number
        )
        assert not report.passed
        kinds = {h.kind for h in report.hits}
        assert "length_floor" in kinds, (
            f"Ch{chapter_number:03d} was expected to fail length_floor; "
            f"actual kinds={sorted(kinds)}"
        )


# ---------------------------------------------------------------------------
# 山风与归人 (project 1) — same invariant for its critical chapter
# ---------------------------------------------------------------------------


class TestShanfeng1KnownLengthCollapse:
    EXPECTED_LENGTH_FLOOR_CHAPTER = 6

    def test_ch006_still_fails_length_floor(self) -> None:
        text = _load_chapter(SHANFENG_1, self.EXPECTED_LENGTH_FLOOR_CHAPTER)
        report = ChapterQualityPrescreen().prescreen(
            text, chapter_number=self.EXPECTED_LENGTH_FLOOR_CHAPTER
        )
        assert not report.passed
        assert "length_floor" in {h.kind for h in report.hits}


# ---------------------------------------------------------------------------
# 山风与归人 (project 1) — quote-balance smoke test
# ---------------------------------------------------------------------------


class TestShanfeng1QuoteBalance:
    def test_all_existing_chapters_have_balanced_quotes(self) -> None:
        if not (SHANFENG_1 / "chapters").is_dir():
            pytest.skip(f"project not present: {SHANFENG_1}")
        prescreen = ChapterQualityPrescreen(check_quote_balance=True)
        offenders: list[str] = []
        for chapter_file in sorted((SHANFENG_1 / "chapters").glob("chapter_*.md")):
            chapter_number = int(chapter_file.stem.split("_")[1])
            report = prescreen.prescreen(
                chapter_file.read_text(encoding="utf-8"),
                chapter_number=chapter_number,
            )
            hits = _quote_hits(report)
            if hits:
                offenders.append(f"{chapter_file.name}: {len(hits)}")
        assert not offenders, "Unbalanced quotes found: " + ", ".join(offenders)


# ---------------------------------------------------------------------------
# Aggregate invariant — formatting collapse rate must be ≤ 1 hit per 100 KB
# ---------------------------------------------------------------------------


class TestShanfeng2AggregateCleanRate:
    """Across all 24 chapters, the aggregate auto-fixable hit count must be
    below the regression threshold. If this fails, the cleanup needs to be
    re-run or a new collapse has slipped in."""

    MAX_TOTAL_FORMATTING_HITS = 0  # we just cleared them all

    def test_total_formatting_hits_within_threshold(self) -> None:
        if not (SHANFENG_2 / "chapters").is_dir():
            pytest.skip(f"project not present: {SHANFENG_2}")
        prescreen = ChapterQualityPrescreen()
        total = 0
        for chapter_file in sorted(
            (SHANFENG_2 / "chapters").glob("chapter_*.md")
        ):
            text = chapter_file.read_text(encoding="utf-8")
            chapter_number = int(chapter_file.stem.split("_")[1])
            report = prescreen.prescreen(text, chapter_number=chapter_number)
            total += len(_formatting_hits(report))
        assert total <= self.MAX_TOTAL_FORMATTING_HITS, (
            f"Project has {total} auto-fixable formatting hit(s); "
            f"expected ≤ {self.MAX_TOTAL_FORMATTING_HITS}. Re-run "
            f"`python scripts/post_format_check.py --project-root "
            f"data/山风与归人2 --apply-fixes --in-place`."
        )
