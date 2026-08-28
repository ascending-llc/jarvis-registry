"""Pytest configuration for workflow-worker tests."""

import os

os.environ.setdefault("CREDS_KEY", "00" * 16)


def pytest_report_teststatus(report, config):
    """
    This pytest configuration suppresses the green dots and yellow skip letters
    for passed and skipped tests. In the age of AI coding agent, we should make
    the outputs of unit test runs that need no action as short as possible to
    avoid polluting the LLM context. When certain tests fail, the output is as
    detailed as without this configuration.
    """
    if report.when == "call" and report.passed:
        # Returns (category, short_letter, verbose_word)
        # Setting short_letter to "" suppresses the green dot
        return ("passed", "", "")
    if report.skipped:
        # Marker-based skips report at "setup"; runtime pytest.skip() reports at "call".
        return ("skipped", "", "")
