"""Tests for novel_forge.core.guards."""

from __future__ import annotations

import pytest

from novel_forge.core.guards import assert_not_coroutine


async def async_func() -> int:
    return 42


async def async_func_no_return() -> None:
    pass


def regular_func() -> int:
    return 42


class SampleClass:
    async def async_method(self) -> str:
        return "async method"

    def regular_method(self) -> str:
        return "regular method"


def test_raises_on_unawaited_coroutine_from_async_func() -> None:
    result = async_func()
    with pytest.raises(RuntimeError, match="unawaited coroutine"):
        assert_not_coroutine(result, "async_func()")
    result.close()


def test_raises_on_unawaited_coroutine_from_async_method() -> None:
    obj = SampleClass()
    result = obj.async_method()
    with pytest.raises(RuntimeError, match="unawaited coroutine"):
        assert_not_coroutine(result, "obj.async_method()")
    result.close()


def test_raises_on_unawaited_coroutine_no_return() -> None:
    result = async_func_no_return()
    with pytest.raises(RuntimeError, match="unawaited coroutine"):
        assert_not_coroutine(result, "async_func_no_return()")
    result.close()


def test_raises_on_async_function_passed_as_value() -> None:
    with pytest.raises(RuntimeError, match="coroutine function"):
        assert_not_coroutine(async_func, "async_func itself")


def test_raises_on_async_method_passed_as_value() -> None:
    obj = SampleClass()
    with pytest.raises(RuntimeError, match="coroutine function"):
        assert_not_coroutine(obj.async_method, "obj.async_method itself")


def test_passes_on_regular_function() -> None:
    assert_not_coroutine(regular_func, "regular_func()")


def test_passes_on_regular_method() -> None:
    obj = SampleClass()
    assert_not_coroutine(obj.regular_method, "obj.regular_method()")


def test_passes_on_none() -> None:
    assert_not_coroutine(None, "None value")


def test_passes_on_string() -> None:
    assert_not_coroutine("hello", "string value")


def test_passes_on_int() -> None:
    assert_not_coroutine(42, "integer value")


def test_passes_on_list() -> None:
    assert_not_coroutine([1, 2, 3], "list value")


def test_passes_on_dict() -> None:
    assert_not_coroutine({"key": "value"}, "dict value")


def test_passes_on_awaited_result() -> None:
    import asyncio

    async def get_value() -> int:
        return 100

    awaited_value = asyncio.run(get_value())
    assert_not_coroutine(awaited_value, "awaited_value")


def test_error_message_contains_context() -> None:
    result = async_func()
    try:
        with pytest.raises(RuntimeError, match=r"async_func\(\)"):
            assert_not_coroutine(result, "async_func()")
    finally:
        result.close()


def test_error_message_contains_coroutine_type() -> None:
    result = async_func()
    try:
        with pytest.raises(RuntimeError, match="coroutine"):
            assert_not_coroutine(result, "test")
    finally:
        result.close()