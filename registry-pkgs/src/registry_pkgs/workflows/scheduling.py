"""Shared CRON validation and next-fire-time calculation."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter


def validate_schedule(cron_expression: str, timezone_name: str) -> ZoneInfo:
    """Validate a standard five-field CRON expression and IANA timezone."""
    if len(cron_expression.split()) != 5 or not croniter.is_valid(cron_expression):
        raise ValueError("cron_expression must be a valid five-field CRON expression")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA timezone: {timezone_name!r}") from exc


def calculate_next_run_at(
    cron_expression: str,
    timezone_name: str,
    base_time: datetime | None = None,
) -> datetime:
    """Return the next CRON fire time normalized to UTC."""
    timezone = validate_schedule(cron_expression, timezone_name)
    base_utc = base_time or datetime.now(UTC)
    if base_utc.tzinfo is None:
        base_utc = base_utc.replace(tzinfo=UTC)
    local_base = base_utc.astimezone(timezone)
    return croniter(cron_expression, local_base).get_next(datetime).astimezone(UTC)
