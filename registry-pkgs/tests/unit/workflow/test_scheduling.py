from datetime import UTC, datetime

import pytest

from registry_pkgs.workflows.scheduling import calculate_next_run_at, validate_schedule


def test_calculate_next_run_at_converts_local_time_to_utc() -> None:
    base_time = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)

    next_run = calculate_next_run_at("0 2 * * *", "Asia/Shanghai", base_time)

    assert next_run == datetime(2026, 8, 20, 18, 0, tzinfo=UTC)


@pytest.mark.parametrize("cron_expression", ["0 2 * *", "0 2 * * * *", "61 2 * * *"])
def test_validate_schedule_rejects_non_standard_cron(cron_expression: str) -> None:
    with pytest.raises(ValueError, match="five-field"):
        validate_schedule(cron_expression, "UTC")


def test_validate_schedule_rejects_unknown_timezone() -> None:
    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        validate_schedule("0 2 * * *", "Mars/Olympus")


def test_calculate_next_run_at_accepts_naive_base_as_utc() -> None:
    next_run = calculate_next_run_at("*/15 * * * *", "UTC", datetime(2026, 8, 20, 0, 1))

    assert next_run == datetime(2026, 8, 20, 0, 15, tzinfo=UTC)
