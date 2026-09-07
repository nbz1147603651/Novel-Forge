"""Per-widget last-HTML hash short-circuit for setHtml() / setPlainText().

Design:
- 缓存存储在 widget property 上（widget._nf_last_html_hash），随 widget 销毁自动消失
- 不缓存渲染产物本身（QTextDocument 与 widget 绑定，复用会 crash）
- 超过 _MAX_LEN 的 payload 不缓存（避免内存膨胀）
"""
from __future__ import annotations

import hashlib
from typing import Final

_PROPERTY_NAME: Final[str] = "_nf_last_html_hash"
_MAX_LEN: Final[int] = 1_000_000


def should_set_html(widget, html: str) -> bool:
    """Return True if setHtml should be called (payload changed or first time).

    Args:
        widget: 目标 QTextBrowser / QTextEdit。
        html: 待设置的 HTML 字符串。

    Returns:
        True 表示需要调用 setHtml；False 表示 payload 与上次相同，可跳过。
    """
    if len(html) > _MAX_LEN:
        return True
    new_hash = hashlib.sha1(html.encode("utf-8")).hexdigest()
    old_hash = getattr(widget, _PROPERTY_NAME, None)
    if new_hash == old_hash:
        return False
    try:
        setattr(widget, _PROPERTY_NAME, new_hash)
    except (AttributeError, RuntimeError):
        pass
    return True
