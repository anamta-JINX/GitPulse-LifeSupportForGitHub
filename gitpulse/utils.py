from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from .models import RepoConfig


def parse_hhmm(value: str) -> int:
    raw = value.strip().upper().replace(".", "")
    twelve_hour = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([AP])M", raw)
    if twelve_hour:
        hour = int(twelve_hour.group(1))
        minute = int(twelve_hour.group(2) or 0)
        if not (1 <= hour <= 12 and 0 <= minute <= 59):
            raise ValueError("Use a valid 12-hour time, for example 10:00 AM or 5:30 PM.")
        if hour == 12:
            hour = 0
        if twelve_hour.group(3) == "P":
            hour += 12
        return hour * 60 + minute

    match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if not match:
        raise ValueError("Use a time such as 10:00 AM or 5:30 PM.")
    hour, minute = map(int, match.groups())
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Use a valid time such as 10:00 AM or 5:30 PM.")
    return hour * 60 + minute


def format_hhmm(minutes: int) -> str:
    hour, minute = divmod(minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def canonical_hhmm(value: str) -> str:
    """Return a flexible time input in the internal 24-hour format."""
    return format_hhmm(parse_hhmm(value))


def format_hhmm_12(value: str | int) -> str:
    """Format an internal time for every user-facing surface."""
    minutes = parse_hhmm(value) if isinstance(value, str) else int(value)
    hour, minute = divmod(minutes, 60)
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}"


def format_datetime_12(value: datetime) -> str:
    return f"{value.strftime('%b')} {value.day} {format_hhmm_12(value.hour * 60 + value.minute)}"


def planned_times_for(repo: "RepoConfig", date_key: str) -> list[str] | None:
    raw = getattr(repo, "calendar_plan", {}).get(date_key)
    if raw is None:
        return None
    try:
        return [format_hhmm(value) for value in sorted({parse_hhmm(str(value)) for value in raw})]
    except (TypeError, ValueError):
        return None


def pulse_target_for_date(repo: "RepoConfig", date_key: str) -> int:
    planned = planned_times_for(repo, date_key)
    if getattr(repo, "calendar_plan", {}):
        return len(planned or [])
    return len(planned) if planned else int(repo.commits_per_day)


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    raw = repo_url.strip().rstrip("/")
    if not raw:
        raise ValueError("Enter a GitHub repository URL.")

    if raw.startswith("git@github.com:"):
        path = raw.split(":", 1)[1]
        parts = path.removesuffix(".git").split("/")
    else:
        if raw.startswith("github.com/"):
            raw = "https://" + raw
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"github.com", "www.github.com"}:
            raise ValueError("Repository must be a GitHub repository URL.")
        parts = [piece for piece in parsed.path.split("/") if piece]
        if parts:
            parts[-1] = parts[-1].removesuffix(".git")

    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", piece) for piece in parts):
        raise ValueError("Repository URL must point directly to a GitHub repository.")
    return parts[0], parts[1]


def normalize_remote(url: str) -> str:
    raw = url.strip().rstrip("/").removesuffix(".git")
    if raw.startswith("git@github.com:"):
        raw = "https://github.com/" + raw.split(":", 1)[1]
    return raw.lower()


def safe_repo_name(repo_url: str, repo_id: str) -> str:
    try:
        owner, repo = parse_repo_url(repo_url)
        base = f"{owner}-{repo}"
    except Exception:
        base = "repository"
    digest = hashlib.sha1(f"{repo_id}|{repo_url}".encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-") or "repository"
    return f"{safe}-{digest}"


def repo_commit_url(repo_url: str, commit_hash: str) -> str:
    owner, repo = parse_repo_url(repo_url)
    return f"https://github.com/{owner}/{repo}/commit/{commit_hash}"


def today_key() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def path_for_display(path: Path) -> str:
    try:
        return str(path.resolve())
    except Exception:
        return str(path)
