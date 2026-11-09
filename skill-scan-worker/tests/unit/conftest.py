"""Unit-test setup: allow constructing Beanie Document models without init_beanie / a real Mongo.

Beanie's ``Document.__init__`` resolves the collection, so constructing ``ModelSource`` or ``SkillScanResult``
raises ``CollectionWasNotInitialized`` without an init. Patching ``get_settings`` (the same trick as
registry-pkgs' own unit tests) lets construction and the capping validators run offline. Integration tests under
``tests/integration`` are unaffected: they init Beanie and this conftest does not apply there.
"""

from unittest.mock import patch

import pytest
from beanie.odm.settings.document import DocumentSettings

from registry_pkgs.models.model_source import ModelSource
from registry_pkgs.models.skill_scan_result import SkillScanResult


@pytest.fixture(autouse=True)
def _offline_document_settings():
    with (
        patch.object(ModelSource, "get_settings", return_value=DocumentSettings()),
        patch.object(SkillScanResult, "get_settings", return_value=DocumentSettings()),
    ):
        yield
