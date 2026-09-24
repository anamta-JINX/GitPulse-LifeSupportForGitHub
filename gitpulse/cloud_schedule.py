"""Pure scheduling helpers for GitPulse cloud execution.

This module intentionally has no GUI, tray, storage, or Git side effects. The
random-time algorithm mirrors the desktop scheduler's stable per-day bucketing
logic so cloud automation cannot interfere with the Windows application.
"""
from __future__ import annotations

import random


def parse_hhmm(value: str) -> int:
    """Convert HH:MM to minutes after midnight."""
    try:
        hour_text, minute_text = value.strip().split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Time must use HH:MM.") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Time must use a valid 24-hour HH:MM value.")
    return hour * 60 + minute


def format_hhmm(value: int) -> str:
    """Convert minutes after midnight to HH:MM."""
    if not 0 <= value < 24 * 60:
        raise ValueError("Minute value is outside one day.")
    return f"{value // 60:02d}:{value % 60:02d}"


def random_schedule_times(
    date_key: str,
    repo_id: str,
    repo_url: str,
    count: int = 2,
    start_time: str = "10:00",
    end_time: str = "21:59",
    variant: int = 0,
) -> list[str]:
    """Return stable, unique random times for one date.

    This intentionally follows GitPulse's desktop scheduler algorithm:
    a stable seed is built from the date/repository/settings, the allowed time
    span is divided into one bucket per pulse, and one random minute is chosen
    from each bucket. Re-running on the same date yields the same times.
    """
    pulse_count = int(count)
    if pulse_count < 1:
        raise ValueError("Pulse count must be at least 1.")

    start = parse_hhmm(start_time)
    end = parse_hhmm(end_time)
    if end <= start:
        raise ValueError("End time must be later than start time.")

    span = end - start + 1
    if pulse_count > span:
        raise ValueError("The time window is too small for unique pulse times.")

    seed = (
        f"GitPulse|{date_key}|{repo_id}|{repo_url}|"
        f"{pulse_count}|{start_time}|{end_time}|{variant}"
    )
    rng = random.Random(seed)

    values: list[int] = []
    for index in range(pulse_count):
        bucket_start = start + int(index * span / pulse_count)
        bucket_end = start + int((index + 1) * span / pulse_count) - 1
        bucket_end = max(bucket_start, min(bucket_end, end))
        values.append(rng.randint(bucket_start, bucket_end))

    return [format_hhmm(value) for value in sorted(values)]


def due_pulse_count(planned_times: list[str], now_hhmm: str) -> int:
    """Return how many planned pulses should already have happened."""
    now_minutes = parse_hhmm(now_hhmm)
    return sum(1 for value in planned_times if parse_hhmm(value) <= now_minutes)
