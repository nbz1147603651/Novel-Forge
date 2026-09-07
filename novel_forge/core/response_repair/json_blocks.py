"""Targeted JSON-block repairs used by the response parser.

The broad parser in :mod:`novel_forge.core.utils.json` owns extraction,
truncation repair, and legacy compatibility.  This module keeps newer
structure-level repairs small and testable so the parser does not become one
large pile of ad-hoc rules.
"""

from __future__ import annotations


def repair_object_key_only_members(text: str) -> str:
    """Turn key-only object members into boolean flags.

    Some LLMs emit an object member as a standalone quoted string after valid
    key/value pairs, for example ``{"note":"x","not_public"}``.  That shape is
    invalid JSON, but the intended fact is recoverable without inventing new
    prose: preserve the string as a key and mark it true.

    The repair is intentionally conservative.  It only fires inside objects
    that already contain at least one normal ``key: value`` member, so
    object-as-array mistakes such as ``{"a","b"}`` remain available for the
    dedicated object-as-array repair path.
    """
    if not text or '"' not in text or "{" not in text:
        return text

    result: list[str] = []
    stack: list[dict[str, object]] = []
    in_string = False
    escape_next = False
    string_role = ""
    changed = False

    def current() -> dict[str, object] | None:
        return stack[-1] if stack else None

    def mark_parent_value() -> None:
        parent = current()
        if parent is None:
            return
        if parent["type"] == "object" and parent["state"] == "expect_value":
            parent["state"] = "after_value"
            return
        if parent["type"] == "array" and parent["state"] == "expect_value":
            parent["state"] = "after_value"

    def close_key_only_member(ctx: dict[str, object]) -> bool:
        nonlocal changed
        if ctx["type"] != "object" or ctx["state"] != "expect_colon":
            return False
        if not bool(ctx.get("has_key_value")):
            return False
        result.append(":true")
        ctx["state"] = "after_value"
        changed = True
        return True

    for ch in text:
        if in_string:
            result.append(ch)
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"':
                in_string = False
                ctx = current()
                if string_role == "object_key" and ctx is not None and ctx["type"] == "object":
                    ctx["state"] = "expect_colon"
                elif ctx is not None:
                    if ctx["type"] == "object" and ctx["state"] == "expect_value":
                        ctx["state"] = "after_value"
                    elif ctx["type"] == "array" and ctx["state"] == "expect_value":
                        ctx["state"] = "after_value"
                string_role = ""
            continue

        ctx = current()
        if ctx is not None and ctx["type"] == "object" and ctx["state"] == "expect_colon":
            if ch in " \t\r\n":
                result.append(ch)
                continue
            if ch == ":":
                result.append(ch)
                ctx["state"] = "expect_value"
                ctx["has_key_value"] = True
                continue
            if ch in ",}":
                close_key_only_member(ctx)
            else:
                result.append(ch)
                continue

        if ch in " \t\r\n":
            result.append(ch)
            continue

        if ch == '"':
            ctx = current()
            if ctx is not None and ctx["type"] == "object" and ctx["state"] == "expect_key":
                string_role = "object_key"
            else:
                string_role = "value"
            in_string = True
            escape_next = False
            result.append(ch)
            continue

        if ch == "{":
            result.append(ch)
            stack.append({"type": "object", "state": "expect_key", "has_key_value": False})
            continue

        if ch == "[":
            result.append(ch)
            stack.append({"type": "array", "state": "expect_value"})
            continue

        if ch == ",":
            result.append(ch)
            ctx = current()
            if ctx is not None:
                if ctx["type"] == "object":
                    ctx["state"] = "expect_key"
                elif ctx["type"] == "array":
                    ctx["state"] = "expect_value"
            continue

        if ch == "}":
            ctx = current()
            if ctx is not None and ctx["type"] == "object":
                close_key_only_member(ctx)
                stack.pop()
                result.append(ch)
                mark_parent_value()
                continue
            result.append(ch)
            continue

        if ch == "]":
            if stack and stack[-1]["type"] == "array":
                stack.pop()
                result.append(ch)
                mark_parent_value()
                continue
            result.append(ch)
            continue

        result.append(ch)
        ctx = current()
        if ctx is not None:
            if ctx["type"] == "object" and ctx["state"] == "expect_value":
                ctx["state"] = "after_value"
            elif ctx["type"] == "array" and ctx["state"] == "expect_value":
                ctx["state"] = "after_value"

    return "".join(result) if changed else text


def repair_object_separators(text: str) -> str:
    """Insert missing ``,`` after object closes that are followed by another
    JSON structural opener inside an array.

    Chinese LLMs frequently emit shapes like ``[..., {...}, {...]`` or
    ``[..., {...}]`` when the LLM forgets the comma between two array
    elements that happen to be objects. ``json.loads`` reports
    ``Expecting ',' delimiter`` at the position of the next opener, but the
    intent is recoverable: the missing comma goes between the previous
    ``}`` and the following ``{`` / ``]``.
    """
    if not text or "{" not in text or "}" not in text:
        return text

    result: list[str] = []
    stack: list[str] = []
    in_string = False
    escape_next = False
    pending_object_close = False
    whitespace_buffer = ""
    changed = False

    def flush_whitespace() -> None:
        nonlocal whitespace_buffer
        if whitespace_buffer:
            result.append(whitespace_buffer)
            whitespace_buffer = ""

    for ch in text:
        if in_string:
            result.append(ch)
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"':
                in_string = False
            continue

        if ch == '"':
            result.append(ch)
            in_string = True
            continue

        if ch in " \t\r\n":
            if pending_object_close:
                whitespace_buffer += ch
            else:
                result.append(ch)
            continue

        if ch == "{":
            flush_whitespace()
            stack.append("{")
            result.append(ch)
            continue
        if ch == "[":
            flush_whitespace()
            stack.append("[")
            result.append(ch)
            continue

        if ch == "}":
            in_array = bool(stack) and stack[-1] == "["
            if in_array and pending_object_close:
                flush_whitespace()
                stack.pop()
                result.append("}")
                pending_object_close = False
                continue
            if stack and stack[-1] == "{":
                stack.pop()
            result.append(ch)
            pending_object_close = in_array
            continue

        if ch == "]":
            in_array = bool(stack) and stack[-1] == "["
            if in_array and pending_object_close:
                result.append(",")
                changed = True
                pending_object_close = False
            flush_whitespace()
            if stack and stack[-1] == "[":
                stack.pop()
            result.append(ch)
            continue

        if pending_object_close:
            flush_whitespace()
            pending_object_close = False
        result.append(ch)

    return "".join(result) if changed else text


def repair_array_item_separators(text: str) -> str:
    """Repair common separator damage between adjacent array items.

    A frequent long-output failure from Chinese LLMs is an array of strings
    where two items are emitted as::

        ["上一条"
         "下一条"]

    Another shape from long minified outputs is quoting the next object opener
    by accident::

        [{"a": 1},"{"a": 2}]

    The JSON decoder reports ``Expecting ',' delimiter`` even though the shape
    is otherwise recoverable.  This scanner only acts while the active container
    is an array, so object keys and normal string values are left untouched.
    """
    if not text or '"' not in text or "[" not in text:
        return text

    result: list[str] = []
    stack: list[str] = []
    in_string = False
    escape_next = False
    pending_array_value = False
    whitespace_after_value = ""
    changed = False

    def _looks_like_quoted_collection_opener(index: int) -> bool:
        cursor = index + 1
        while cursor < len(text) and text[cursor] in " \t\r\n":
            cursor += 1
        if cursor >= len(text) or text[cursor] not in "{[":
            return False

        opener = text[cursor]
        cursor += 1
        while cursor < len(text) and text[cursor] in " \t\r\n":
            cursor += 1
        if cursor >= len(text):
            return False
        if opener == "{":
            return text[cursor] in {'"', "}"}
        return text[cursor] in {'"', "{", "[", "]"}

    def _flush_whitespace() -> None:
        nonlocal whitespace_after_value
        if whitespace_after_value:
            result.append(whitespace_after_value)
            whitespace_after_value = ""

    for index, ch in enumerate(text):
        if in_string:
            result.append(ch)
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"':
                in_string = False
                pending_array_value = bool(stack and stack[-1] == "[")
            continue

        if pending_array_value:
            if ch in " \t\r\n":
                whitespace_after_value += ch
                continue
            if ch in {",", "]"}:
                _flush_whitespace()
                pending_array_value = False
            elif ch in {'"', "{", "["} and stack and stack[-1] == "[":
                result.append(",")
                _flush_whitespace()
                pending_array_value = False
                changed = True
            else:
                _flush_whitespace()
                pending_array_value = False

        if ch == '"' and stack and stack[-1] == "[" and _looks_like_quoted_collection_opener(index):
            changed = True
            continue

        if ch == '"':
            result.append(ch)
            in_string = True
            escape_next = False
            continue
        if ch == "{":
            stack.append("{")
        elif ch == "[":
            stack.append("[")
        elif ch in "}]":
            expected = "{" if ch == "}" else "["
            if stack and stack[-1] == expected:
                stack.pop()
                pending_array_value = bool(stack and stack[-1] == "[")
        result.append(ch)

    _flush_whitespace()
    return "".join(result) if changed else text


def repair_missing_colon_delimiters(text: str) -> str:
    """Insert missing ``:`` between object keys and their values.

    Chinese LLMs occasionally emit object members where the colon
    separator between key and value is dropped, for example::

        {"name" "张三", "age" 25}

    The JSON decoder reports ``Expecting ':' delimiter`` at the position
    of the value.  This scanner detects the pattern and inserts the
    missing colon without altering any content.

    The repair is conservative: it only fires inside objects after a
    complete key string, and only when the next non-whitespace character
    is a valid JSON value opener (``"``, ``{``, ``[``, digit, ``t``/``f``/``n``).

    The repair must never insert a colon inside a value string. To guard
    against malformed JSON where an object key is missing its opening quote
    (e.g. ``{element_id":"horror_dread_rhythm"}``), the scanner tracks whether
    the key was actually opened with a ``"``. A key that lacks its opening
    quote cannot reliably transition to ``expect_colon``, so the repair is
    skipped for such tokens and the malformed input is returned unchanged so
    a later, safer strategy can handle it.
    """
    if not text or '"' not in text or "{" not in text:
        return text

    result: list[str] = []
    stack: list[dict[str, object]] = []
    in_string = False
    escape_next = False
    string_role = ""  # "object_key" or "value"
    changed = False

    # Characters that can start a JSON value
    _VALUE_STARTERS = frozenset('"{[-0123456789tfn')

    def current() -> dict[str, object] | None:
        return stack[-1] if stack else None

    for ch in text:
        if in_string:
            result.append(ch)
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"':
                in_string = False
                ctx = current()
                if (
                    string_role == "object_key"
                    and ctx is not None
                    and ctx["type"] == "object"
                    # Only trust the key->colon transition when the key was
                    # actually opened with a quote. A bare key (missing its
                    # opening quote) leaves key_open_quote_seen False, so the
                    # following value quote must not be mistaken for a key
                    # close -- otherwise a value string gets corrupted by a
                    # spurious inserted colon (e.g. ``rhy:thm``).
                    and ctx.get("key_open_quote_seen")
                ):
                    ctx["state"] = "expect_colon"
                elif ctx is not None:
                    if ctx["type"] == "object" and ctx["state"] == "expect_value":
                        ctx["state"] = "after_value"
                    elif ctx["type"] == "array" and ctx["state"] == "expect_value":
                        ctx["state"] = "after_value"
                string_role = ""
            continue

        ctx = current()

        # After a key string, expecting colon
        if ctx is not None and ctx["type"] == "object" and ctx["state"] == "expect_colon":
            if ch in " \t\r\n":
                result.append(ch)
                continue
            if ch == ":":
                result.append(ch)
                ctx["state"] = "expect_value"
                ctx["has_key_value"] = True
                continue
            # Check if this is a value start (missing colon)
            if ch in _VALUE_STARTERS:
                result.append(":")
                result.append(ch)
                ctx["state"] = "expect_value"
                ctx["has_key_value"] = True
                changed = True
                # If the value starts with a quote, enter string mode
                if ch == '"':
                    in_string = True
                    escape_next = False
                    string_role = "value"
                elif ch == "{":
                    # Push new object container
                    stack.append({"type": "object", "state": "expect_key", "has_key_value": False})
                elif ch == "[":
                    # Push new array container
                    stack.append({"type": "array", "state": "expect_value"})
                continue
            # Not a value starter - let it through (will be a parse error)
            result.append(ch)
            continue

        if ch in " \t\r\n":
            result.append(ch)
            continue

        if ch == '"':
            ctx = current()
            if (
                ctx is not None
                and ctx["type"] == "object"
                and ctx["state"] == "expect_key"
                # A key must start with a quote to be treated as an object key.
                # If bare (unquoted) key characters were already consumed for
                # this member, the upcoming quote is the value opener, not a
                # key opener -- misclassifying it corrupts the value.
                and not ctx.get("bare_key_seen")
            ):
                string_role = "object_key"
                # Mark that this key was opened with a quote so the closing
                # quote can reliably trigger the key->colon transition.
                ctx["key_open_quote_seen"] = True
            else:
                string_role = "value"
            in_string = True
            escape_next = False
            result.append(ch)
            continue

        if ch == "{":
            result.append(ch)
            stack.append(
                {
                    "type": "object",
                    "state": "expect_key",
                    "has_key_value": False,
                    "key_open_quote_seen": False,
                    "bare_key_seen": False,
                }
            )
            continue

        if ch == "[":
            result.append(ch)
            stack.append({"type": "array", "state": "expect_value"})
            continue

        if ch == ",":
            result.append(ch)
            ctx = current()
            if ctx is not None:
                if ctx["type"] == "object":
                    ctx["state"] = "expect_key"
                    ctx["key_open_quote_seen"] = False
                    ctx["bare_key_seen"] = False
                elif ctx["type"] == "array":
                    ctx["state"] = "expect_value"
            continue

        if ch == "}":
            ctx = current()
            if ctx is not None and ctx["type"] == "object":
                stack.pop()
                result.append(ch)
                # Mark parent value consumed
                parent = current()
                if parent is not None:
                    if parent["type"] == "object" and parent["state"] == "expect_value":
                        parent["state"] = "after_value"
                    elif parent["type"] == "array" and parent["state"] == "expect_value":
                        parent["state"] = "after_value"
                continue
            result.append(ch)
            continue

        if ch == "]":
            if stack and stack[-1]["type"] == "array":
                stack.pop()
                result.append(ch)
                parent = current()
                if parent is not None:
                    if parent["type"] == "object" and parent["state"] == "expect_value":
                        parent["state"] = "after_value"
                    elif parent["type"] == "array" and parent["state"] == "expect_value":
                        parent["state"] = "after_value"
                continue
            result.append(ch)
            continue

        result.append(ch)
        # Mark value consumed for non-structural chars (numbers, true/false/null)
        ctx = current()
        if ctx is not None:
            if ctx["type"] == "object" and ctx["state"] == "expect_value":
                ctx["state"] = "after_value"
            elif ctx["type"] == "array" and ctx["state"] == "expect_value":
                ctx["state"] = "after_value"
            elif (
                ctx["type"] == "object"
                and ctx["state"] == "expect_key"
                # A non-structural char while still expecting a key means the
                # object member has a bare (unquoted) key. Flag it so the next
                # quote is not misread as a key opener.
                and not ctx.get("bare_key_seen")
            ):
                ctx["bare_key_seen"] = True

    return "".join(result) if changed else text
