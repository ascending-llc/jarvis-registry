from datetime import UTC, datetime, timedelta

import pytest

from workflow_worker import scheduler


@pytest.mark.parametrize(
    ("deadline_offset", "max_sleep_seconds", "expected"),
    [
        (None, 30.0, 30.0),
        (5.0, 30.0, 5.0),
        (60.0, 30.0, 30.0),
        (-5.0, 30.0, 0.0),
    ],
)
def test_calculate_sleep_seconds(
    deadline_offset: float | None,
    max_sleep_seconds: float,
    expected: float,
) -> None:
    now = datetime.now(UTC)
    next_due = None if deadline_offset is None else now + timedelta(seconds=deadline_offset)

    assert scheduler.calculate_sleep_seconds(next_due, now, max_sleep_seconds) == expected
