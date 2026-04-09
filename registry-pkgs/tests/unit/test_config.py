import logging
import os
from unittest.mock import patch

import pytest

from registry_pkgs.core.config import JarvisBaseSettings


@pytest.mark.unit
@patch.dict(os.environ, {"X_JARVIS_REGISTRY_IMPORT_CHECKS": "disabled"})
def test_validation_disablement(caplog) -> None:
    caplog.set_level(logging.WARNING)

    JarvisBaseSettings()

    assert "JWT_PRIVATE_KEY and JWT_PUBLIC_KEY validation is disabled." in caplog.text
