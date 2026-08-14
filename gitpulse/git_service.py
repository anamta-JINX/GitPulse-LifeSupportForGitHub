from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import RepoConfig
from .storage import REPOS_DIR, ensure_dirs, write_log
from .utils import normalize_remote, parse_repo_url, pulse_target_for_date, safe_repo_name, today_key

TARGET_FILE = "gitpulse.txt"
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


@dataclass
class LocalSyncResult:
    created: bool
    pushed: bool
    repo_id: str
    repo_name: str
    repo_url: str
    branch: str
    staged_files: tuple[str, ...] = ()
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
        write_log(f"Cloning {repo.repo_url} into GitPulse cache.")
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

            # Rebase any prior unpushed GitPulse commits on top of remote changes.
            self.run_git(["pull", "--rebase", "origin", branch], cwd=repo_dir, timeout=240)

            # If a previous network push failed, the GitPulse commits are still
            # safely present in this private cache. Retry them before creating
            # another pulse so progress cannot become stranded locally.
            ahead_proc = self.run_git(["rev-list", "--count", f"origin/{branch}..HEAD"], cwd=repo_dir, check=False)
            try:
                ahead = int((ahead_proc.stdout or "0").strip() or "0")
            except ValueError:
                ahead = 0
            if ahead > 0:
                write_log(f"Retrying {ahead} pending GitPulse commit(s) for {repo.name or repo.repo_url}.")
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

    def inspect_pulse_target(self, repo: RepoConfig) -> tuple[bool, str]:
        """Connect the private cache and report the managed pulse-file policy."""
        repo_dir, _branch = self.ensure_repo(repo, sync=True)
        if (repo_dir / TARGET_FILE).is_file():
            return True, "Connected. gitpulse.txt already exists and will be preserved."
        return False, "Connected. The first pulse will create gitpulse.txt; future pulses will update it."

    def _local_repo(self, repo: RepoConfig) -> tuple[Path, str]:
        raw_path = repo.local_path.strip()
        if not raw_path:
            raise RuntimeError("Choose the local repository folder for hourly sync.")
        selected = Path(raw_path).expanduser().resolve()
        if not selected.is_dir():
            raise RuntimeError("The hourly-sync folder does not exist or is not a directory.")

        top_proc = self.run_git(["rev-parse", "--show-toplevel"], cwd=selected, timeout=20)
        repo_dir = Path(top_proc.stdout.strip()).resolve()
        if selected != repo_dir:
            raise RuntimeError("Choose the repository root folder, not one of its subfolders.")

        remote = self.run_git(["remote", "get-url", "origin"], cwd=repo_dir, timeout=20).stdout.strip()
        if normalize_remote(remote) != normalize_remote(repo.repo_url):
            raise RuntimeError("The local folder's origin does not match this connected GitHub repository.")

        branch = self.run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repo_dir, check=False, timeout=20).stdout.strip()
        if not branch:
            raise RuntimeError("Hourly local sync cannot run while the local repository is in detached HEAD state.")
        if repo.branch.strip() and branch != repo.branch.strip():
            raise RuntimeError(f"The local repository is on '{branch}', but GitPulse is configured for '{repo.branch.strip()}'.")
        return repo_dir, branch

    def _staged_files(self, repo_dir: Path) -> tuple[str, ...]:
        raw = self.run_git(["diff", "--cached", "--name-only", "-z"], cwd=repo_dir, timeout=30).stdout
        return tuple(path for path in raw.split("\0") if path)

    def inspect_local_repository(self, repo: RepoConfig) -> tuple[Path, str, tuple[str, ...]]:
        """Validate a local-sync folder without changing it or using network."""
        repo_dir, branch = self._local_repo(repo)
        return repo_dir, branch, self._staged_files(repo_dir)

    def _prepare_local_identity(self, repo: RepoConfig, repo_dir: Path) -> None:
        owner, _name = parse_repo_url(repo.repo_url)
        current_name = self.run_git(["config", "user.name"], cwd=repo_dir, check=False, timeout=15).stdout.strip()
        current_email = self.run_git(["config", "user.email"], cwd=repo_dir, check=False, timeout=15).stdout.strip()
        if not current_name:
            self.run_git(["config", "user.name", owner], cwd=repo_dir, timeout=15)
        if not current_email:
            self.run_git(["config", "user.email", repo.commit_email.strip()], cwd=repo_dir, timeout=15)

    def _remote_branch_counts(self, repo_dir: Path, branch: str) -> tuple[int, int]:
        self.run_git(["fetch", "origin"], cwd=repo_dir, timeout=240)
        remote_ref = f"refs/remotes/origin/{branch}"
        if self.run_git(["show-ref", "--verify", remote_ref], cwd=repo_dir, check=False, timeout=20).returncode != 0:
            raise RuntimeError(f"Branch '{branch}' was not found on origin.")
        counts = self.run_git(["rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"], cwd=repo_dir, timeout=30).stdout.split()
        if len(counts) != 2:
            raise RuntimeError("GitPulse could not compare the local and GitHub branches.")
        return int(counts[0]), int(counts[1])

    def _integrate_remote_preserving_work(
        self,
        repo_dir: Path,
        branch: str,
        staged_files: tuple[str, ...],
        local_ahead: int,
    ) -> int:
        """Safely update or rebase a local branch without losing local work.

        The full staged/unstaged/untracked worktree is protected in a temporary
        stash. A behind-only branch fast-forwards; a diverged branch rebases its
        local commits. The exact index is restored before Hourly Sync commits.
        """
        worktree_dirty = bool(
            self.run_git(["status", "--porcelain"], cwd=repo_dir, timeout=30).stdout.strip()
        )
        stash_created = False
        if worktree_dirty:
            before = self.run_git(
                ["rev-parse", "--verify", "--quiet", "refs/stash"],
                cwd=repo_dir,
                check=False,
                timeout=20,
            ).stdout.strip()
            self.run_git(
                ["stash", "push", "--include-untracked", "-m", "Hourly sync safety backup"],
                cwd=repo_dir,
                timeout=120,
            )
            after = self.run_git(
                ["rev-parse", "--verify", "--quiet", "refs/stash"],
                cwd=repo_dir,
                check=False,
                timeout=20,
            ).stdout.strip()
            stash_created = bool(after and after != before)
            if not stash_created:
                raise RuntimeError("Hourly Sync could not protect the local worktree before updating it.")

        if local_ahead:
            updated = self.run_git(
                ["rebase", f"origin/{branch}"],
                cwd=repo_dir,
                check=False,
                timeout=240,
            )
            if updated.returncode != 0:
                details = (updated.stderr or updated.stdout or "Git reported a rebase conflict.").strip()
                self.run_git(["rebase", "--abort"], cwd=repo_dir, check=False, timeout=60)
                if stash_created:
                    restored = self.run_git(["stash", "pop", "--index"], cwd=repo_dir, check=False, timeout=120)
                    if restored.returncode != 0:
                        raise RuntimeError(
                            "The automatic rebase conflicted and Git could not restore the safety backup. "
                            "Your work remains in git stash."
                        )
                raise RuntimeError(
                    "The local and GitHub branches contain a real merge conflict. GitPulse aborted the "
                    f"automatic rebase and restored your work. Resolve it manually.\n\n{details}"
                )
        else:
            updated = self.run_git(
                ["merge", "--ff-only", f"origin/{branch}"],
                cwd=repo_dir,
                check=False,
                timeout=120,
            )
            if updated.returncode != 0:
                details = (updated.stderr or updated.stdout or "Git could not fast-forward the branch.").strip()
                if stash_created:
                    restored = self.run_git(["stash", "pop", "--index"], cwd=repo_dir, check=False, timeout=120)
                    if restored.returncode != 0:
                        raise RuntimeError(
                            "Git could not update the branch or restore the safety backup. "
                            "Your work remains in git stash."
                        )
                raise RuntimeError(f"Git could not fast-forward the local branch. Your work was restored.\n\n{details}")

        if stash_created:
            restored = self.run_git(
                ["stash", "pop", "--index"],
                cwd=repo_dir,
                check=False,
                timeout=120,
            )
            if restored.returncode != 0:
                raise RuntimeError(
                    "GitHub was fast-forwarded locally, but Git could not automatically restore "
                    "the saved worktree. The safety backup remains in git stash; no commit was made."
                )

        restored_staged = self._staged_files(repo_dir)
        if restored_staged != staged_files:
            raise RuntimeError(
                "Hourly Sync stopped because the staged-file selection changed while updating from GitHub. "
                "Review git status; no commit was made."
            )
        counts = self.run_git(
            ["rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"],
            cwd=repo_dir,
            timeout=30,
        ).stdout.split()
        if len(counts) != 2 or int(counts[1]) != 0:
            raise RuntimeError("Hourly Sync could not finish aligning the local and GitHub branches.")
        return int(counts[0])

    def _verify_local_branch_is_pushable(
        self,
        repo_dir: Path,
        branch: str,
        staged_files: tuple[str, ...] = (),
    ) -> int:
        ahead, behind = self._remote_branch_counts(repo_dir, branch)
        if behind:
            ahead = self._integrate_remote_preserving_work(repo_dir, branch, staged_files, ahead)
        return ahead

    @staticmethod
    def _pending_sync_ref(repo: RepoConfig) -> str:
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "-", repo.id) or "repository"
        return f"refs/gitpulse/hourly/{safe_id}"

    def _pending_sync_commit(self, repo: RepoConfig, repo_dir: Path) -> str:
        return self.run_git(
            ["rev-parse", "--verify", "--quiet", self._pending_sync_ref(repo)],
            cwd=repo_dir,
            check=False,
            timeout=20,
        ).stdout.strip()

    def _mark_pending_sync_commit(self, repo: RepoConfig, repo_dir: Path, commit_hash: str) -> None:
        self.run_git(["update-ref", self._pending_sync_ref(repo), commit_hash], cwd=repo_dir, timeout=20)

    def _clear_pending_sync_commit(self, repo: RepoConfig, repo_dir: Path) -> None:
        self.run_git(["update-ref", "-d", self._pending_sync_ref(repo)], cwd=repo_dir, check=False, timeout=20)

    def sync_staged_changes(self, repo: RepoConfig, push: bool = True) -> LocalSyncResult:
        """Commit exactly the user's staged index and optionally push it.

        GitPulse never runs ``git add``. Unstaged and untracked work remains
        uncommitted. If the branch is behind only because scheduled pulses
        changed ``gitpulse.txt``, a guarded stash/fast-forward/restore cycle
        keeps the local branch compatible with Hourly Sync.
        """
        repo_dir, branch = self._local_repo(repo)
        staged_files = self._staged_files(repo_dir)
        head_before_update = self.run_git(["rev-parse", "HEAD"], cwd=repo_dir, check=False, timeout=20).stdout.strip()
        pending_before_update = self._pending_sync_commit(repo, repo_dir)
        pending_was_head = bool(pending_before_update) and pending_before_update == head_before_update

        ahead = 0
        if push:
            ahead = self._verify_local_branch_is_pushable(repo_dir, branch, staged_files)
            if pending_was_head and ahead > 0:
                rebased_head = self.run_git(["rev-parse", "HEAD"], cwd=repo_dir, timeout=20).stdout.strip()
                self._mark_pending_sync_commit(repo, repo_dir, rebased_head)

        repo_name = repo.name or parse_repo_url(repo.repo_url)[1]
        head_hash = self.run_git(["rev-parse", "HEAD"], cwd=repo_dir, check=False, timeout=20).stdout.strip()
        pending_hash = self._pending_sync_commit(repo, repo_dir)
        pending_managed_commit = ahead > 0 and bool(pending_hash) and pending_hash == head_hash

        # Keep one-version compatibility with a failed push created before the
        # internal pending ref existed. New commits never use a branded subject.
        subject = self.run_git(["log", "-1", "--pretty=%s"], cwd=repo_dir, check=False, timeout=20).stdout.strip()
        legacy_prefix = "chore(" + "git" + "pulse): sync staged changes ["
        pending_managed_commit = pending_managed_commit or (ahead > 0 and subject.lower().startswith(legacy_prefix))

        if not staged_files:
            # Retry a GitPulse-created commit whose earlier push failed, but do
            # not push unrelated local-only commits in a normal no-op check.
            if push and pending_managed_commit:
                self.run_git(["push", "origin", f"HEAD:{branch}"], cwd=repo_dir, timeout=240)
                self._clear_pending_sync_commit(repo, repo_dir)
                commit_hash = self.run_git(["rev-parse", "--short=10", "HEAD"], cwd=repo_dir, timeout=20).stdout.strip()
                return LocalSyncResult(
                    created=False,
                    pushed=True,
                    repo_id=repo.id,
                    repo_name=repo_name,
                    repo_url=repo.repo_url,
                    branch=branch,
                    commit_hash=commit_hash,
                    message="Retried and pushed a pending hourly local-sync commit.",
                )
            return LocalSyncResult(
                created=False,
                pushed=False,
                repo_id=repo.id,
                repo_name=repo_name,
                repo_url=repo.repo_url,
                branch=branch,
                message="No staged changes were found.",
            )

        self._prepare_local_identity(repo, repo_dir)
        message = "Update files"
        self.run_git(["commit", "-m", message], cwd=repo_dir, timeout=180)
        full_commit_hash = self.run_git(["rev-parse", "HEAD"], cwd=repo_dir, timeout=20).stdout.strip()
        commit_hash = self.run_git(["rev-parse", "--short=10", "HEAD"], cwd=repo_dir, timeout=20).stdout.strip()
        if push:
            self._mark_pending_sync_commit(repo, repo_dir, full_commit_hash)
            self.run_git(["push", "origin", f"HEAD:{branch}"], cwd=repo_dir, timeout=240)
            self._clear_pending_sync_commit(repo, repo_dir)
        return LocalSyncResult(
            created=True,
            pushed=push,
            repo_id=repo.id,
            repo_name=repo_name,
            repo_url=repo.repo_url,
            branch=branch,
            staged_files=staged_files,
            commit_hash=commit_hash,
            message=message,
        )

    @staticmethod
    def read_daily_count(file_path: Path, date_key: str) -> int:
        if not file_path.exists():
            return 0
        pattern = re.compile(rf"^{re.escape(date_key)} \| GitPulse (\d+)/(\d+)\s*$")
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
        new_line = f"{date_key} | GitPulse {count:02d}/{target:02d}"
        lines: list[str] = []
        if file_path.exists():
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []

        replaced = False
        for index, line in enumerate(lines):
            if line.startswith(date_key + " | GitPulse "):
                lines[index] = new_line
                replaced = True
                break
        if not replaced:
            lines.append(new_line)
        file_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def _prepare_cache_for_pulse(self, repo_dir: Path) -> None:
        # The cache belongs exclusively to GitPulse. Recover safely from an
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
                "GitPulse's private cache contains an unexpected change. "
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
            raise RuntimeError("Safety check failed: GitPulse may commit only gitpulse.txt.")
        diff_check = self.run_git(["diff", "--cached", "--quiet"], cwd=repo_dir, check=False)
        if diff_check.returncode == 0:
            raise RuntimeError("No file change was produced, so no commit was created.")

    def _commit_one(self, repo: RepoConfig, repo_dir: Path, branch: str, next_count: int, target: int) -> PulseResult:
        date_key = today_key()
        pulse_file = repo_dir / TARGET_FILE
        self._prepare_cache_for_pulse(repo_dir)
        self.update_daily_line(pulse_file, date_key, next_count, target)
        self._stage_target_only(repo_dir)

        message = "Update"
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
        target = pulse_target_for_date(repo, date_key)
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
        target = pulse_target_for_date(repo, date_key)
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
