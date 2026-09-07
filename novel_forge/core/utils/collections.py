"""Collection manipulation utilities."""

from __future__ import annotations

from typing import Any, Callable, TypeVar, cast, overload

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")


def deduplicate_list(
    items: list[T],
    *,
    key: Callable[[T], Any] | None = None,
    preserve_order: bool = True,
) -> list[T]:
    """Remove duplicates from list.
    
    Args:
        items: List to deduplicate
        key: Optional function to extract comparison key
        preserve_order: If True, preserve original order
        
    Returns:
        List with duplicates removed
        
    Examples:
        >>> deduplicate_list([1, 2, 1, 3, 2])
        [1, 2, 3]
        >>> deduplicate_list(["a", "B", "a"], key=str.lower)
        ["a", "B"]
    """
    if not preserve_order:
        # Use set for faster deduplication if order doesn't matter
        if key is None:
            try:
                unique_items: set[Any] = set(cast(list[Any], items))
                return list(cast(set[T], unique_items))
            except TypeError:
                # Items are not hashable, fall through to preserve_order path
                pass
    
    seen: set[Any] = set()
    result: list[T] = []
    
    for item in items:
        item_key = key(item) if key else item
        try:
            if item_key not in seen:
                seen.add(item_key)
                result.append(item)
        except TypeError:
            # item_key is not hashable, use linear search
            found = False
            for seen_item in result:
                if (key(seen_item) if key else seen_item) == item_key:
                    found = True
                    break
            if not found:
                result.append(item)
    
    return result


def group_by(
    items: list[T],
    key_fn: Callable[[T], K],
) -> dict[K, list[T]]:
    """Group items by key function.
    
    Args:
        items: Items to group
        key_fn: Function to extract grouping key
        
    Returns:
        Dictionary mapping keys to lists of items
        
    Examples:
        >>> items = [{"id": 1, "type": "a"}, {"id": 2, "type": "b"}]
        >>> group_by(items, lambda x: x["type"])
        {"a": [...], "b": [...]}
    """
    groups: dict[K, list[T]] = {}
    
    for item in items:
        key = key_fn(item)
        if key not in groups:
            groups[key] = []
        groups[key].append(item)
    
    return groups


def merge_dicts(*dicts: Any, deep: bool = False) -> dict[str, Any]:
    """Merge multiple dictionaries.
    
    Args:
        *dicts: Dictionaries to merge
        deep: If True, recursively merge nested dicts
        
    Returns:
        Merged dictionary (later dicts override earlier ones)
        
    Examples:
        >>> merge_dicts({"a": 1}, {"b": 2})
        {"a": 1, "b": 2}
        >>> merge_dicts({"a": 1}, {"a": 2})  # Later overrides earlier
        {"a": 2}
    """
    result: dict[str, Any] = {}
    
    for d in dicts:
        if not isinstance(d, dict):
            continue
        
        if deep:
            for key, value in d.items():
                if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                    # Recursive merge for nested dicts
                    result[key] = merge_dicts(result[key], value, deep=True)
                else:
                    result[key] = value
        else:
            result.update(d)
    
    return result


@overload
def flatten_list(items: list[T | list[T]]) -> list[T]:
    ...


@overload
def flatten_list(items: list[T | list[T]], *, depth: int) -> list[T | list[Any]]:
    ...


def flatten_list(
    items: list[Any],
    *,
    depth: int = 1,
) -> list[Any]:
    """Flatten a nested list.
    
    Args:
        items: List potentially containing nested lists
        depth: How many levels to flatten (default 1)
        
    Returns:
        Flattened list
        
    Examples:
        >>> flatten_list([1, [2, 3], 4])
        [1, 2, 3, 4]
        >>> flatten_list([1, [2, [3, 4]], 5], depth=1)
        [1, 2, [3, 4], 5]
    """
    if depth <= 0:
        return items
    
    result: list[Any] = []
    
    for item in items:
        if isinstance(item, list):
            if depth > 1:
                result.extend(flatten_list(item, depth=depth - 1))
            else:
                result.extend(item)
        else:
            result.append(item)
    
    return result


def chunk_list(items: list[T], chunk_size: int) -> list[list[T]]:
    """Split list into chunks of specified size.
    
    Args:
        items: List to chunk
        chunk_size: Size of each chunk
        
    Returns:
        List of chunks
        
    Raises:
        ValueError: If chunk_size <= 0
        
    Examples:
        >>> chunk_list([1, 2, 3, 4, 5], 2)
        [[1, 2], [3, 4], [5]]
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    
    chunks: list[list[T]] = []
    
    for i in range(0, len(items), chunk_size):
        chunks.append(items[i : i + chunk_size])
    
    return chunks


def partition_list(
    items: list[T],
    predicate: Callable[[T], bool],
) -> tuple[list[T], list[T]]:
    """Partition list into two based on predicate.
    
    Args:
        items: List to partition
        predicate: Function returning True for items to include in first list
        
    Returns:
        Tuple of (matching_items, non_matching_items)
        
    Examples:
        >>> matching, non_matching = partition_list([1, 2, 3, 4], lambda x: x > 2)
        >>> matching
        [3, 4]
        >>> non_matching
        [1, 2]
    """
    matching: list[T] = []
    non_matching: list[T] = []
    
    for item in items:
        if predicate(item):
            matching.append(item)
        else:
            non_matching.append(item)
    
    return matching, non_matching


def unique_by(
    items: list[T],
    key_fn: Callable[[T], Any],
) -> list[T]:
    """Get unique items based on key function.
    
    Args:
        items: Items to filter
        key_fn: Function to extract comparison key
        
    Returns:
        List of unique items (first occurrence kept)
        
    Examples:
        >>> items = [{"id": 1, "name": "a"}, {"id": 1, "name": "b"}]
        >>> unique_by(items, lambda x: x["id"])
        [{"id": 1, "name": "a"}]
    """
    seen: set[Any] = set()
    result: list[T] = []
    
    for item in items:
        key = key_fn(item)
        try:
            if key not in seen:
                seen.add(key)
                result.append(item)
        except TypeError:
            # Key is not hashable, use linear search
            found = False
            for seen_item in result:
                if key_fn(seen_item) == key:
                    found = True
                    break
            if not found:
                result.append(item)
    
    return result
