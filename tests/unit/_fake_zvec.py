"""Small fake Zvec module for vector-store tests."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class _Param:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _DataType:
    INT32 = "INT32"
    INT64 = "INT64"
    BOOL = "BOOL"
    STRING = "STRING"
    VECTOR_FP32 = "VECTOR_FP32"


class _MetricType:
    COSINE = "COSINE"


class _LogLevel:
    WARN = "WARN"


class _FieldSchema:
    def __init__(self, name: str, data_type: str, **kwargs: Any) -> None:
        self.name = name
        self.data_type = data_type
        self.kwargs = kwargs


class _VectorSchema(_FieldSchema):
    def __init__(self, name: str, data_type: str, dimension: int, **kwargs: Any) -> None:
        super().__init__(name, data_type, **kwargs)
        self.dimension = dimension


class _CollectionSchema:
    def __init__(self, name: str, fields: list[Any], vectors: Any) -> None:
        self.name = name
        self.fields = fields
        self.vectors = [vectors] if not isinstance(vectors, list) else vectors


class _Doc:
    def __init__(
        self,
        id: str,
        score: float | None = None,
        vectors: dict[str, list[float]] | None = None,
        fields: dict[str, Any] | None = None,
    ) -> None:
        self.id = id
        self.score = 0.0 if score is None else score
        self.vectors = vectors or {}
        self.fields = fields or {}

    def field(self, name: str) -> Any:
        return self.fields.get(name)


class _Fts:
    def __init__(self, query_string: str | None = None, match_string: str | None = None) -> None:
        self.query_string = query_string
        self.match_string = match_string


class _Query:
    def __init__(
        self,
        field_name: str,
        vector: list[float] | None = None,
        fts: _Fts | None = None,
        **_: Any,
    ) -> None:
        self.field_name = field_name
        self.vector = vector or []
        self.fts = fts


class _WeightedReRanker:
    def __init__(self, weights: list[float]) -> None:
        self.weights = weights


class _FakeCollection:
    def __init__(self, path: str, schema: _CollectionSchema) -> None:
        self.path = path
        self.schema = schema
        self.docs: dict[str, _Doc] = {}
        self.destroyed = False
        self.flushed = False

    @property
    def stats(self) -> Any:
        return SimpleNamespace(doc_count=len(self.docs))

    def upsert(self, docs: _Doc | list[_Doc]) -> list[Any] | Any:
        doc_list = [docs] if isinstance(docs, _Doc) else docs
        for doc in doc_list:
            self.docs[doc.id] = doc
        return [SimpleNamespace(ok=lambda: True) for _ in doc_list]

    def delete(self, ids: str | list[str]) -> list[Any] | Any:
        id_list = [ids] if isinstance(ids, str) else ids
        for doc_id in id_list:
            self.docs.pop(str(doc_id), None)
        return [SimpleNamespace(ok=lambda: True) for _ in id_list]

    def query(
        self,
        queries: _Query | list[_Query] | None = None,
        *,
        topk: int = 10,
        filter: str | None = None,
        reranker: _WeightedReRanker | None = None,
        **_: Any,
    ) -> list[_Doc]:
        candidates = [
            doc for doc in self.docs.values() if _matches_filter_expression(doc.fields, filter)
        ]
        if isinstance(queries, list):
            return _score_hybrid(candidates, queries, reranker)[:topk]
        query = queries
        if query is not None:
            if query.vector:
                scored = []
                for doc in candidates:
                    vec = doc.vectors.get(query.field_name, [])
                    distance = 1.0 - _cosine(query.vector, vec)
                    scored.append(
                        _Doc(id=doc.id, score=distance, fields=doc.fields, vectors=doc.vectors)
                    )
                scored.sort(key=lambda item: item.score)
                return scored[:topk]
            if query.fts is not None:
                scored = _score_fts(candidates, query)
                return scored[:topk]
        return candidates[:topk]

    def destroy(self) -> None:
        self.destroyed = True
        self.docs.clear()
        _REGISTRY.pop(self.path, None)
        path = Path(self.path)
        if path.exists():
            path.rmdir()

    def flush(self) -> None:
        self.flushed = True


_REGISTRY: dict[str, _FakeCollection] = {}


def install_fake_zvec(monkeypatch: Any) -> Any:
    _REGISTRY.clear()
    module = SimpleNamespace(
        DataType=_DataType,
        MetricType=_MetricType,
        LogLevel=_LogLevel,
        InvertIndexParam=_Param,
        FtsIndexParam=_Param,
        HnswIndexParam=_Param,
        HnswRabitqIndexParam=_Param,
        DiskAnnIndexParam=_Param,
        IVFIndexParam=_Param,
        FlatIndexParam=_Param,
        FieldSchema=_FieldSchema,
        VectorSchema=_VectorSchema,
        CollectionSchema=_CollectionSchema,
        Doc=_Doc,
        Fts=_Fts,
        Query=_Query,
        WeightedReRanker=_WeightedReRanker,
        init=lambda **_kwargs: None,
        create_and_open=_create_and_open,
        open=_open,
    )
    monkeypatch.setitem(__import__("sys").modules, "zvec", module)
    return module


def _create_and_open(path: str, schema: _CollectionSchema) -> _FakeCollection:
    Path(path).mkdir(parents=True, exist_ok=True)
    collection = _FakeCollection(path, schema)
    _REGISTRY[path] = collection
    return collection


def _open(path: str) -> _FakeCollection:
    if path not in _REGISTRY:
        raise FileNotFoundError(path)
    return _REGISTRY[path]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _score_fts(candidates: list[_Doc], query: _Query) -> list[_Doc]:
    terms = _text_terms(query.fts.match_string if query.fts is not None else "")
    scored = []
    for doc in candidates:
        score = _text_score(str(doc.fields.get(query.field_name, "") or ""), terms)
        if score <= 0:
            continue
        scored.append(_Doc(id=doc.id, score=score, fields=doc.fields, vectors=doc.vectors))
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored


def _score_hybrid(
    candidates: list[_Doc],
    queries: list[_Query],
    reranker: _WeightedReRanker | None,
) -> list[_Doc]:
    weights = reranker.weights if reranker is not None else [1.0 / len(queries)] * len(queries)
    scored = []
    for doc in candidates:
        score = 0.0
        for idx, query in enumerate(queries):
            weight = weights[idx] if idx < len(weights) else 0.0
            if query.vector:
                vec = doc.vectors.get(query.field_name, [])
                query_score = _cosine(query.vector, vec)
            elif query.fts is not None:
                terms = _text_terms(query.fts.match_string or query.fts.query_string or "")
                query_score = _text_score(str(doc.fields.get(query.field_name, "") or ""), terms)
            else:
                query_score = 0.0
            score += weight * query_score
        scored.append(_Doc(id=doc.id, score=score, fields=doc.fields, vectors=doc.vectors))
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored


def _text_terms(text: str | None) -> set[str]:
    return {part.lower() for part in str(text or "").split() if part.strip()}


def _text_score(text: str, terms: set[str]) -> float:
    if not terms:
        return 0.0
    lower = text.lower()
    matched = sum(1 for term in terms if term in lower)
    return matched / len(terms)


def _matches_filter_expression(fields: dict[str, Any], expression: str | None) -> bool:
    if not expression:
        return True
    clauses = [part.strip() for part in expression.split(" and ") if part.strip()]
    for clause in clauses:
        matched = False
        for operator in (">=", "<=", "!=", "=", ">", "<"):
            if operator not in clause:
                continue
            left, right = [part.strip() for part in clause.split(operator, 1)]
            actual = fields.get(left)
            expected = _parse_value(right)
            if operator == ">=":
                matched = actual >= expected
            elif operator == "<=":
                matched = actual <= expected
            elif operator == "!=":
                matched = actual != expected
            elif operator == "=":
                matched = actual == expected
            elif operator == ">":
                matched = actual > expected
            elif operator == "<":
                matched = actual < expected
            break
        if not matched:
            return False
    return True


def _parse_value(value: str) -> Any:
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return float(value)
