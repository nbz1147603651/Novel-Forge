"""ChapterQualityPrescreen — deterministic format/stylistic gate for chapters.

Catches production collapses that can slip past semantic eval:
- 双句号 / 双顿号 / 错配标点 (e.g. 。。、，，、。！)
- 字数塌陷或膨胀（默认 4000-12000 字，可在构造时覆盖）
- 连续重复字（她她她绕开、看看看着）
- 中文引号失配（可选检查，报告但不自动修复）
- 章末空白（违反 style_profile 的 chapter_end_required 钩子原则）

命中 critical 级（即字数崩塌）应立即升级到 FULLTEXT_REWRITE；
命中 high 级中的重复标点、连续重复字可被 ``apply_format_fixes`` 静态修复，
引号失配只报告，不自动猜测补删。

用法：

>>> report = ChapterQualityPrescreen().prescreen(text, chapter_number=20)
>>> if not report.passed:
...     fixed = ChapterQualityPrescreen.apply_format_fixes(text)
...     if any(h.severity == "critical" for h in report.hits):
...         # escalate to fulltext rewrite
...         ...

该模块独立于 HumanizeScanStep 的 AI 味检测 —— 这层是纯格式与长度守门员，
不需要 LLM。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# Hit / Report dataclasses
# ---------------------------------------------------------------------------


# Severity levels mirror HumanizeScanStep._Severity so the two reports can
# be unioned by downstream consumers (e.g. QualityGate).
_HIT_SEVERITIES: Final = ("critical", "high", "medium", "low")


@dataclass(frozen=True)
class PrescreenHit:
    """A single deterministic issue found in a chapter."""

    kind: str
    severity: str  # one of _HIT_SEVERITIES
    span: tuple[int, int] | None
    quote: str
    fix_suggestion: str

    def __post_init__(self) -> None:
        if self.severity not in _HIT_SEVERITIES:
            raise ValueError(
                f"severity must be one of {_HIT_SEVERITIES}, got {self.severity!r}"
            )


@dataclass(frozen=True)
class PrescreenReport:
    """Aggregate report returned from ``ChapterQualityPrescreen.prescreen``."""

    chapter_number: int
    char_count: int
    hits: tuple[PrescreenHit, ...]
    passed: bool


# ---------------------------------------------------------------------------
# Regex catalog
# ---------------------------------------------------------------------------


# Double punctuation: any adjacent run of Chinese punctuation that can be
# collapsed safely by keeping the first mark. Closing quotes are intentionally
# excluded here: "。」" can be a valid quoted sentence, and deleting the quote
# would corrupt dialogue. Quote balance is checked separately below.
_DOUBLE_PUNCT_RE: Final = re.compile(
    r"([。，！？；：、])[。，！？；：、]+"
)


# Triple-repeated CJK character: "她她她", "看看看" — common LLM drift artifacts.
# We require ≥ 3 same chars in a row because double characters are common
# legitimate Chinese (看看, 想想, 尝尝). Non-CJK repeats are intentionally
# ignored so auto-fix never collapses paragraph whitespace, ASCII ellipses,
# Markdown dividers, or numeric identifiers.
_REPEATED_CHAR_RE: Final = re.compile(
    r"([\u4e00-\u9fff])\1{2,}"
)


# Trailing whitespace at end of chapter: violation of chapter_end_required.
_TRAILING_BLANK_RE: Final = re.compile(r"[\s\n]+$")

_QUOTE_PAIRS: Final = {
    "「": "」",
    "『": "』",
    "“": "”",
}
_CLOSING_QUOTES: Final = {close: open_ for open_, close in _QUOTE_PAIRS.items()}


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class ChapterQualityPrescreen:
    """Deterministic pre-eval gate for chapter text formatting and length."""

    DEFAULT_MIN_CHARS: Final = 4000
    DEFAULT_MAX_CHARS: Final = 12000

    def __init__(
        self,
        *,
        min_chars: int = DEFAULT_MIN_CHARS,
        max_chars: int = DEFAULT_MAX_CHARS,
        check_quote_balance: bool = False,
    ) -> None:
        if min_chars <= 0:
            raise ValueError(f"min_chars must be > 0, got {min_chars}")
        if max_chars <= min_chars:
            raise ValueError(
                f"max_chars ({max_chars}) must be > min_chars ({min_chars})"
            )
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.check_quote_balance = check_quote_balance

    # ── Main entry point ────────────────────────────────────────────────────

    def prescreen(self, chapter_text: str, *, chapter_number: int) -> PrescreenReport:
        """Run all checks. Returns a PrescreenReport.

        ``passed`` is False if any hit has severity "critical" (length floor).
        High-severity formatting hits (double punctuation, repeated chars)
        keep ``passed`` True because they can be auto-fixed in-place.
        """
        if chapter_text is None:
            raise ValueError("chapter_text must not be None")

        hits: list[PrescreenHit] = []
        char_count = len(chapter_text)

        # 1. Length floor (critical)
        if char_count < self.min_chars:
            hits.append(PrescreenHit(
                kind="length_floor",
                severity="critical",
                span=None,
                quote="",
                fix_suggestion=(
                    f"章节仅 {char_count} 字，低于 {self.min_chars} 字下限。"
                    f"建议扩展场景或合并邻章，或调低 min_chars 阈值（spec 级覆盖）。"
                ),
            ))

        # 2. Length ceiling (medium)
        if char_count > self.max_chars:
            hits.append(PrescreenHit(
                kind="length_ceiling",
                severity="medium",
                span=None,
                quote="",
                fix_suggestion=(
                    f"章节 {char_count} 字，超过 {self.max_chars} 字上限。"
                    f"建议拆分章末或裁剪次要环境描写。"
                ),
            ))

        # 3. Double punctuation (high)
        for m in _DOUBLE_PUNCT_RE.finditer(chapter_text):
            evidence = chapter_text[m.start():m.end()]
            hits.append(PrescreenHit(
                kind="double_punctuation",
                severity="high",
                span=(m.start(), m.end()),
                quote=evidence,
                fix_suggestion=f"删除重复标点「{evidence}」",
            ))

        # 4. Triple repeated character (high)
        for m in _REPEATED_CHAR_RE.finditer(chapter_text):
            evidence = chapter_text[m.start():m.end()]
            hits.append(PrescreenHit(
                kind="repeated_char",
                severity="high",
                span=(m.start(), m.end()),
                quote=evidence,
                fix_suggestion=f"修正连续重复字「{evidence}」",
            ))

        # 5. Quote balance (high, report-only). Disabled by default because
        # some prose styles use opening quotation marks as dialogue markers.
        if self.check_quote_balance:
            hits.extend(self._quote_balance_hits(chapter_text))

        # 6. Empty / blank chapter tail (medium)
        if _TRAILING_BLANK_RE.search(chapter_text) and chapter_text.rstrip() != chapter_text:
            # only flag if there's substantive content before the trailing whitespace
            stripped = chapter_text.rstrip()
            if stripped and stripped[-1] in "。！？」":
                hits.append(PrescreenHit(
                    kind="empty_tail",
                    severity="medium",
                    span=(len(stripped), len(chapter_text)),
                    quote=chapter_text[len(stripped):],
                    fix_suggestion="删除章末空白行，确保章末有钩子句。",
                ))

        passed = not any(h.severity == "critical" for h in hits)
        return PrescreenReport(
            chapter_number=chapter_number,
            char_count=char_count,
            hits=tuple(hits),
            passed=passed,
        )

    @staticmethod
    def _quote_balance_hits(chapter_text: str) -> list[PrescreenHit]:
        """Return unmatched Chinese quote-mark hits without mutating text."""
        hits: list[PrescreenHit] = []
        stack: list[tuple[str, int]] = []

        for index, char in enumerate(chapter_text):
            if char in _QUOTE_PAIRS:
                stack.append((char, index))
                continue
            if char not in _CLOSING_QUOTES:
                continue

            expected_open = _CLOSING_QUOTES[char]
            if stack and stack[-1][0] == expected_open:
                stack.pop()
                continue

            quote = chapter_text[max(0, index - 8): index + 9]
            hits.append(PrescreenHit(
                kind="unbalanced_quote",
                severity="high",
                span=(index, index + 1),
                quote=quote,
                fix_suggestion=f"右引号「{char}」没有匹配的左引号「{expected_open}」。",
            ))

        for open_char, index in stack:
            expected_close = _QUOTE_PAIRS[open_char]
            quote = chapter_text[max(0, index - 8): index + 9]
            hits.append(PrescreenHit(
                kind="unbalanced_quote",
                severity="high",
                span=(index, index + 1),
                quote=quote,
                fix_suggestion=f"左引号「{open_char}」没有匹配的右引号「{expected_close}」。",
            ))

        return hits

    # ── Static fix path ─────────────────────────────────────────────────────

    @staticmethod
    def apply_format_fixes(chapter_text: str) -> str:
        """Apply deterministic, non-LLM fixes for high-severity formatting hits.

        Operations performed (in order):
        1. Collapse double terminal punctuation (。。 → 。, ，， → ，, etc.)
        2. Collapse triple-or-more repeated characters (她她她 → 她)

        Length violations, unbalanced quotes, and empty-tail issues cannot be
        fixed here — those require structural rewrite or human review. Length
        violations are intentionally not silently truncated.
        """
        # 1. Double punctuation: keep the first occurrence.
        chapter_text = _DOUBLE_PUNCT_RE.sub(r"\1", chapter_text)

        # 2. Triple repeated character: keep a single occurrence.
        chapter_text = _REPEATED_CHAR_RE.sub(
            lambda m: m.group(1),
            chapter_text,
        )

        return chapter_text


__all__ = [
    "ChapterQualityPrescreen",
    "PrescreenHit",
    "PrescreenReport",
]
