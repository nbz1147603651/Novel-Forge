"""Provider-neutral text normalization with no pipeline import dependency."""

from __future__ import annotations

import re


def normalize_speakable_text(text: str) -> str:
    """Remove tags and non-speakable symbols for content/duration checks."""

    return re.sub(
        r"<[^>]+>|\([^)]{1,40}\)|[^\w\u3400-\u9fff]+",
        "",
        text,
        flags=re.UNICODE,
    )
