from datetime import datetime


def calculate_sleep_seconds(
    next_due: datetime | None,
    now: datetime,
    max_sleep_seconds: float,
) -> float:
    """Adaptive sleep: wait until the next known deadline, capped at max_sleep_seconds.

    The cap ensures newly created or toggled schedules are discovered within
    max_sleep_seconds even if they fire sooner than the previously peeked deadline.
    """
    if next_due is None:
        return max_sleep_seconds
    return min(max_sleep_seconds, max(0.0, (next_due - now).total_seconds()))
