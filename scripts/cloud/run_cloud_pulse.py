"""Headless GitPulse runner for GitHub Actions.

Runs inside the caller repository. It only stages the configured pulse file,
creates non-empty commits, and pushes the currently checked-out branch.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gitpulse.cloud_schedule import due_pulse_count, random_schedule_times


def run_git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def current_branch(repo_dir: Path) -> str:
    return run_git(["branch", "--show-current"], repo_dir).stdout.strip() or "main"


def sync_remote(repo_dir: Path, branch: str) -> None:
    run_git(["pull", "--rebase", "origin", branch], repo_dir)


def read_daily_state(file_path: Path, date_key: str, minimum_target: int) -> tuple[list[str], int, int, int | None]:
    lines = file_path.read_text(encoding="utf-8").splitlines() if file_path.exists() else []
    pattern = re.compile(rf"^{re.escape(date_key)} \| GitPulse (\d+)/(\d+)\s*$")

    current = 0
    display_target = minimum_target
    line_index: int | None = None

    for index, line in enumerate(lines):
        match = pattern.match(line.strip())
        if match:
            current = int(match.group(1))
            display_target = max(minimum_target, int(match.group(2)))
            line_index = index
            break

    return lines, current, display_target, line_index


def create_one_pulse(
    repo_dir: Path,
    target_file: str,
    date_key: str,
    next_count: int,
    minimum_target: int,
    branch: str,
) -> bool:
    file_path = repo_dir / target_file
    lines, current, display_target, line_index = read_daily_state(file_path, date_key, minimum_target)

    if current >= next_count or current >= minimum_target:
        return False

    new_line = f"{date_key} | GitPulse {next_count:02d}/{display_target:02d}"
    if line_index is None:
        lines.append(new_line)
    else:
        lines[line_index] = new_line

    file_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    run_git(["add", "--", target_file], repo_dir)
    staged = [
        value.strip()
        for value in run_git(["diff", "--cached", "--name-only"], repo_dir).stdout.splitlines()
        if value.strip()
    ]
    if staged != [target_file]:
        run_git(["reset"], repo_dir, check=False)
        raise RuntimeError(f"Safety check failed: cloud pulse may commit only {target_file}.")

    unchanged = run_git(["diff", "--cached", "--quiet"], repo_dir, check=False)
    if unchanged.returncode == 0:
        raise RuntimeError("No file change was produced, so no cloud pulse commit was created.")

    run_git(["commit", "-m", "Update", "--", target_file], repo_dir)
    run_git(["push", "origin", f"HEAD:{branch}"], repo_dir)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one GitPulse cloud scheduling check.")
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--repo-id", default="cloud")
    parser.add_argument("--repo-url", default="")
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--start-time", default="10:00")
    parser.add_argument("--end-time", default="21:59")
    parser.add_argument("--timezone", default="Asia/Karachi")
    parser.add_argument("--target-file", default="gitpulse.txt")
    parser.add_argument("--commit-name", default="GitPulse")
    parser.add_argument("--commit-email", required=True)
    parser.add_argument("--max-wait-seconds", type=int, default=1860)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    repo_dir = Path(args.repo_dir).resolve()
    if not (repo_dir / ".git").exists():
        raise RuntimeError(f"Not a Git repository: {repo_dir}")

    timezone = ZoneInfo(args.timezone)
    branch = current_branch(repo_dir)
    repo_url = args.repo_url.strip()
    if not repo_url:
        repo_url = run_git(["remote", "get-url", "origin"], repo_dir).stdout.strip()

    run_git(["config", "user.name", args.commit_name], repo_dir)
    run_git(["config", "user.email", args.commit_email], repo_dir)

    now = datetime.now(timezone)
    date_key = now.date().isoformat()
    selected_times = random_schedule_times(
        date_key=date_key,
        repo_id=args.repo_id,
        repo_url=repo_url,
        count=args.count,
        start_time=args.start_time,
        end_time=args.end_time,
    )
    print(f"GitPulse plan for {date_key}: {', '.join(selected_times)} ({args.timezone})")

    sync_remote(repo_dir, branch)
    target_path = repo_dir / args.target_file
    _, current, _, _ = read_daily_state(target_path, date_key, args.count)
    if current >= args.count:
        print(f"Today's cloud pulse target is already complete ({current}/{args.count}).")
        return 0

    now = datetime.now(timezone)
    due_count = due_pulse_count(selected_times, now.strftime("%H:%M"))

    next_index = current
    if next_index < len(selected_times):
        hour, minute = (int(part) for part in selected_times[next_index].split(":", 1))
        next_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        wait_seconds = (next_time - now).total_seconds()

        if 0 < wait_seconds <= args.max_wait_seconds:
            print(
                f"Next random pulse is {selected_times[next_index]} ({args.timezone}); "
                f"waiting {int(wait_seconds)} seconds."
            )
            time.sleep(wait_seconds)
            now = datetime.now(timezone)
            due_count = due_pulse_count(selected_times, now.strftime("%H:%M"))

    if due_count <= current:
        print("No cloud pulse is due on this check.")
        return 0

    # Re-sync after a short wait so a desktop/manual pulse created meanwhile
    # is respected instead of duplicated.
    sync_remote(repo_dir, branch)
    _, current, _, _ = read_daily_state(target_path, date_key, args.count)

    target_count = min(args.count, due_count)
    while current < target_count:
        next_count = current + 1
        if not create_one_pulse(
            repo_dir=repo_dir,
            target_file=args.target_file,
            date_key=date_key,
            next_count=next_count,
            minimum_target=args.count,
            branch=branch,
        ):
            break
        current = next_count
        print(f"Created GitPulse cloud commit {current}/{args.count} for {date_key}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
