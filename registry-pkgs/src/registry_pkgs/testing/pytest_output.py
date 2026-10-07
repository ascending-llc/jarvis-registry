"""
Pytest plugin that keeps the output of passing test runs to the final summary line. In the age of AI coding
agents, we should make the outputs of unit test runs that need no action as short as possible to avoid
polluting the LLM context. When certain tests fail, the output is as detailed as without this plugin.

Each workspace loads it from its ``tests/conftest.py`` with
``pytest_plugins = ["registry_pkgs.testing.pytest_output"]``.
"""


def pytest_report_teststatus(report, config):
    """
    Suppresses the green dots and yellow skip letters for passed and skipped tests.
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
