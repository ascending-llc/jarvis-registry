from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from registry_pkgs.models.workflow import WorkflowRun


class FakeRunCollection:
    """Minimal in-memory ``workflow_runs`` collection for one document.

    Honours the filters the run-write paths use: ``_id``, ``status`` (a plain value or
    ``{"$in": [...]}``) and ``pending_directive`` equality. Supports ``$set`` and ``$unset``.
    """

    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc

    def _matches(self, flt: dict[str, Any]) -> bool:
        if flt["_id"] != self.doc["_id"]:
            return False
        status = flt.get("status")
        if isinstance(status, dict):
            if self.doc["status"] not in status["$in"]:
                return False
        elif status is not None and self.doc["status"] != status:
            return False
        return "pending_directive" not in flt or self.doc.get("pending_directive") == flt["pending_directive"]

    async def update_one(self, flt, update, session=None, **kwargs):  # noqa: ANN001, ANN003, ANN201
        if not self._matches(flt):
            return SimpleNamespace(matched_count=0, modified_count=0)
        before = dict(self.doc)
        self.doc.update(update.get("$set", {}))
        for key in update.get("$unset", {}):
            self.doc.pop(key, None)
        modified = 0 if self.doc == before else 1
        return SimpleNamespace(matched_count=1, modified_count=modified)


@pytest.fixture
def fake_run_collection(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], FakeRunCollection]:
    """Return a factory that backs ``WorkflowRun.get_pymongo_collection()`` with one fake document."""

    def _install(doc: dict[str, Any]) -> FakeRunCollection:
        collection = FakeRunCollection(doc)
        monkeypatch.setattr(WorkflowRun, "get_pymongo_collection", classmethod(lambda cls: collection))
        return collection

    return _install
