"""Pytest configuration for workflow-worker tests."""

import os

from registry_pkgs.testing.fixtures import disable_dotenv_loading, setup_test_scope_group_ids

disable_dotenv_loading()
setup_test_scope_group_ids()
os.environ.setdefault("X_JARVIS_REGISTRY_IMPORT_CHECKS", "disabled")
os.environ.setdefault("CREDS_KEY", "00" * 16)


pytest_plugins = ["registry_pkgs.testing.pytest_output"]
