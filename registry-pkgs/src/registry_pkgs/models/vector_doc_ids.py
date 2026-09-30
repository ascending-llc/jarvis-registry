"""Deterministic weaviate object ids for a model's vector documents.

Weaviate's batch import upserts by UUID (an object with an existing id replaces the old one), so
giving each document a stable, content-independent id makes re-inserting the same entity idempotent.
That is what stops a reindex pod whose lease was taken over from creating duplicate rows in the new
generation when it and the new owner both sweep the same document.

The ids are positional (``:i``): identical Mongo content always yields the same document list in the
same order, hence the same ids; two natural keys that collide (e.g. two prompts with one name) still
get distinct ids. This is safe because vector writes are gated during a reindex, so ``to_documents``
is a stable pure function of the (frozen) Mongo document while a sweep runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid5

if TYPE_CHECKING:
    from langchain_core.documents import Document

_VECTOR_DOC_ID_NAMESPACE = UUID("1b681868-a2a0-5e68-9c0d-9b7f6e5d4c3b")


def assign_deterministic_doc_ids(docs: list[Document], collection_name: str, entity_id: object) -> list[Document]:
    """Stamp each document with a deterministic id when the entity has an id; otherwise leave ids None."""
    if entity_id is None:
        return docs
    for i, doc in enumerate(docs):
        doc.id = str(uuid5(_VECTOR_DOC_ID_NAMESPACE, f"{collection_name}:{entity_id}:{i}"))
    return docs
