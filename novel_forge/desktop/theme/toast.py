"""Toast notification styles.

Provides the QSS selector/token contract for the floating Toast widget,
replacing the previous inline ``self.setStyleSheet(...)`` call inside
``components/toast.py``.

The widget advertises its variant via ``objectName`` (e.g. ``"toast"``
with ``property variant="success"``) so a single QSS block can style
all four variants.  Variant accent colors map to existing design
tokens (``status.*`` and ``accent.primary``) so a future theme swap
updates the toasts automatically.  The translucent top-level shell itself is
painted by ``PaintedFloatingSurface``; QSS keeps the public theme contract and
label styling, but does not act as the compositor-visible background source.

Token reference (see ``novel_forge/desktop/tokens/colors.py``):
- success → status.success_warm   (#2f6d4c)
- error   → status.danger_alt     (#a63d32)
- warning → accent.primary        (#b65634)
- info    → text.secondary        (#66574b)

The painted shell keeps the previous inline rgba() alphas:
- background alpha 245 (~96% opaque)
- border alpha 40  (~16% opaque, hairline)
- border-left alpha 180 (~70% opaque, accent rail)
"""

from __future__ import annotations

_CONTENT_TEMPLATE = """

QWidget#toast {
    color: {{text.primary}};
}

QWidget#toast[variant="success"] {
    color: {{text.primary}};
}

QWidget#toast[variant="error"] {
    color: {{text.primary}};
}

QWidget#toast[variant="warning"] {
    color: {{text.primary}};
}

QWidget#toast[variant="info"] {
    color: {{text.primary}};
}

QWidget#toast QLabel#toastLabel {
    color: {{text.primary}};
    background: transparent;
    padding: 0px;
}

"""

CONTENT = _CONTENT_TEMPLATE
