"""Stateful test double for the scripts' small, explicitly supported MongoDB subset.

This is not a MongoDB emulator: unsupported filters and compound values fail loudly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from copy import deepcopy
from typing import Any

from pymongo.results import DeleteResult, UpdateResult

type Document = dict[str, Any]
type Predicate = Callable[[Document], bool]

MISSING = object()


def _scalar(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple, set)):
        raise NotImplementedError("Only top-level scalar values are supported")
    return value


def _field_predicate(field: str, operator: str, operand: Any) -> Predicate:
    if "." in field or field.startswith("$"):
        raise NotImplementedError(f"Unsupported field: {field}")
    if operator in {"$in", "$nin"}:
        if not isinstance(operand, list):
            raise NotImplementedError(f"{operator} requires a list of scalar values")
        for item in operand:
            _scalar(item)
    elif operator == "$exists":
        if not isinstance(operand, bool):
            raise NotImplementedError("$exists requires a boolean")
    elif operator in {"$eq", "$ne"}:
        _scalar(operand)
    else:
        raise NotImplementedError(f"Unsupported operator: {operator}")

    def match(document: Document) -> bool:
        value = _scalar(document.get(field, MISSING))
        if operator == "$exists":
            return (value is not MISSING) == operand
        # MongoDB equality to null also matches missing fields. Its negation does not.
        candidate = None if value is MISSING else value
        if operator == "$in":
            return candidate in operand
        if operator == "$nin":
            return candidate not in operand
        if operator == "$ne":
            return candidate != operand
        return candidate == operand

    return match


def _predicate(query: Document) -> Predicate:
    predicates: list[Predicate] = []
    for field, condition in query.items():
        if field == "$or":
            if not isinstance(condition, list) or not condition or not all(isinstance(q, dict) for q in condition):
                raise NotImplementedError("$or requires a nonempty list of queries")
            alternatives = [_predicate(clause) for clause in condition]
            predicates.append(lambda doc, clauses=alternatives: any(clause(doc) for clause in clauses))
        elif isinstance(condition, dict):
            if not condition:
                raise NotImplementedError("Nested document equality is not supported")
            predicates.extend(_field_predicate(field, op, value) for op, value in condition.items())
        else:
            predicates.append(_field_predicate(field, "$eq", condition))
    return lambda document: all(predicate(document) for predicate in predicates)


class MemoryCursor:
    def __init__(self, documents: list[Document]) -> None:
        self.documents = documents

    def limit(self, count: int) -> MemoryCursor:
        if count < 0:
            raise NotImplementedError("Negative cursor limits are not supported")
        if count:
            self.documents = self.documents[:count]
        return self

    async def __aiter__(self) -> AsyncIterator[Document]:
        for document in self.documents:
            yield deepcopy(document)


class MemoryCollection:
    def __init__(self, database: MemoryDatabase, name: str) -> None:
        self.database = database
        self.name = name

    def find(self, query: Document, projection: dict[str, int] | None = None) -> MemoryCursor:
        predicate = _predicate(query)
        if projection is not None and any(value != 1 or "." in field for field, value in projection.items()):
            raise NotImplementedError("Only inclusive top-level projections are supported")
        documents = [deepcopy(doc) for doc in self.database.documents.get(self.name, []) if predicate(doc)]
        if projection:
            fields = set(projection) | {"_id"}
            documents = [{key: value for key, value in doc.items() if key in fields} for doc in documents]
        return MemoryCursor(documents)

    async def distinct(self, field: str, query: Document) -> list[Any]:
        values: list[Any] = []
        async for document in self.find(query):
            if field in document:
                value = _scalar(document[field])
                if value not in values:
                    values.append(value)
        return values

    async def update_many(self, query: Document, update: Document) -> UpdateResult:
        predicate = _predicate(query)
        for operator, fields in update.items():
            if operator not in {"$set", "$unset"} or not isinstance(fields, dict):
                raise NotImplementedError(f"Unsupported update: {operator}")
            for field, value in fields.items():
                _field_predicate(field, "$eq", value)
        matched = modified = 0
        for document in self.database.documents.get(self.name, []):
            if not predicate(document):
                continue
            matched += 1
            before = deepcopy(document)
            document.update(deepcopy(update.get("$set", {})))
            for field in update.get("$unset", {}):
                document.pop(field, None)
            modified += document != before
        return UpdateResult({"n": matched, "nModified": modified}, acknowledged=True)

    async def delete_many(self, query: Document) -> DeleteResult:
        predicate = _predicate(query)
        documents = self.database.documents.get(self.name, [])
        retained = [document for document in documents if not predicate(document)]
        deleted = len(documents) - len(retained)
        if self.name in self.database.documents:
            self.database.documents[self.name] = retained
        return DeleteResult({"n": deleted}, acknowledged=True)


class MemoryDatabase:
    def __init__(self, documents: dict[str, list[Document]]) -> None:
        self.documents = deepcopy(documents)
        self.collections: dict[str, MemoryCollection] = {}

    def __getitem__(self, name: str) -> MemoryCollection:
        if name not in self.collections:
            self.collections[name] = MemoryCollection(self, name)
        return self.collections[name]

    async def list_collection_names(self) -> list[str]:
        return list(self.documents)

    def snapshot(self) -> dict[str, list[Document]]:
        return deepcopy(self.documents)
