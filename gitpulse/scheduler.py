from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from .git_service import GitService, LocalSyncResult, PulseResult
from .models import AppConfig, RepoConfig
from .storage import APP_DIR, PID_FILE, WORKER_META_FILE, append_history, ensure_dirs, load_config, load_state, save_state, write_log
from .tray import BackgroundTray
from .utils import format_hhmm, format_hhmm_12, parse_hhmm, parse_repo_url, planned_times_for, pulse_target_for_date, repo_commit_url, today_key

LOCK_DIR = APP_DIR / "locks"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
LOCAL_SYNC_INTERVAL_SECONDS = 3600
LOCAL_SYNC_RETRY_SECONDS = 300
WORKER_PROTOCOL = "1.5.0-span-tray-worker-3"


@dataclass(frozen=True)
class SpanCompletion:
    repo_id: str
    repo_name: str
    fingerprint: str
    start_date: str
    end_date: str
    days: int
    total_pulses: int


def validate_repo(repo: RepoConfig) -> list[str]:
    errors: list[str] = []
    try:
        parse_repo_url(repo.repo_url)
    except ValueError as exc:
        errors.append(str(exc))

    email = repo.commit_email.strip()
    if not email or "@" not in email or email.startswith("@") or email.endswith("@"):
        errors.append("Enter an email associated with your GitHub account.")

    try:
        count = int(repo.commits_per_day)
        if not 1 <= count <= 100:
            errors.append("Commits per day must be between 1 and 100.")
        start = parse_hhmm(repo.start_time)
        end = parse_hhmm(repo.end_time)
        if end <= start:
            errors.append("End time must be later than start time on the same day.")
        elif count > (end - start + 1):
            errors.append("The selected time window is too small for that many unique commit times.")
    except ValueError as exc:
        errors.append(str(exc))

    return errors


def validate_local_sync(repo: RepoConfig) -> list[str]:
    if not repo.local_sync_enabled:
        return []
    raw_path = repo.local_path.strip()
    if not raw_path:
        return ["Choose the local repository folder for hourly sync."]
    local_path = Path(raw_path).expanduser()
    if not local_path.is_dir():
        return ["The hourly-sync folder does not exist or is not a directory."]
    if not (local_path / ".git").exists():
        return ["The hourly-sync folder is not a Git repository root."]
    return []


def _random_schedule_times(
    repo: RepoConfig,
    date_key: str,
    count: int,
    start_time: str,
    end_time: str,
    variant: int = 0,
) -> list[str]:
    start = parse_hhmm(start_time)
    end = parse_hhmm(end_time)
    span = end - start + 1

    # Stable for the current day so restarting the app does not regenerate the
    # day's timetable. Different date/repository/settings => different times.
    seed = f"GitPulse|{date_key}|{repo.id}|{repo.repo_url}|{count}|{start_time}|{end_time}|{variant}"
    rng = random.Random(seed)

    values: list[int] = []
    for index in range(count):
        bucket_start = start + int(index * span / count)
        bucket_end = start + int((index + 1) * span / count) - 1
        bucket_end = max(bucket_start, min(bucket_end, end))
        values.append(rng.randint(bucket_start, bucket_end))
    return [format_hhmm(value) for value in sorted(values)]


def schedule_for_day(repo: RepoConfig, date_key: str) -> list[str]:
    planned = planned_times_for(repo, date_key)
    if repo.calendar_plan:
        return planned or []
    return _random_schedule_times(
        repo,
        date_key,
        int(repo.commits_per_day),
        repo.start_time,
        repo.end_time,
    )


def generate_calendar_plan(
    repo: RepoConfig,
    start_date: str,
    days: int,
    commits_per_day: int,
    start_time: str,
    end_time: str,
) -> dict[str, list[str]]:
    """Build a stable, varied schedule for one to thirty calendar days."""
    try:
        first_day = datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Start date must use YYYY-MM-DD.") from exc
    if not 1 <= int(days) <= 30:
        raise ValueError("Calendar length must be between 1 and 30 days.")
    count = int(commits_per_day)
    if not 1 <= count <= 100:
        raise ValueError("Pulses per day must be between 1 and 100.")
    start = parse_hhmm(start_time)
    end = parse_hhmm(end_time)
    if end <= start:
        raise ValueError("End time must be later than start time.")
    if count > end - start + 1:
        raise ValueError("The selected time window is too small for that many unique pulse times.")

    plan: dict[str, list[str]] = {}
    used_schedules: set[tuple[str, ...]] = set()
    for offset in range(int(days)):
        date_key = (first_day + timedelta(days=offset)).isoformat()
        values: list[str] = []
        for variant in range(100):
            candidate = _random_schedule_times(repo, date_key, count, start_time, end_time, variant)
            values = candidate
            if tuple(candidate) not in used_schedules:
                break
        plan[date_key] = values
        used_schedules.add(tuple(values))
    return plan


def _random_daily_counts(
    repo: RepoConfig,
    start_date: str,
    days: int,
    total_pulses: int,
    daily_capacity: int,
    variant: int = 0,
) -> list[int]:
    """Split one pulse budget across days with a stable, varied mix."""
    seed = (
        f"GitPulse|automatic-counts|{repo.id}|{repo.repo_url}|{start_date}|"
        f"{days}|{total_pulses}|{daily_capacity}|{variant}"
    )
    rng = random.Random(seed)
    counts = [1 for _ in range(days)]
    remaining = total_pulses - days
    weights = [rng.uniform(0.45, 1.65) for _ in range(days)]

    while remaining:
        available = [index for index, value in enumerate(counts) if value < daily_capacity]
        if not available:
            raise ValueError("The selected time window cannot fit the requested total pulses.")
        selected = rng.choices(available, weights=[weights[index] for index in available], k=1)[0]
        counts[selected] += 1
        remaining -= 1

    # When the budget allows variation, avoid presenting an accidentally flat
    # plan. The total remains exact and every day stays within its capacity.
    if days > 1 and len(set(counts)) == 1:
        source = next((index for index, value in enumerate(counts) if value > 1), None)
        target = next((index for index, value in enumerate(counts) if value < daily_capacity and index != source), None)
        if source is not None and target is not None:
            counts[source] -= 1
            counts[target] += 1
    return counts


def generate_automatic_plan(
    repo: RepoConfig,
    start_date: str,
    days: int,
    total_pulses: int,
    start_time: str,
    end_time: str,
    variant: int = 0,
) -> dict[str, list[str]]:
    """Vary both daily pulse counts and times across a one-to-thirty-day plan."""
    try:
        first_day = datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Start date must use YYYY-MM-DD.") from exc
    day_count = int(days)
    if not 1 <= day_count <= 30:
        raise ValueError("Plan length must be between 1 and 30 days.")

    start = parse_hhmm(start_time)
    end = parse_hhmm(end_time)
    if end <= start:
        raise ValueError("End time must be later than start time.")
    daily_capacity = min(100, end - start + 1)
    total = int(total_pulses)
    minimum_total = day_count
    maximum_total = day_count * daily_capacity
    if not minimum_total <= total <= maximum_total:
        raise ValueError(
            f"Total pulses must be between {minimum_total} and {maximum_total} "
            "for the selected days and time window."
        )

    counts = _random_daily_counts(
        repo,
        first_day.isoformat(),
        day_count,
        total,
        daily_capacity,
        variant,
    )
    plan: dict[str, list[str]] = {}
    for offset, count in enumerate(counts):
        date_key = (first_day + timedelta(days=offset)).isoformat()
        plan[date_key] = _random_schedule_times(
            repo,
            date_key,
            count,
            start_time,
            end_time,
            variant=variant,
        )
    return plan


def repo_fingerprint(repo: RepoConfig) -> str:
    return "|".join(
        [
            repo.repo_url.strip(),
            str(repo.commits_per_day),
            repo.start_time.strip(),
            repo.end_time.strip(),
            repo.branch.strip(),
            str(bool(repo.enabled)),
            json.dumps(repo.calendar_plan, sort_keys=True),
        ]
    )


def span_fingerprint(repo: RepoConfig) -> str:
    payload = json.dumps(repo.calendar_plan, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sync_span_state(state: dict, config: AppConfig) -> dict:
    spans = state.setdefault("spans", {})
    planned_ids: set[str] = set()
    for repo in config.repositories:
        dates = sorted(date_key for date_key, times in repo.calendar_plan.items() if times)
        if not dates:
            continue
        planned_ids.add(repo.id)
        fingerprint = span_fingerprint(repo)
        existing = spans.get(repo.id, {})
        if existing.get("fingerprint") != fingerprint:
            existing = {
                "fingerprint": fingerprint,
                "start_date": dates[0],
                "end_date": dates[-1],
                "days": len(dates),
                "total_pulses": sum(len(repo.calendar_plan[date_key]) for date_key in dates),
                "completed_dates": [],
                "notified": False,
            }
        else:
            existing["start_date"] = dates[0]
            existing["end_date"] = dates[-1]
            existing["days"] = len(dates)
            existing["total_pulses"] = sum(len(repo.calendar_plan[date_key]) for date_key in dates)
            existing["completed_dates"] = sorted(
                date_key for date_key in set(existing.get("completed_dates", [])) if date_key in dates
            )
            existing.setdefault("notified", False)
        spans[repo.id] = existing

    for stale_id in list(spans):
        if stale_id not in planned_ids:
            spans.pop(stale_id, None)
    return spans


def _mark_span_date_complete(state: dict, repo: RepoConfig, date_key: str) -> None:
    if date_key not in repo.calendar_plan or not repo.calendar_plan.get(date_key):
        return
    item = state.get("repos", {}).get(repo.id, {})
    times = item.get("times", [])
    if not times or len(item.get("done", [])) < len(times):
        return
    span = state.get("spans", {}).get(repo.id)
    if not isinstance(span, dict) or span.get("fingerprint") != span_fingerprint(repo):
        return
    completed = set(span.get("completed_dates", []))
    completed.add(date_key)
    span["completed_dates"] = sorted(completed)


def ensure_today_state(config: AppConfig | None = None) -> dict:
    ensure_dirs()
    config = config or load_config()
    state = load_state()
    date_key = today_key()
    spans = _sync_span_state(state, config)
    previous_date = str(state.get("date", ""))
    if previous_date and previous_date != date_key:
        for repo in config.repositories:
            _mark_span_date_complete(state, repo, previous_date)
    if previous_date != date_key:
        state = {"date": date_key, "repos": {}, "spans": spans}
    else:
        state["spans"] = spans

    repo_state = state.setdefault("repos", {})
    active_ids = {repo.id for repo in config.repositories}
    for stale_id in list(repo_state):
        if stale_id not in active_ids:
            repo_state.pop(stale_id, None)

    for repo in config.repositories:
        fingerprint = repo_fingerprint(repo)
        existing = repo_state.get(repo.id, {})
        if existing.get("fingerprint") != fingerprint:
            times = schedule_for_day(repo, date_key)
            completed = min(len(existing.get("done", [])), len(times))
            existing = {
                "fingerprint": fingerprint,
                "times": times,
                "done": [str(index) for index in range(1, completed + 1)],
                "last_error": "",
                "local_sync_last_check": existing.get("local_sync_last_check", 0.0),
                "local_sync_next_check": existing.get("local_sync_next_check", 0.0),
                "local_sync_status": existing.get("local_sync_status", "Ready"),
                "local_sync_error": existing.get("local_sync_error", ""),
            }
            repo_state[repo.id] = existing
        else:
            existing.setdefault("times", schedule_for_day(repo, date_key))
            existing.setdefault("done", [])
            existing.setdefault("last_error", "")
            existing.setdefault("local_sync_last_check", 0.0)
            existing.setdefault("local_sync_next_check", 0.0)
            existing.setdefault("local_sync_status", "Ready")
            existing.setdefault("local_sync_error", "")

    save_state(state)
    return state


def mark_progress(repo_id: str, completed_count: int, error: str = "") -> None:
    config = load_config()
    state = ensure_today_state(config)
    item = state.setdefault("repos", {}).setdefault(repo_id, {"times": [], "done": [], "last_error": ""})
    times = item.get("times", [])
    count = max(0, min(int(completed_count), len(times)))
    item["done"] = [str(index) for index in range(1, count + 1)]
    item["last_error"] = error
    repo = next((candidate for candidate in config.repositories if candidate.id == repo_id), None)
    if repo is not None and not error:
        _mark_span_date_complete(state, repo, str(state.get("date", "")))
    save_state(state)


def pending_span_completions(config: AppConfig | None = None) -> list[SpanCompletion]:
    config = config or load_config()
    state = ensure_today_state(config)
    events: list[SpanCompletion] = []
    for repo in config.repositories:
        dates = sorted(date_key for date_key, times in repo.calendar_plan.items() if times)
        if not dates:
            continue
        _mark_span_date_complete(state, repo, str(state.get("date", "")))
        span = state.get("spans", {}).get(repo.id, {})
        completed_dates = set(span.get("completed_dates", []))
        if span.get("notified") or not set(dates).issubset(completed_dates):
            continue
        events.append(
            SpanCompletion(
                repo_id=repo.id,
                repo_name=repo.name or parse_repo_url(repo.repo_url)[1],
                fingerprint=str(span.get("fingerprint", "")),
                start_date=dates[0],
                end_date=dates[-1],
                days=len(dates),
                total_pulses=sum(len(repo.calendar_plan[date_key]) for date_key in dates),
            )
        )
    save_state(state)
    return events


def mark_span_notified(event: SpanCompletion) -> bool:
    config = load_config()
    repo = next((candidate for candidate in config.repositories if candidate.id == event.repo_id), None)
    if repo is None:
        return False
    state = ensure_today_state(config)
    span = state.get("spans", {}).get(event.repo_id, {})
    if span.get("fingerprint") != event.fingerprint or span.get("notified"):
        return False
    span["notified"] = True
    save_state(state)
    append_history(
        {
            "repo_id": repo.id,
            "repo_name": event.repo_name,
            "repo_url": repo.repo_url,
            "pulse": f"{event.total_pulses}/{event.total_pulses}",
            "commit": "",
            "branch": repo.branch,
            "status": "Complete",
            "mode": "Automatic span",
            "message": f"Automatic span completed: {event.total_pulses} pulses across {event.days} days.",
            "commit_url": "",
        }
    )
    return True


def next_scheduled_time(repo: RepoConfig, state: dict | None = None) -> str:
    state = state or ensure_today_state()
    item = state.get("repos", {}).get(repo.id, {})
    done = set(item.get("done", []))
    for index, value in enumerate(item.get("times", []), start=1):
        if str(index) not in done:
            return value
    return "Complete"


@contextmanager
def repo_lock(repo_id: str) -> Iterator[None]:
    ensure_dirs()
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCK_DIR / f"{repo_id}.lock"
    fd: int | None = None
    try:
        # If a stale lock is older than ten minutes, recover it.
        if path.exists():
            try:
                if time.time() - path.stat().st_mtime > 600:
                    path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
        except FileExistsError as exc:
            raise RuntimeError("This repository is already being processed by GitPulse.") from exc
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _record_success(repo: RepoConfig, result: PulseResult, mode: str) -> None:
    append_history(
        {
            "repo_id": repo.id,
            "repo_name": result.repo_name,
            "repo_url": repo.repo_url,
            "pulse": f"{result.count}/{result.target}",
            "commit": result.commit_hash,
            "branch": result.branch,
            "status": "Pushed" if result.created else "Complete",
            "mode": mode,
            "message": result.message,
            "commit_url": repo_commit_url(repo.repo_url, result.commit_hash) if result.commit_hash else "",
        }
    )


def _record_error(repo: RepoConfig, mode: str, exc: Exception) -> None:
    append_history(
        {
            "repo_id": repo.id,
            "repo_name": repo.name or repo.repo_url,
            "repo_url": repo.repo_url,
            "pulse": "—",
            "commit": "",
            "branch": repo.branch,
            "status": "Failed",
            "mode": mode,
            "message": str(exc),
            "commit_url": "",
        }
    )
    write_log(f"{repo.name or repo.repo_url}: {mode} failed: {exc}")


def run_one_pulse(repo: RepoConfig, mode: str = "Manual") -> PulseResult:
    errors = validate_repo(repo)
    if errors:
        raise ValueError("\n".join(errors))
    service = GitService()
    with repo_lock(repo.id):
        try:
            result = service.pulse(repo, push=True)
            _record_success(repo, result, mode)
            mark_progress(repo.id, result.count)
            write_log(f"{result.repo_name}: pulse {result.count}/{result.target} {result.commit_hash or result.message}")
            return result
        except Exception as exc:
            _record_error(repo, mode, exc)
            state = ensure_today_state()
            current_done = len(state.get("repos", {}).get(repo.id, {}).get("done", []))
            mark_progress(repo.id, current_done, str(exc))
            raise


def run_complete_today(repo: RepoConfig, mode: str = "Complete today") -> list[PulseResult]:
    errors = validate_repo(repo)
    if errors:
        raise ValueError("\n".join(errors))
    service = GitService()
    with repo_lock(repo.id):
        try:
            results = service.complete_today(repo)
            if not results:
                target = pulse_target_for_date(repo, today_key())
                mark_progress(repo.id, target)
                append_history(
                    {
                        "repo_id": repo.id,
                        "repo_name": repo.name or repo.repo_url,
                        "repo_url": repo.repo_url,
                        "pulse": f"{target}/{target}",
                        "commit": "",
                        "branch": repo.branch,
                        "status": "Complete",
                        "mode": mode,
                        "message": "Today's target was already complete.",
                        "commit_url": "",
                    }
                )
                return []
            for result in results:
                _record_success(repo, result, mode)
            mark_progress(repo.id, results[-1].count)
            write_log(f"{results[-1].repo_name}: completed {len(results)} remaining pulse(s) for today.")
            return results
        except Exception as exc:
            _record_error(repo, mode, exc)
            raise


def complete_all_enabled(config: AppConfig | None = None) -> dict[str, str]:
    config = config or load_config()
    repos = [repo for repo in config.repositories if repo.enabled]
    results: dict[str, str] = {}

    def task(repo: RepoConfig) -> tuple[str, str]:
        try:
            created = run_complete_today(repo, mode="Complete all")
            return repo.id, f"Created {len(created)} commit(s)."
        except Exception as exc:
            return repo.id, f"Failed: {exc}"

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(repos)))) as pool:
        for repo_id, message in pool.map(task, repos):
            results[repo_id] = message
    return results


def _process_scheduled_repo(repo: RepoConfig, due_index: int) -> None:
    try:
        result = run_one_pulse(repo, mode="Scheduled")
        # State is based on actual count; this also catches manual progress.
        mark_progress(repo.id, result.count)
    except Exception as exc:
        write_log(f"Scheduled pulse failed for {repo.name or repo.repo_url}: {exc}")


def _record_local_sync(repo: RepoConfig, result: LocalSyncResult, mode: str) -> None:
    if not result.pushed:
        return
    append_history(
        {
            "repo_id": repo.id,
            "repo_name": result.repo_name,
            "repo_url": repo.repo_url,
            "pulse": "Local sync",
            "commit": result.commit_hash,
            "branch": result.branch,
            "status": "Pushed",
            "mode": mode,
            "message": result.message,
            "commit_url": repo_commit_url(repo.repo_url, result.commit_hash) if result.commit_hash else "",
        }
    )


def run_local_sync(repo: RepoConfig, mode: str = "Hourly local sync") -> LocalSyncResult:
    errors = [*validate_repo(repo), *validate_local_sync(repo)]
    if errors:
        raise ValueError("\n".join(errors))
    if not repo.local_sync_enabled:
        raise ValueError("Hourly local sync is not enabled for this repository.")
    with repo_lock(repo.id):
        try:
            result = GitService().sync_staged_changes(repo, push=True)
            _record_local_sync(repo, result, mode)
            if result.pushed:
                write_log(f"{result.repo_name}: hourly local sync pushed {result.commit_hash}.")
            state = ensure_today_state(load_config())
            item = state.setdefault("repos", {}).setdefault(repo.id, {})
            completed_at = time.time()
            item["local_sync_last_check"] = completed_at
            item["local_sync_next_check"] = completed_at + LOCAL_SYNC_INTERVAL_SECONDS
            now = datetime.now().astimezone()
            display_time = format_hhmm_12(now.hour * 60 + now.minute)
            item["local_sync_status"] = f"Synced at {display_time}" if result.pushed else "Ready · nothing staged"
            item["local_sync_error"] = ""
            save_state(state)
            return result
        except Exception as exc:
            _record_error(repo, mode, exc)
            state = ensure_today_state(load_config())
            failed_at = time.time()
            item = state.setdefault("repos", {}).setdefault(repo.id, {})
            item["local_sync_last_check"] = failed_at
            item["local_sync_next_check"] = failed_at + LOCAL_SYNC_RETRY_SECONDS
            item["local_sync_status"] = "Needs attention · retry in 5 min"
            item["local_sync_error"] = str(exc)
            save_state(state)
            raise


def _process_local_sync_repo(repo: RepoConfig) -> str:
    try:
        result = run_local_sync(repo)
        if result.pushed:
            now = datetime.now().astimezone()
            return f"Synced at {format_hhmm_12(now.hour * 60 + now.minute)}"
        return "Ready · nothing staged"
    except Exception as exc:
        write_log(f"Hourly local sync failed for {repo.name or repo.repo_url}: {exc}")
        return "Needs attention · retry in 5 min"


def local_sync_is_due(last_check: float, now_timestamp: float, next_check: float = 0.0) -> bool:
    """Return true at the planned time, with legacy hourly-state support."""
    if next_check > 0:
        return now_timestamp >= next_check
    return last_check <= 0 or now_timestamp - last_check >= LOCAL_SYNC_INTERVAL_SECONDS


def claim_local_sync_check(repo_id: str) -> None:
    """Reserve an immediate UI-triggered check so the worker cannot race it."""
    state = ensure_today_state(load_config())
    item = state.setdefault("repos", {}).setdefault(repo_id, {})
    claimed_at = time.time()
    item["local_sync_last_check"] = claimed_at
    item["local_sync_next_check"] = claimed_at + LOCAL_SYNC_INTERVAL_SECONDS
    item["local_sync_status"] = "Checking staged changes…"
    save_state(state)


def queue_all_local_sync_checks() -> None:
    """Make every opted-in repository due after replacing an old worker."""
    config = load_config()
    enabled_ids = {repo.id for repo in config.repositories if repo.local_sync_enabled}
    if not enabled_ids:
        return
    state = ensure_today_state(config)
    for repo_id in enabled_ids:
        item = state.setdefault("repos", {}).setdefault(repo_id, {})
        item["local_sync_last_check"] = 0.0
        item["local_sync_next_check"] = 0.0
        item["local_sync_status"] = "Ready · current worker check pending"
        item["local_sync_error"] = ""
    save_state(state)


def worker_tick() -> list[SpanCompletion]:
    config = load_config()
    candidates = [repo for repo in config.repositories if repo.enabled or repo.local_sync_enabled]
    if not candidates:
        return pending_span_completions(config)

    state = ensure_today_state(config)
    now = datetime.now().astimezone()
    current_minute = now.hour * 60 + now.minute
    due: list[tuple[RepoConfig, int]] = []
    local_due: list[RepoConfig] = []
    now_timestamp = now.timestamp()

    for repo in candidates:
        if repo.local_sync_enabled:
            item = state.get("repos", {}).get(repo.id, {})
            try:
                last_check = float(item.get("local_sync_last_check", 0.0) or 0.0)
            except (TypeError, ValueError):
                last_check = 0.0
            try:
                next_check = float(item.get("local_sync_next_check", 0.0) or 0.0)
            except (TypeError, ValueError):
                next_check = 0.0
            if local_sync_is_due(last_check, now_timestamp, next_check):
                # Claim the check before starting work so the 25-second worker
                # tick cannot schedule the same repository twice.
                item["local_sync_last_check"] = now_timestamp
                item["local_sync_next_check"] = now_timestamp + LOCAL_SYNC_INTERVAL_SECONDS
                item["local_sync_status"] = "Checking staged changes…"
                local_due.append(repo)

        if not repo.enabled or validate_repo(repo):
            continue
        planned_today = planned_times_for(repo, str(state.get("date", "")))
        if planned_today:
            start = parse_hhmm(planned_today[0])
            end = 23 * 60 + 59
        else:
            start = parse_hhmm(repo.start_time)
            end = parse_hhmm(repo.end_time)
        if not (start <= current_minute <= end):
            continue

        item = state.get("repos", {}).get(repo.id, {})
        done = set(item.get("done", []))
        for index, hhmm in enumerate(item.get("times", []), start=1):
            if str(index) in done:
                continue
            if current_minute >= parse_hhmm(hhmm):
                due.append((repo, index))
            # Process at most one overdue slot per repo per tick.
            break

    if local_due:
        save_state(state)

    if due:
        with ThreadPoolExecutor(max_workers=min(4, len(due))) as pool:
            futures = [pool.submit(_process_scheduled_repo, repo, index) for repo, index in due]
            for future in futures:
                try:
                    future.result()
                except Exception:
                    pass

    # Run local sync after scheduled pulses so the per-repository safety lock
    # never makes the two modes race each other.
    if local_due:
        with ThreadPoolExecutor(max_workers=min(4, len(local_due))) as pool:
            results = list(pool.map(_process_local_sync_repo, local_due))
        latest = ensure_today_state(load_config())
        for repo, message in zip(local_due, results):
            latest.setdefault("repos", {}).setdefault(repo.id, {})["local_sync_status"] = message
        save_state(latest)
    return pending_span_completions(config)


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            text=True,
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        )
        for row in csv.reader(proc.stdout.splitlines()):
            if len(row) >= 2 and row[1].strip() == str(pid):
                return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def worker_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        if process_alive(pid):
            return pid
    except Exception:
        pass
    try:
        PID_FILE.unlink(missing_ok=True)
        WORKER_META_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    return None


def worker_is_current(pid: int | None = None) -> bool:
    active_pid = pid or worker_pid()
    if not active_pid:
        return False
    try:
        metadata = json.loads(WORKER_META_FILE.read_text(encoding="utf-8"))
        return int(metadata.get("pid", 0)) == active_pid and metadata.get("protocol") == WORKER_PROTOCOL
    except Exception:
        return False


def _write_pid() -> None:
    ensure_dirs()
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    WORKER_META_FILE.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "protocol": WORKER_PROTOCOL,
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _clear_pid() -> None:
    try:
        if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_FILE.unlink(missing_ok=True)
            WORKER_META_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def app_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve())]
    script = Path(sys.argv[0]).resolve()
    python = Path(sys.executable).resolve()
    if os.name == "nt":
        pythonw = python.with_name("pythonw.exe")
        if pythonw.exists():
            python = pythonw
    return [str(python), str(script)]


def worker_command() -> list[str]:
    return [*app_command(), "--worker"]


def _open_main_window() -> None:
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "cwd": str(Path(sys.argv[0]).resolve().parent),
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(app_command(), **kwargs)


def worker_main() -> None:
    if worker_pid():
        return
    _write_pid()
    stop_event = threading.Event()
    tray = BackgroundTray(on_open=_open_main_window, on_exit=stop_event.set, on_error=write_log)
    try:
        config = load_config()
        if config.start_with_windows:
            set_start_with_windows(True)
        tray_ready = tray.start()
        if os.name == "nt" and not tray_ready:
            write_log("Background worker started, but the Windows tray icon could not be created.")
        else:
            write_log("Background worker started with notification-area icon.")

        while not stop_event.is_set():
            try:
                completions = worker_tick()
                for event in completions:
                    delivered = tray.notify(
                        "GitPulse span complete",
                        f"{event.repo_name}: {event.total_pulses} pulses completed across {event.days} days.",
                    )
                    if delivered and mark_span_notified(event):
                        write_log(
                            f"{event.repo_name}: automatic span complete "
                            f"({event.total_pulses} pulses across {event.days} days)."
                        )
            except Exception as exc:
                write_log(f"Worker tick failed: {exc}")
            stop_event.wait(25)
    except KeyboardInterrupt:
        pass
    finally:
        tray.stop()
        _clear_pid()
        write_log("Background worker stopped.")


def launch_worker() -> None:
    existing = worker_pid()
    if existing and worker_is_current(existing):
        return
    if existing:
        write_log("Replacing an outdated background worker.")
        stop_worker()
        queue_all_local_sync_checks()
    command = worker_command()
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "cwd": str(Path(sys.argv[0]).resolve().parent),
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    for _ in range(50):
        time.sleep(0.1)
        active = worker_pid()
        if active and worker_is_current(active):
            return
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(
                f"The background worker exited during startup (code {exit_code}). "
                "Open startup-error.log for details."
            )
    raise RuntimeError("The background worker did not come online within five seconds.")


def stop_worker() -> None:
    pid = worker_pid()
    if not pid:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(0.3)
    try:
        PID_FILE.unlink(missing_ok=True)
        WORKER_META_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    write_log("Background worker stopped from UI.")


def startup_file() -> Path | None:
    if os.name != "nt":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "GitPulse.vbs"


def set_start_with_windows(enabled: bool) -> None:
    path = startup_file()
    if not path:
        return
    legacy_path = path.with_suffix(".cmd")
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        command = subprocess.list2cmdline(worker_command())
        escaped = command.replace('"', '""')
        path.write_text(
            'Set shell = CreateObject("WScript.Shell")\n'
            f'shell.Run "{escaped}", 0, False\n',
            encoding="utf-8",
        )
        legacy_path.unlink(missing_ok=True)
    else:
        path.unlink(missing_ok=True)
        legacy_path.unlink(missing_ok=True)
