from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from greenpulse.git_service import GitService
from greenpulse.models import RepoConfig
from greenpulse.scheduler import schedule_for_day, validate_repo
from greenpulse.utils import parse_hhmm


class GreenPulseCoreTests(unittest.TestCase):
    def test_schedule_has_requested_unique_times_in_window(self) -> None:
        repo = RepoConfig(
            name="Test",
            repo_url="https://github.com/example/example",
            commit_email="dev@example.com",
            commits_per_day=20,
            start_time="10:00",
            end_time="23:59",
        )
        values = schedule_for_day(repo, "2026-08-13")
        self.assertEqual(len(values), 20)
        self.assertEqual(len(set(values)), 20)
        self.assertTrue(all(parse_hhmm("10:00") <= parse_hhmm(v) <= parse_hhmm("23:59") for v in values))

    def test_invalid_time_window_is_rejected(self) -> None:
        repo = RepoConfig(
            repo_url="https://github.com/example/example",
            commit_email="dev@example.com",
            commits_per_day=20,
            start_time="23:00",
            end_time="10:00",
        )
        self.assertTrue(validate_repo(repo))

    def test_commits_are_non_empty_and_only_touch_greenpulse_txt(self) -> None:
        service = GitService()
        with tempfile.TemporaryDirectory() as temp:
            repo_dir = Path(temp)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo_dir, check=True)
            (repo_dir / "README.md").write_text("# Example\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)

            repo = RepoConfig(
                name="Example",
                repo_url="https://github.com/example/example",
                commit_email="dev@example.com",
                commits_per_day=3,
            )
            results = []
            for count in range(1, 4):
                results.append(service._commit_one(repo, repo_dir, "main", count, 3))

            self.assertTrue(all(result.created for result in results))
            log_count = int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=repo_dir, text=True).strip())
            self.assertEqual(log_count, 4)
            final_line = (repo_dir / "greenpulse.txt").read_text(encoding="utf-8").strip()
            self.assertRegex(final_line, r"\d{4}-\d{2}-\d{2} \| GreenPulse 03/03")

            changed = subprocess.check_output(["git", "show", "--pretty=", "--name-only", "HEAD"], cwd=repo_dir, text=True).splitlines()
            self.assertEqual([line for line in changed if line], ["greenpulse.txt"])


if __name__ == "__main__":
    unittest.main()
