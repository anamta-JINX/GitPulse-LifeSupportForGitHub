from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import RepoConfig
from .storage import REPOS_DIR, ensure_dirs, write_log
from .utils import normalize_remote, parse_repo_url, safe_repo_name, today_key

TARGET_FILE = "greenpulse.txt"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass
class PulseResult:
    created: bool
    repo_id: str
    repo_name: str
    repo_url: str
    count: int
    target: int
    branch: str
    commit_hash: str = ""
    message: str = ""


class GitService:
    def __init__(self) -> None:
        ensure_dirs()

    def run_git(
        self,
        args: list[str],
        cwd: Path | None = None,
        check: bool = True,
        timeout: int = 180,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.setdefault("GIT_TERMINAL_PROMPT", "1")
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(cwd) if cwd else None,
                text=True,
                capture_output=True,
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Git is not installed or is not available in PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Git operation timed out.") from exc

        if check and proc.returncode != 0:
            details = (proc.stderr or proc.stdout or "Git command failed.").strip()
            raise RuntimeError(details)
        return proc

    def git_available(self) -> bool:
        try:
            return self.run_git(["--version"], check=False, timeout=10).returncode == 0
        except Exception:
            return False

    def cache_dir(self, repo: RepoConfig) -> Path:
        return REPOS_DIR / safe_repo_name(repo.repo_url, repo.id)

    def _cache_matches(self, repo_dir: Path, repo_url: str) -> bool:
        if not (repo_dir / ".git").exists():
            return False
        try:
            current = self.run_git(["remote", "get-url", "origin"], cwd=repo_dir, timeout=20).stdout.strip()
            return normalize_remote(current) == normalize_remote(repo_url)
        except Exception:
            return False

    def _clone(self, repo: RepoConfig, repo_dir: Path) -> None:
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        if repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)
        write_log(f"Cloning {repo.repo_url} into GreenPulse cache.")
        self.run_git(["clone", repo.repo_url.strip(), str(repo_dir)], timeout=360)

    def _remote_default_branch(self, repo_dir: Path) -> str:
        # A fresh clone usually knows its checked-out default branch already.
        current = self.run_git(["symbolic-ref", "--short", "HEAD"], cwd=repo_dir, check=False).stdout.strip()
        if current:
            return current

        proc = self.run_git(["ls-remote", "--symref", "origin", "HEAD"], cwd=repo_dir)
        for line in proc.stdout.splitlines():
            if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
                return line.split("refs/heads/", 1)[1].split("\t", 1)[0]
        return "main"

    def ensure_repo(self, repo: RepoConfig, sync: bool = True) -> tuple[Path, str]:
        parse_repo_url(repo.repo_url)
        repo_dir = self.cache_dir(repo)

        if repo_dir.exists() and not self._cache_matches(repo_dir, repo.repo_url):
            write_log(f"Repairing cache for {repo.name or repo.repo_url}: origin was missing or incorrect.")
            shutil.rmtree(repo_dir, ignore_errors=True)

        if not (repo_dir / ".git").exists():
            self._clone(repo, repo_dir)

        # Verify origin after clone. If anything is wrong, rebuild once.
        try:
            current = self.run_git(["remote", "get-url", "origin"], cwd=repo_dir, timeout=20).stdout.strip()
            if normalize_remote(current) != normalize_remote(repo.repo_url):
                raise RuntimeError("Cached origin does not match the selected repository.")
        except Exception:
            self._clone(repo, repo_dir)

        branch = repo.branch.strip() or self._remote_default_branch(repo_dir)

        owner, _ = parse_repo_url(repo.repo_url)
        self.run_git(["config", "user.name", owner], cwd=repo_dir)
        self.run_git(["config", "user.email", repo.commit_email.strip()], cwd=repo_dir)

        if sync:
            self.run_git(["fetch", "origin"], cwd=repo_dir, timeout=240)
            local_branch = self.run_git(["show-ref", "--verify", f"refs/heads/{branch}"], cwd=repo_dir, check=False).returncode == 0
            remote_branch = self.run_git(["show-ref", "--verify", f"refs/remotes/origin/{branch}"], cwd=repo_dir, check=False).returncode == 0
            if local_branch:
                self.run_git(["checkout", branch], cwd=repo_dir)
            elif remote_branch:
                self.run_git(["checkout", "-B", branch, f"origin/{branch}"], cwd=repo_dir)
            else:
                raise RuntimeError(f"Branch '{branch}' was not found on the selected repository.")

            # Rebase any prior unpushed GreenPulse commits on top of remote changes.
            self.run_git(["pull", "--rebase", "origin", branch], cwd=repo_dir, timeout=240)

            # If a previous network push failed, the GreenPulse commits are still
            # safely present in this private cache. Retry them before creating
            # another pulse so progress cannot become stranded locally.
            ahead_proc = self.run_git(["rev-list", "--count", f"origin/{branch}..HEAD"], cwd=repo_dir, check=False)
            try:
                ahead = int((ahead_proc.stdout or "0").strip() or "0")
            except ValueError:
                ahead = 0
            if ahead > 0:
                write_log(f"Retrying {ahead} pending GreenPulse commit(s) for {repo.name or repo.repo_url}.")
                self.run_git(["push", "origin", branch], cwd=repo_dir, timeout=240)

        return repo_dir, branch

    def test_connection(self, repo: RepoConfig) -> tuple[bool, str]:
        if not self.git_available():
            return False, "Git is not installed or is not available in PATH."
        try:
            parse_repo_url(repo.repo_url)
            proc = self.run_git(["ls-remote", repo.repo_url.strip(), "HEAD"], timeout=120)
            if not proc.stdout.strip():
                return False, "GitHub returned no repository HEAD. Check access and repository state."
            return True, "Repository access confirmed."
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def read_daily_count(file_path: Path, date_key: str) -> int:
        if not file_path.exists():
            return 0
        pattern = re.compile(rf"^{re.escape(date_key)} \| GreenPulse (\d+)/(\d+)\s*$")
        try:
            for line in file_path.read_text(encoding="utf-8").splitlines():
                match = pattern.match(line.strip())
                if match:
                    return int(match.group(1))
        except OSError:
            return 0
        return 0

    @staticmethod
    def update_daily_line(file_path: Path, date_key: str, count: int, target: int) -> None:
        new_line = f"{date_key} | GreenPulse {count:02d}/{target:02d}"
        lines: list[str] = []
        if file_path.exists():
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []

        replaced = False
        for index, line in enumerate(lines):
            if line.startswith(date_key + " | GreenPulse "):
                lines[index] = new_line
                replaced = True
                break
        if not replaced:
            lines.append(new_line)
        file_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def _prepare_cache_for_pulse(self, repo_dir: Path) -> None:
        # The cache belongs exclusively to GreenPulse. Recover safely from an
        # interrupted file write without touching the user's real local repo.
        status = self.run_git(["status", "--porcelain"], cwd=repo_dir).stdout.splitlines()
        if not status:
            return
        unexpected = []
        for line in status:
            path = line[3:].strip().strip('"') if len(line) >= 4 else ""
            if path != TARGET_FILE:
                unexpected.append(line)
        if unexpected:
            raise RuntimeError(
                "GreenPulse's private cache contains an unexpected change. "
                "No commit was made. Use Repair cache from the repository menu."
            )
        self.run_git(["restore", "--staged", "--worktree", "--", TARGET_FILE], cwd=repo_dir, check=False)
        target = repo_dir / TARGET_FILE
        # restore cannot remove an untracked target, so remove only that file if necessary.
        status_after = self.run_git(["status", "--porcelain", "--", TARGET_FILE], cwd=repo_dir).stdout.strip()
        if status_after.startswith("??") and target.exists():
            target.unlink(missing_ok=True)

    def _stage_target_only(self, repo_dir: Path) -> None:
        self.run_git(["add", "--", TARGET_FILE], cwd=repo_dir)
        changed = [
            item.strip()
            for item in self.run_git(["diff", "--cached", "--name-only"], cwd=repo_dir).stdout.splitlines()
            if item.strip()
        ]
        if changed != [TARGET_FILE]:
            self.run_git(["reset"], cwd=repo_dir, check=False)
            raise RuntimeError("Safety check failed: GreenPulse may commit only greenpulse.txt.")
        diff_check = self.run_git(["diff", "--cached", "--quiet"], cwd=repo_dir, check=False)
        if diff_check.returncode == 0:
            raise RuntimeError("No file change was produced, so no commit was created.")

    def _commit_one(self, repo: RepoConfig, repo_dir: Path, branch: str, next_count: int, target: int) -> PulseResult:
        date_key = today_key()
        pulse_file = repo_dir / TARGET_FILE
        self._prepare_cache_for_pulse(repo_dir)
        self.update_daily_line(pulse_file, date_key, next_count, target)
        self._stage_target_only(repo_dir)

        message = f"chore(greenpulse): pulse {next_count:02d}/{target:02d} [{date_key}]"
        self.run_git(["commit", "-m", message, "--", TARGET_FILE], cwd=repo_dir)
        commit_hash = self.run_git(["rev-parse", "--short=10", "HEAD"], cwd=repo_dir).stdout.strip()
        return PulseResult(
            created=True,
            repo_id=repo.id,
            repo_name=repo.name or parse_repo_url(repo.repo_url)[1],
            repo_url=repo.repo_url,
            count=next_count,
            target=target,
            branch=branch,
            commit_hash=commit_hash,
            message=message,
        )

    def pulse(self, repo: RepoConfig, push: bool = True) -> PulseResult:
        repo_dir, branch = self.ensure_repo(repo, sync=True)
        date_key = today_key()
        target = int(repo.commits_per_day)
        current = self.read_daily_count(repo_dir / TARGET_FILE, date_key)
        if current >= target:
            return PulseResult(
                created=False,
                repo_id=repo.id,
                repo_name=repo.name or parse_repo_url(repo.repo_url)[1],
                repo_url=repo.repo_url,
                count=current,
                target=target,
                branch=branch,
                message=f"Today's target is already complete ({current}/{target}).",
            )

        result = self._commit_one(repo, repo_dir, branch, current + 1, target)
        if push:
            self.run_git(["push", "origin", branch], cwd=repo_dir, timeout=240)
        return result

    def complete_today(self, repo: RepoConfig) -> list[PulseResult]:
        repo_dir, branch = self.ensure_repo(repo, sync=True)
        date_key = today_key()
        target = int(repo.commits_per_day)
        current = self.read_daily_count(repo_dir / TARGET_FILE, date_key)
        results: list[PulseResult] = []
        if current >= target:
            return results

        for next_count in range(current + 1, target + 1):
            results.append(self._commit_one(repo, repo_dir, branch, next_count, target))

        # Send all commits in a single network push while preserving each commit.
        self.run_git(["push", "origin", branch], cwd=repo_dir, timeout=300)
        return results

    def repair_cache(self, repo: RepoConfig) -> None:
        repo_dir = self.cache_dir(repo)
        if repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)
        self.ensure_repo(repo, sync=True)

    def remove_cache(self, repo: RepoConfig) -> None:
        repo_dir = self.cache_dir(repo)
        if repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)
