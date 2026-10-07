"""Pytest configuration for workflow-worker tests."""

import os

from registry_pkgs.testing.fixtures import disable_dotenv_loading

disable_dotenv_loading()
os.environ.setdefault("X_JARVIS_REGISTRY_IMPORT_CHECKS", "disabled")
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


def pytest_sessionfinish(session):
    """
    Drops the empty line that pytest's terminal reporter prints right before the final summary.
    That line ends the progress-letter line, which is empty when pytest_report_teststatus above
    suppresses every letter. A plain (non-wrapper) hook runs before the reporter's wrapper prints
    it, so this patches the writer for that one call. If a failure letter was printed, the line
    is needed and kept.
    """
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None or reporter._tw.width_of_current_line != 0:
        return

    writer = reporter._tw
    original_line = writer.line

    def line(s="", **markup):
        del writer.line  # One-shot: later calls go back to the class method.
        if s:
            original_line(s, **markup)

    writer.line = line
