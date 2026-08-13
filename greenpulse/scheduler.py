from __future__ import annotations

import json
import os
import random
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .git_service import GitService, PulseResult
from .models import AppConfig, RepoConfig
from .storage import APP_DIR, PID_FILE, append_history, ensure_dirs, load_config, load_state, save_state, write_log
from .utils import format_hhmm, parse_hhmm, parse_repo_url, repo_commit_url, today_key

LOCK_DIR = APP_DIR / "locks"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


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


def schedule_for_day(repo: RepoConfig, date_key: str) -> list[str]:
    count = int(repo.commits_per_day)
    start = parse_hhmm(repo.start_time)
    end = parse_hhmm(repo.end_time)
    span = end - start + 1

    # Stable for the current day so restarting the app does not regenerate the
    # day's timetable. Different date/repository/settings => different times.
    seed = f"GreenPulse|{date_key}|{repo.id}|{repo.repo_url}|{count}|{repo.start_time}|{repo.end_time}"
    rng = random.Random(seed)

    values: list[int] = []
    for index in range(count):
        bucket_start = start + int(index * span / count)
        bucket_end = start + int((index + 1) * span / count) - 1
        bucket_end = max(bucket_start, min(bucket_end, end))
        values.append(rng.randint(bucket_start, bucket_end))
    return [format_hhmm(value) for value in sorted(values)]


def repo_fingerprint(repo: RepoConfig) -> str:
    return "|".join(
        [
            repo.repo_url.strip(),
            str(repo.commits_per_day),
            repo.start_time.strip(),
            repo.end_time.strip(),
            repo.branch.strip(),
            str(bool(repo.enabled)),
        ]
    )


def ensure_today_state(config: AppConfig | None = None) -> dict:
    ensure_dirs()
    config = config or load_config()
    state = load_state()
    date_key = today_key()
    if state.get("date") != date_key:
        state = {"date": date_key, "repos": {}}

    repo_state = state.setdefault("repos", {})
    active_ids = {repo.id for repo in config.repositories}
    for stale_id in list(repo_state):
        if stale_id not in active_ids:
            repo_state.pop(stale_id, None)

    for repo in config.repositories:
        fingerprint = repo_fingerprint(repo)
        existing = repo_state.get(repo.id, {})
        if existing.get("fingerprint") != fingerprint:
            existing = {
                "fingerprint": fingerprint,
                "times": schedule_for_day(repo, date_key),
                "done": [],
                "last_error": "",
            }
            repo_state[repo.id] = existing
        else:
            existing.setdefault("times", schedule_for_day(repo, date_key))
            existing.setdefault("done", [])
            existing.setdefault("last_error", "")

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
    save_state(state)


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
            raise RuntimeError("This repository is already being processed by GreenPulse.") from exc
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
                mark_progress(repo.id, int(repo.commits_per_day))
                append_history(
                    {
                        "repo_id": repo.id,
                        "repo_name": repo.name or repo.repo_url,
                        "repo_url": repo.repo_url,
                        "pulse": f"{repo.commits_per_day}/{repo.commits_per_day}",
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


def worker_tick() -> None:
    config = load_config()
    enabled = [repo for repo in config.repositories if repo.enabled and not validate_repo(repo)]
    if not enabled:
        return

    state = ensure_today_state(config)
    now = datetime.now().astimezone()
    current_minute = now.hour * 60 + now.minute
    due: list[tuple[RepoConfig, int]] = []

    for repo in enabled:
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

    if not due:
        return

    with ThreadPoolExecutor(max_workers=min(4, len(due))) as pool:
        futures = [pool.submit(_process_scheduled_repo, repo, index) for repo, index in due]
        for future in futures:
            try:
                future.result()
            except Exception:
                pass


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
        return str(pid) in proc.stdout and "No tasks are running" not in proc.stdout
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
    except OSError:
        pass
    return None


def _write_pid() -> None:
    ensure_dirs()
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def _clear_pid() -> None:
    try:
        if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def worker_main() -> None:
    if worker_pid():
        return
    _write_pid()
    write_log("Background worker started.")
    try:
        while True:
            try:
                worker_tick()
            except Exception as exc:
                write_log(f"Worker tick failed: {exc}")
            time.sleep(25)
    except KeyboardInterrupt:
        pass
    finally:
        _clear_pid()
        write_log("Background worker stopped.")


def worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve()), "--worker"]
    script = Path(sys.argv[0]).resolve()
    python = Path(sys.executable).resolve()
    if os.name == "nt":
        pythonw = python.with_name("pythonw.exe")
        if pythonw.exists():
            python = pythonw
    return [str(python), str(script), "--worker"]


def launch_worker() -> None:
    if worker_pid():
        return
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
    subprocess.Popen(command, **kwargs)
    for _ in range(30):
        time.sleep(0.1)
        if worker_pid():
            return


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
    except OSError:
        pass
    write_log("Background worker stopped from UI.")


def startup_file() -> Path | None:
    if os.name != "nt":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "GreenPulse.cmd"


def set_start_with_windows(enabled: bool) -> None:
    path = startup_file()
    if not path:
        return
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        command = subprocess.list2cmdline(worker_command())
        path.write_text(f"@echo off\nstart \"\" {command}\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
