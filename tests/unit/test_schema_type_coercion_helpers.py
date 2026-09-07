"""Shared test helpers for validating type coercion on Pydantic schema fields.

Importable by all per-schema validator test files (Tasks 5, 11-25).
Each helper instantiates a model with a non-typed input and asserts
that the ``mode="before"`` validator coerces it to the expected type.

Usage
-----
>>> from test_schema_type_coercion_helpers import assert_field_coerces_to_string
>>>
>>> class TestSceneIntent:
>>>     def test_summary_coerces(self) -> None:
>>>         assert_field_coerces_to_string(SceneIntent, "summary", {"nested": "obj"})
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from novel_forge.core.utils.type_coerce import stringify_text_value


def assert_field_coerces_to_string(
    model_class: type[BaseModel],
    field_name: str,
    non_string_input: Any,
    expected: str | None = None,
) -> None:
    """Verify that a Pydantic model field coerces non-string input to ``str``.

    Creates an instance of *model_class* with ``{field_name: non_string_input}``
    and asserts:

    1. No exception is raised during construction.
    2. The resulting field value is an instance of ``str``.
    3. If *expected* is provided, the value equals *expected*.
    4. If *expected* is ``None``, ``stringify_text_value(non_string_input)`` is
       used as the expected value.

    Parameters
    ----------
    model_class : type[BaseModel]
        The Pydantic model to instantiate.
    field_name : str
        Name of the field to test.
    non_string_input : Any
        A non-string value (``dict``, ``list``, ``int``, ``None``, …) that
        the ``mode="before"`` validator should coerce to ``str``.
    expected : str | None
        Expected coerced string.  When ``None`` (default) the expected value
        is computed via ``stringify_text_value(non_string_input)``.
    """
    instance = model_class(**{field_name: non_string_input})  # type: ignore[arg-type]
    value = getattr(instance, field_name)

    assert isinstance(value, str), (
        f"Expected field '{field_name}' on {model_class.__name__} to be str, "
        f"got {type(value).__name__}: {value!r}"
    )

    if expected is not None:
        assert value == expected, (
            f"Expected field '{field_name}' = {expected!r}, got {value!r}"
        )
    else:
        computed = stringify_text_value(non_string_input)
        assert value == computed, (
            f"Expected field '{field_name}' = {computed!r} "
            f"(from stringify_text_value), got {value!r}"
        )


def assert_list_field_coerces(
    model_class: type[BaseModel],
    field_name: str,
    non_list_input: Any,
    expected: list[str] | None = None,
) -> None:
    """Verify that a Pydantic model field coerces non-list input to ``list[str]``.

    Creates an instance of *model_class* with ``{field_name: non_list_input}``
    and asserts:

    1. No exception is raised during construction.
    2. The resulting field value is a ``list``.
    3. Every element in the list is a ``str``.
    4. If *expected* is provided, the value equals *expected*.

    Parameters
    ----------
    model_class : type[BaseModel]
        The Pydantic model to instantiate.
    field_name : str
        Name of the ``list[str]`` field to test.
    non_list_input : Any
        A non-list value (``str``, ``dict``, ``int``, ``None``, …) that
        the ``mode="before"`` validator should coerce to ``list[str]``.
    expected : list[str] | None
        Expected coerced list.  When ``None`` (default) equality is not
        asserted — the caller is responsible for verifying content.
    """
    instance = model_class(**{field_name: non_list_input})  # type: ignore[arg-type]
    value = getattr(instance, field_name)

    assert isinstance(value, list), (
        f"Expected field '{field_name}' on {model_class.__name__} to be list, "
        f"got {type(value).__name__}: {value!r}"
    )

    for i, item in enumerate(value):
        assert isinstance(item, str), (
            f"Expected item {i} of field '{field_name}' on "
            f"{model_class.__name__} to be str, "
            f"got {type(item).__name__}: {item!r}"
        )

    if expected is not None:
        assert value == expected, (
            f"Expected field '{field_name}' = {expected!r}, got {value!r}"
        )
