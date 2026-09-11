"""The single editing path for pulse spans, shared by preview and Save."""
from __future__ import annotations

from datetime import date, timedelta

from .models import RepoConfig
from .scheduler import generate_automatic_plan
from .utils import canonical_hhmm


def update_span(
    repo: RepoConfig,
    start_date: str,
    days: str | int,
    total_pulses: str | int,
    start_time: str,
    end_time: str,
    *,
    today: date | None = None,
    completed_today: int = 0,
) -> RepoConfig:
    """Return an independent edited copy without changing the saved repository.

    Opening and saving an unchanged span preserves its exact allocation. Past
    dates in an active span retain their saved slots; only remaining dates are
    redistributed. Today's target cannot fall below pulses already completed.
    """
    today = today or date.today()
    try:
        day_count = int(str(days).strip())
        total = int(str(total_pulses).strip())
    except ValueError as exc:
        raise ValueError("Days and total pulses must be whole numbers.") from exc
    if not 1 <= day_count <= 365:
        raise ValueError("Days in span must be between 1 and 365.")
    try:
        first = date.fromisoformat(start_date.strip())
        dates = [(first + timedelta(days=i)).isoformat() for i in range(day_count)]
    except (ValueError, OverflowError) as exc:
        raise ValueError("Enter a valid start date as YYYY-MM-DD.") from exc
    if total < day_count:
        raise ValueError(f"Use at least {day_count} pulses for {day_count} days (one per day).")
    start = canonical_hhmm(start_time)
    end = canonical_hhmm(end_time)
    if end <= start:
        raise ValueError("End time must be later than start time on the same day.")
    updated = RepoConfig.from_dict(repo.to_dict())
    updated.schedule_mode = "span"
    unchanged = (
        dates == sorted(repo.calendar_plan)
        and total == sum(len(times) for times in repo.calendar_plan.values())
        and start == canonical_hhmm(repo.start_time)
        and end == canonical_hhmm(repo.end_time)
    )
    if unchanged:
        return updated

    fixed: dict[str, list[str]] = {}
    for key in dates:
        if key < today.isoformat():
            if key not in repo.calendar_plan:
                raise ValueError("New span dates must start today or later.")
            fixed[key] = list(repo.calendar_plan[key])
    pending = [key for key in dates if key not in fixed]
    if not pending:
        raise ValueError("This span has ended. Choose today or a future start date.")
    remaining = total - sum(len(times) for times in fixed.values())
    minimums = {today.isoformat(): max(1, completed_today)}
    required = sum(minimums.get(key, 1) for key in pending)
    if remaining < required:
        minimum_total = total - remaining + required
        raise ValueError(
            f"Use at least {minimum_total} pulses to keep past days and today's "
            "completed pulses, with one on every remaining day."
        )
    plan = generate_automatic_plan(
        updated, pending[0], len(pending), remaining, start, end, minimums=minimums
    )
    updated.calendar_plan = {**fixed, **plan}
    updated.start_time = start
    updated.end_time = end
    return updated
