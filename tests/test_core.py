from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

from gitpulse import __version__
from gitpulse import scheduler, storage
from gitpulse.git_service import GitService
from gitpulse.models import AppConfig, RepoConfig
from gitpulse.scheduler import generate_automatic_plan, generate_calendar_plan, local_sync_is_due, schedule_for_day, validate_local_sync, validate_repo
from gitpulse.utils import canonical_hhmm, format_hhmm_12, parse_hhmm, parse_repo_url, pulse_target_for_date


class GitPulseCoreTests(unittest.TestCase):
    def test_version_is_1_5_0(self) -> None:
        self.assertEqual(__version__, "1.5.0")

    def test_gui_launcher_contains_runnable_appended_archive(self) -> None:
        launcher = Path(__file__).resolve().parent.parent / "GitPulse.exe"
        self.assertEqual(launcher.read_bytes()[:2], b"MZ")
        with ZipFile(launcher) as archive:
            self.assertEqual(archive.namelist(), ["__main__.py"])
            main = archive.read("__main__.py").decode("utf-8")
        self.assertIn('root / "GitPulse.pyw"', main)

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

    def test_daily_schedule_is_stable_but_changes_by_date(self) -> None:
        repo = RepoConfig(
            id="stable-repo",
            repo_url="https://github.com/example/example",
            commit_email="dev@example.com",
            commits_per_day=12,
            start_time="09:00",
            end_time="21:00",
        )
        first = schedule_for_day(repo, "2026-08-13")
        self.assertEqual(first, schedule_for_day(repo, "2026-08-13"))
        self.assertNotEqual(first, schedule_for_day(repo, "2026-08-14"))

    def test_all_visible_times_support_am_pm(self) -> None:
        self.assertEqual(parse_hhmm("12:00 AM"), 0)
        self.assertEqual(parse_hhmm("12:00 PM"), 720)
        self.assertEqual(parse_hhmm("5:30 pm"), 1050)
        self.assertEqual(parse_hhmm("5 PM"), 1020)
        self.assertEqual(canonical_hhmm("5:30 PM"), "17:30")
        self.assertEqual(format_hhmm_12("00:05"), "12:05 AM")
        self.assertEqual(format_hhmm_12("17:30"), "5:30 PM")

    def test_calendar_generates_varied_schedule_for_thirty_days(self) -> None:
        repo = RepoConfig(
            id="calendar-repo",
            repo_url="https://github.com/example/calendar",
            commit_email="dev@example.com",
            commits_per_day=5,
            start_time="09:00",
            end_time="17:00",
        )
        plan = generate_calendar_plan(repo, "2026-08-14", 30, 5, "9:00 AM", "5:00 PM")

        self.assertEqual(len(plan), 30)
        self.assertTrue(all(len(times) == 5 and len(set(times)) == 5 for times in plan.values()))
        self.assertTrue(all(parse_hhmm("09:00") <= parse_hhmm(value) <= parse_hhmm("17:00") for times in plan.values() for value in times))
        self.assertEqual(len({tuple(times) for times in plan.values()}), 30)
        repo.calendar_plan = plan
        self.assertEqual(schedule_for_day(repo, "2026-08-20"), plan["2026-08-20"])
        self.assertEqual(pulse_target_for_date(repo, "2026-08-20"), 5)
        self.assertEqual(RepoConfig.from_dict(repo.to_dict()).calendar_plan, plan)

    def test_automatic_plan_varies_daily_counts_and_times_with_exact_total(self) -> None:
        repo = RepoConfig(
            id="automatic-repo",
            repo_url="https://github.com/example/automatic",
            commit_email="dev@example.com",
            commits_per_day=5,
            start_time="09:00",
            end_time="17:00",
        )
        plan = generate_automatic_plan(repo, "2026-08-14", 30, 180, "9:00 AM", "5:00 PM")
        counts = [len(times) for times in plan.values()]

        self.assertEqual(len(plan), 30)
        self.assertEqual(sum(counts), 180)
        self.assertGreater(len(set(counts)), 1)
        self.assertTrue(all(count >= 1 for count in counts))
        self.assertTrue(all(len(times) == len(set(times)) for times in plan.values()))
        self.assertTrue(
            all(
                parse_hhmm("09:00") <= parse_hhmm(value) <= parse_hhmm("17:00")
                for times in plan.values()
                for value in times
            )
        )
        self.assertEqual(plan, generate_automatic_plan(repo, "2026-08-14", 30, 180, "9:00 AM", "5:00 PM"))
        self.assertNotEqual(plan, generate_automatic_plan(repo, "2026-08-14", 30, 180, "9:00 AM", "5:00 PM", variant=1))
        repo.calendar_plan = plan
        selected_date = sorted(plan)[8]
        self.assertEqual(pulse_target_for_date(repo, selected_date), len(plan[selected_date]))
        self.assertEqual(schedule_for_day(repo, "2026-08-13"), [])
        self.assertEqual(pulse_target_for_date(repo, "2026-08-13"), 0)
        self.assertEqual(schedule_for_day(repo, "2026-09-30"), [])
        self.assertEqual(pulse_target_for_date(repo, "2026-09-30"), 0)

    def test_automatic_plan_rejects_an_impossible_total(self) -> None:
        repo = RepoConfig(id="automatic-limits", repo_url="https://github.com/example/automatic", commit_email="dev@example.com")
        with self.assertRaisesRegex(ValueError, "between 7"):
            generate_automatic_plan(repo, "2026-08-14", 7, 6, "10:00 AM", "5:00 PM")
        with self.assertRaisesRegex(ValueError, "between 2 and 4"):
            generate_automatic_plan(repo, "2026-08-14", 2, 5, "10:00 AM", "10:01 AM")

    def test_worker_honors_calendar_times_outside_default_window(self) -> None:
        repo = RepoConfig(
            id="calendar-worker",
            repo_url="https://github.com/example/calendar",
            commit_email="dev@example.com",
            commits_per_day=1,
            start_time="10:00",
            end_time="11:00",
            calendar_plan={"2026-08-14": ["20:00"]},
        )
        config = AppConfig(repositories=[repo])
        state = {
            "date": "2026-08-14",
            "repos": {repo.id: {"times": ["20:00"], "done": [], "local_sync_last_check": 0}},
        }

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2026, 8, 14, 20, 0)
                return value.replace(tzinfo=tz) if tz else value

        with (
            patch.object(scheduler, "datetime", FixedDateTime),
            patch.object(scheduler, "load_config", return_value=config),
            patch.object(scheduler, "ensure_today_state", return_value=state),
            patch.object(scheduler, "_process_scheduled_repo") as process,
        ):
            scheduler.worker_tick()
        process.assert_called_once_with(repo, 1)

    def test_only_direct_github_repository_urls_are_accepted(self) -> None:
        self.assertEqual(parse_repo_url("git@github.com:anamta-JINX/GitPulse.git"), ("anamta-JINX", "GitPulse"))
        with self.assertRaises(ValueError):
            parse_repo_url("https://example.com/anamta-JINX/GitPulse")
        with self.assertRaises(ValueError):
            parse_repo_url("https://github.com/anamta-JINX/GitPulse/issues")

    def test_legacy_single_repo_config_is_migrated(self) -> None:
        config = AppConfig.from_dict(
            {
                "repo_url": "https://github.com/example/legacy",
                "commit_email": "dev@example.com",
                "commits_per_day": 7,
            }
        )
        self.assertEqual(len(config.repositories), 1)
        self.assertEqual(config.repositories[0].name, "legacy")
        self.assertEqual(config.repositories[0].commits_per_day, 7)
        self.assertFalse(config.repositories[0].local_sync_enabled)

    def test_hourly_local_sync_due_window(self) -> None:
        self.assertTrue(local_sync_is_due(0, 100))
        self.assertFalse(local_sync_is_due(100, 3699))
        self.assertTrue(local_sync_is_due(100, 3700))
        self.assertFalse(local_sync_is_due(100, 4999, 5000))
        self.assertTrue(local_sync_is_due(100, 5000, 5000))

    def test_worker_runs_hourly_sync_even_when_pulses_are_paused(self) -> None:
        repo = RepoConfig(
            id="local-only",
            repo_url="https://github.com/example/example",
            commit_email="dev@example.com",
            enabled=False,
            local_sync_enabled=True,
            local_path="C:/example",
        )
        config = AppConfig(repositories=[repo])
        state = {"date": "2026-08-14", "repos": {repo.id: {"local_sync_last_check": 0, "local_sync_status": "Ready", "times": [], "done": []}}}
        with patch.object(scheduler, "load_config", return_value=config), patch.object(scheduler, "ensure_today_state", return_value=state), patch.object(scheduler, "save_state"), patch.object(scheduler, "_process_local_sync_repo", return_value="Ready · nothing staged") as process:
            scheduler.worker_tick()
        process.assert_called_once_with(repo)

    def test_worker_start_failure_is_reported(self) -> None:
        process = Mock()
        process.poll.return_value = 1
        with (
            patch.object(scheduler, "worker_pid", return_value=None),
            patch.object(scheduler, "worker_command", return_value=["python", "GitPulse.pyw", "--worker"]),
            patch.object(scheduler.subprocess, "Popen", return_value=process),
            patch.object(scheduler.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "exited during startup"):
                scheduler.launch_worker()

    def test_outdated_worker_is_replaced_before_launch(self) -> None:
        process = Mock()
        process.poll.return_value = None
        with (
            patch.object(scheduler, "worker_pid", side_effect=[111, 222]),
            patch.object(scheduler, "worker_is_current", side_effect=[False, True]),
            patch.object(scheduler, "stop_worker") as stop,
            patch.object(scheduler, "worker_command", return_value=["python", "GitPulse.pyw", "--worker"]),
            patch.object(scheduler.subprocess, "Popen", return_value=process),
            patch.object(scheduler.time, "sleep"),
        ):
            scheduler.launch_worker()
        stop.assert_called_once_with()

    def test_worker_writes_current_build_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pid_file = root / "worker.pid"
            meta_file = root / "worker.json"
            with (
                patch.object(scheduler, "PID_FILE", pid_file),
                patch.object(scheduler, "WORKER_META_FILE", meta_file),
                patch.object(scheduler, "ensure_dirs"),
            ):
                scheduler._write_pid()
                metadata = json.loads(meta_file.read_text(encoding="utf-8"))
                self.assertEqual(metadata["protocol"], scheduler.WORKER_PROTOCOL)
                self.assertTrue(scheduler.worker_is_current(int(pid_file.read_text(encoding="utf-8"))))

    def test_dashboard_includes_calendar_and_visible_sync_error(self) -> None:
        source = (Path(__file__).resolve().parent.parent / "gitpulse" / "ui.py").read_text(encoding="utf-8")
        self.assertIn('GPButton(actions, "Pulse span"', source)
        self.assertIn('format_hhmm_12(repo.start_time)', source)
        self.assertIn('Hourly Sync issue:', source)
        self.assertIn('def fail(error_message: str = details)', source)

    def test_span_calendar_is_scrollable_and_has_no_per_day_input(self) -> None:
        source = (Path(__file__).resolve().parent.parent / "gitpulse" / "ui.py").read_text(encoding="utf-8")
        planner = source.split("class CalendarPlanDialog", 1)[1].split("class LocalSyncDialog", 1)[0]
        self.assertIn('"Pulses for full span"', planner)
        self.assertNotIn('"Pulses / day"', planner)
        self.assertIn("calendar_canvas", planner)
        self.assertIn("yscrollcommand=calendar_scroll.set", planner)
        self.assertIn('"Save & start"', planner)
        self.assertIn("screen_height - 100", planner)

    def test_completed_span_emits_one_notification_event(self) -> None:
        repo = RepoConfig(
            id="span-repo",
            name="Example",
            repo_url="https://github.com/example/span",
            commit_email="dev@example.com",
            calendar_plan={"2026-08-14": ["10:00"], "2026-08-15": ["10:00", "11:00"]},
        )
        config = AppConfig(repositories=[repo])
        state = {
            "date": "2026-08-15",
            "repos": {repo.id: {"times": ["10:00", "11:00"], "done": ["1", "2"]}},
            "spans": {},
        }
        scheduler._sync_span_state(state, config)
        state["spans"][repo.id]["completed_dates"] = ["2026-08-14"]

        with patch.object(scheduler, "ensure_today_state", return_value=state), patch.object(scheduler, "save_state"):
            events = scheduler.pending_span_completions(config)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].total_pulses, 3)
        self.assertEqual(events[0].days, 2)

        with (
            patch.object(scheduler, "load_config", return_value=config),
            patch.object(scheduler, "ensure_today_state", return_value=state),
            patch.object(scheduler, "save_state"),
            patch.object(scheduler, "append_history") as history,
        ):
            self.assertTrue(scheduler.mark_span_notified(events[0]))
            self.assertFalse(scheduler.mark_span_notified(events[0]))
        self.assertTrue(state["spans"][repo.id]["notified"])
        history.assert_called_once()

    def test_day_rollover_preserves_completed_span_dates(self) -> None:
        repo = RepoConfig(
            id="rollover-span",
            repo_url="https://github.com/example/span",
            commit_email="dev@example.com",
            calendar_plan={"2026-08-14": ["10:00"], "2026-08-15": ["11:00"]},
        )
        config = AppConfig(repositories=[repo])
        old_state = {
            "date": "2026-08-14",
            "repos": {repo.id: {"times": ["10:00"], "done": ["1"]}},
            "spans": {},
        }
        with (
            patch.object(scheduler, "ensure_dirs"),
            patch.object(scheduler, "load_state", return_value=old_state),
            patch.object(scheduler, "save_state"),
            patch.object(scheduler, "today_key", return_value="2026-08-15"),
        ):
            new_state = scheduler.ensure_today_state(config)
        self.assertIn("2026-08-14", new_state["spans"][repo.id]["completed_dates"])
        self.assertEqual(new_state["date"], "2026-08-15")

    def test_windows_startup_uses_a_hidden_vbs_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "GitPulse.vbs"
            with (
                patch.object(scheduler, "startup_file", return_value=path),
                patch.object(scheduler, "worker_command", return_value=["pythonw.exe", "GitPulse.pyw", "--worker"]),
            ):
                scheduler.set_start_with_windows(True)
                content = path.read_text(encoding="utf-8")
                self.assertIn('CreateObject("WScript.Shell")', content)
                self.assertIn("--worker", content)
                self.assertIn(", 0, False", content)
                self.assertFalse(path.with_suffix(".cmd").exists())

    def test_background_worker_has_native_tray_and_span_notification(self) -> None:
        root = Path(__file__).resolve().parent.parent
        tray_source = (root / "gitpulse" / "tray.py").read_text(encoding="utf-8")
        scheduler_source = (root / "gitpulse" / "scheduler.py").read_text(encoding="utf-8")
        self.assertIn("Shell_NotifyIconW", tray_source)
        self.assertIn('getattr(wintypes, "HCURSOR", wintypes.HANDLE)', tray_source)
        self.assertIn("GitPulse — Background automation", tray_source)
        self.assertIn("Exit background worker", tray_source)
        self.assertIn('"GitPulse span complete"', scheduler_source)
        self.assertIn("BackgroundTray", scheduler_source)

    def test_time_picker_uses_read_only_12_hour_controls(self) -> None:
        source = (Path(__file__).resolve().parent.parent / "gitpulse" / "ui.py").read_text(encoding="utf-8")
        self.assertIn('values=tuple(str(value) for value in range(1, 13))', source)
        self.assertIn('values=("AM", "PM")', source)
        self.assertIn('state="readonly"', source)
        self.assertIn('self.start_picker = TimePicker', source)
        self.assertNotIn('EntryField(schedule, "Start time"', source)

    def test_hourly_local_sync_requires_a_git_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = RepoConfig(local_path=temp, local_sync_enabled=True)
            self.assertTrue(validate_local_sync(repo))

    def test_daily_file_preserves_prior_dates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pulse_file = Path(temp) / "gitpulse.txt"
            pulse_file.write_text("2026-08-12 | GitPulse 03/03\n", encoding="utf-8")
            GitService.update_daily_line(pulse_file, "2026-08-13", 1, 5)
            GitService.update_daily_line(pulse_file, "2026-08-13", 2, 5)
            self.assertEqual(
                pulse_file.read_text(encoding="utf-8").splitlines(),
                ["2026-08-12 | GitPulse 03/03", "2026-08-13 | GitPulse 02/05"],
            )

    def test_connection_reports_managed_file_create_or_preserve(self) -> None:
        service = GitService()
        repo = RepoConfig(repo_url="https://github.com/example/example", commit_email="dev@example.com")
        with tempfile.TemporaryDirectory() as temp:
            repo_dir = Path(temp)
            with patch.object(service, "ensure_repo", return_value=(repo_dir, "main")):
                exists, message = service.inspect_pulse_target(repo)
                self.assertFalse(exists)
                self.assertIn("first pulse will create", message)
                (repo_dir / "gitpulse.txt").write_text("existing content\n", encoding="utf-8")
                exists, message = service.inspect_pulse_target(repo)
                self.assertTrue(exists)
                self.assertIn("already exists", message)

    def test_existing_gitpulse_content_is_preserved_by_pulse(self) -> None:
        service = GitService()
        with tempfile.TemporaryDirectory() as temp:
            repo_dir = Path(temp)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo_dir, check=True)
            pulse_file = repo_dir / "gitpulse.txt"
            pulse_file.write_text("Keep this project note.\n", encoding="utf-8")
            subprocess.run(["git", "add", "gitpulse.txt"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)
            repo = RepoConfig(name="Example", repo_url="https://github.com/example/example", commit_email="dev@example.com", commits_per_day=1)

            service._commit_one(repo, repo_dir, "main", 1, 1)

            content = pulse_file.read_text(encoding="utf-8")
            self.assertIn("Keep this project note.", content)
            self.assertRegex(content, r"\d{4}-\d{2}-\d{2} \| GitPulse 01/01")
            subject = subprocess.check_output(["git", "log", "-1", "--pretty=%s"], cwd=repo_dir, text=True).strip()
            self.assertEqual(subject, "Update")
            self.assertNotIn("gitpulse", subject.lower())

    def test_commits_are_non_empty_and_only_touch_gitpulse_txt(self) -> None:
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
            final_line = (repo_dir / "gitpulse.txt").read_text(encoding="utf-8").strip()
            self.assertRegex(final_line, r"\d{4}-\d{2}-\d{2} \| GitPulse 03/03")

            changed = subprocess.check_output(["git", "show", "--pretty=", "--name-only", "HEAD"], cwd=repo_dir, text=True).splitlines()
            self.assertEqual([line for line in changed if line], ["gitpulse.txt"])
            first_pulse_changed = subprocess.check_output(["git", "show", "--pretty=", "--name-only", "HEAD~2"], cwd=repo_dir, text=True).splitlines()
            self.assertEqual([line for line in first_pulse_changed if line], ["gitpulse.txt"])

    def test_pulse_button_has_locked_loading_guard(self) -> None:
        source = (Path(__file__).resolve().parent.parent / "gitpulse" / "ui.py").read_text(encoding="utf-8")
        self.assertIn('self.pulse_button.set_loading(True, "Pulsing")', source)
        self.assertIn("if not repo or self._pulse_busy_repo_id:", source)
        self.assertIn('"Sending pulse now…"', source)

    def test_local_sync_commits_only_the_pre_staged_snapshot(self) -> None:
        service = GitService()
        with tempfile.TemporaryDirectory() as temp:
            repo_dir = Path(temp)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo_dir, check=True)
            subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/example.git"], cwd=repo_dir, check=True)
            tracked = repo_dir / "tracked.txt"
            tracked.write_text("initial\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)

            tracked.write_text("staged snapshot\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo_dir, check=True)
            tracked.write_text("staged snapshot\nstill unstaged\n", encoding="utf-8")
            (repo_dir / "untracked.txt").write_text("leave me alone\n", encoding="utf-8")

            repo = RepoConfig(
                name="Example",
                repo_url="https://github.com/example/example",
                commit_email="dev@example.com",
                local_path=str(repo_dir),
                local_sync_enabled=True,
            )
            result = service.sync_staged_changes(repo, push=False)

            self.assertTrue(result.created)
            self.assertFalse(result.pushed)
            self.assertEqual(result.message, "Update files")
            self.assertEqual(result.staged_files, ("tracked.txt",))
            subject = subprocess.check_output(["git", "log", "-1", "--pretty=%s"], cwd=repo_dir, text=True).strip()
            self.assertEqual(subject, "Update files")
            self.assertNotIn("gitpulse", subject.lower())
            committed = subprocess.check_output(["git", "show", "HEAD:tracked.txt"], cwd=repo_dir, text=True)
            self.assertEqual(committed, "staged snapshot\n")
            status = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo_dir, text=True)
            self.assertIn(" M tracked.txt", status)
            self.assertIn("?? untracked.txt", status)

    def test_local_sync_pushes_existing_local_commits_with_staged_snapshot(self) -> None:
        service = GitService()
        with tempfile.TemporaryDirectory() as temp:
            repo_dir = Path(temp)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo_dir, check=True)
            subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/example.git"], cwd=repo_dir, check=True)
            tracked = repo_dir / "tracked.txt"
            tracked.write_text("initial\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "user commit"], cwd=repo_dir, check=True, capture_output=True)
            tracked.write_text("staged\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo_dir, check=True)
            repo = RepoConfig(
                repo_url="https://github.com/example/example",
                commit_email="dev@example.com",
                local_path=str(repo_dir),
                local_sync_enabled=True,
            )

            original_run_git = service.run_git
            pushes: list[list[str]] = []

            def run_without_network(args, *positional, **keywords):
                if args and args[0] == "push":
                    pushes.append(args)
                    return subprocess.CompletedProcess(["git", *args], 0, "", "")
                return original_run_git(args, *positional, **keywords)

            with patch.object(service, "_verify_local_branch_is_pushable", return_value=1), patch.object(service, "run_git", side_effect=run_without_network):
                result = service.sync_staged_changes(repo, push=True)

            self.assertTrue(result.pushed)
            self.assertEqual(pushes, [["push", "origin", "HEAD:main"]])
            self.assertFalse(subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=repo_dir, text=True).strip())
            self.assertFalse(service._pending_sync_commit(repo, repo_dir))

    def test_hourly_sync_absorbs_remote_pulses_and_preserves_local_work(self) -> None:
        service = GitService()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote = root / "remote.git"
            seed = root / "seed"
            local = root / "local"
            pulse = root / "pulse"

            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=seed, check=True)
            subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=seed, check=True)
            (seed / "README.md").write_text("initial\n", encoding="utf-8")
            (seed / "gitpulse.txt").write_text("initial pulse\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md", "gitpulse.txt"], cwd=seed, check=True)
            subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=seed, check=True, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
            subprocess.run(["git", "push", "-u", "origin", "main"], cwd=seed, check=True, capture_output=True)

            subprocess.run(["git", "clone", "-b", "main", str(remote), str(local)], check=True, capture_output=True)
            subprocess.run(["git", "clone", "-b", "main", str(remote), str(pulse)], check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Pulse User"], cwd=pulse, check=True)
            subprocess.run(["git", "config", "user.email", "pulse@example.com"], cwd=pulse, check=True)
            (pulse / "gitpulse.txt").write_text("remote pulse\n", encoding="utf-8")
            subprocess.run(["git", "add", "gitpulse.txt"], cwd=pulse, check=True)
            subprocess.run(["git", "commit", "-m", "Update"], cwd=pulse, check=True, capture_output=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=pulse, check=True, capture_output=True)

            (local / "README.md").write_text("staged snapshot\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=local, check=True)
            (local / "README.md").write_text("staged snapshot\nstill unstaged\n", encoding="utf-8")
            (local / "untracked.txt").write_text("keep me\n", encoding="utf-8")

            ahead = service._verify_local_branch_is_pushable(local, "main", ("README.md",))

            self.assertEqual(ahead, 0)
            self.assertEqual(service._staged_files(local), ("README.md",))
            self.assertEqual(
                subprocess.check_output(["git", "show", ":README.md"], cwd=local, text=True),
                "staged snapshot\n",
            )
            self.assertEqual((local / "README.md").read_text(encoding="utf-8"), "staged snapshot\nstill unstaged\n")
            self.assertEqual((local / "untracked.txt").read_text(encoding="utf-8"), "keep me\n")
            self.assertEqual((local / "gitpulse.txt").read_text(encoding="utf-8"), "remote pulse\n")

    def test_hourly_sync_rebases_a_diverged_branch_and_restores_staging(self) -> None:
        service = GitService()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote = root / "remote.git"
            seed = root / "seed"
            local = root / "local"
            cloud = root / "cloud"

            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True)
            for directory in (seed,):
                subprocess.run(["git", "config", "user.name", "Test User"], cwd=directory, check=True)
                subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=directory, check=True)
            (seed / "README.md").write_text("initial\n", encoding="utf-8")
            (seed / "gitpulse.txt").write_text("initial pulse\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=seed, check=True)
            subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=seed, check=True, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
            subprocess.run(["git", "push", "-u", "origin", "main"], cwd=seed, check=True, capture_output=True)

            subprocess.run(["git", "clone", "-b", "main", str(remote), str(local)], check=True, capture_output=True)
            subprocess.run(["git", "clone", "-b", "main", str(remote), str(cloud)], check=True, capture_output=True)
            for directory in (local, cloud):
                subprocess.run(["git", "config", "user.name", "Test User"], cwd=directory, check=True)
                subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=directory, check=True)

            (local / "local-commit.txt").write_text("local commit\n", encoding="utf-8")
            subprocess.run(["git", "add", "local-commit.txt"], cwd=local, check=True)
            subprocess.run(["git", "commit", "-m", "Local work"], cwd=local, check=True, capture_output=True)
            (local / "README.md").write_text("staged update\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=local, check=True)
            (local / "README.md").write_text("staged update\nunstaged update\n", encoding="utf-8")

            (cloud / "gitpulse.txt").write_text("new cloud pulse\n", encoding="utf-8")
            subprocess.run(["git", "add", "gitpulse.txt"], cwd=cloud, check=True)
            subprocess.run(["git", "commit", "-m", "Update"], cwd=cloud, check=True, capture_output=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=cloud, check=True, capture_output=True)

            ahead = service._verify_local_branch_is_pushable(local, "main", ("README.md",))

            self.assertEqual(ahead, 1)
            self.assertEqual(service._staged_files(local), ("README.md",))
            self.assertEqual((local / "README.md").read_text(encoding="utf-8"), "staged update\nunstaged update\n")
            self.assertEqual((local / "gitpulse.txt").read_text(encoding="utf-8"), "new cloud pulse\n")
            self.assertEqual(subprocess.check_output(["git", "log", "-1", "--pretty=%s"], cwd=local, text=True).strip(), "Local work")

    def test_visible_legacy_branding_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_file = root / "config.json"
            history_file = root / "history.jsonl"
            state_file = root / "state.json"
            marker_file = root / ".gitpulse-brand-v1"
            legacy = "Green" + "Pulse"
            config_file.write_text('{"repositories":[{"name":"' + legacy + '"}]}', encoding="utf-8")
            history_file.write_text('{"repo_name":"' + legacy + '","message":"' + legacy + ' pushed"}\n', encoding="utf-8")
            state_file.write_text('{"repos":{"repo-1":{"local_sync_last_check":123,"local_sync_status":"Failed"}}}', encoding="utf-8")

            with patch.object(storage, "CONFIG_FILE", config_file), patch.object(storage, "HISTORY_FILE", history_file), patch.object(storage, "STATE_FILE", state_file), patch.object(storage, "BRAND_MIGRATION_FILE", marker_file):
                storage._migrate_visible_branding()

            self.assertIn("GitPulse", config_file.read_text(encoding="utf-8"))
            self.assertIn("GitPulse pushed", history_file.read_text(encoding="utf-8"))
            self.assertIn('"local_sync_last_check": 0.0', state_file.read_text(encoding="utf-8"))
            self.assertTrue(marker_file.exists())

    def test_hourly_sync_upgrade_schedules_a_fresh_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_file = root / "state.json"
            marker_file = root / ".gitpulse-hourly-v2"
            state_file.write_text(
                '{"repos":{"repo-1":{"local_sync_last_check":123,"local_sync_next_check":999,"local_sync_status":"Needs attention"}}}',
                encoding="utf-8",
            )

            with (
                patch.object(storage, "STATE_FILE", state_file),
                patch.object(storage, "HOURLY_SYNC_MIGRATION_FILE", marker_file),
            ):
                storage._migrate_hourly_sync_state()

            migrated = state_file.read_text(encoding="utf-8")
            self.assertIn('"local_sync_last_check": 0.0', migrated)
            self.assertIn('"local_sync_next_check": 0.0', migrated)
            self.assertIn('"local_sync_status": "Ready"', migrated)
            self.assertTrue(marker_file.exists())

    def test_sync_engine_upgrade_forces_current_worker_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_file = root / "state.json"
            marker_file = root / ".gitpulse-sync-engine-v3"
            state_file.write_text(
                '{"repos":{"repo-1":{"local_sync_last_check":123,"local_sync_next_check":999,"local_sync_status":"Needs attention","local_sync_error":"old"}}}',
                encoding="utf-8",
            )
            with (
                patch.object(storage, "STATE_FILE", state_file),
                patch.object(storage, "SYNC_ENGINE_MIGRATION_FILE", marker_file),
            ):
                storage._migrate_sync_engine_state()
            migrated = json.loads(state_file.read_text(encoding="utf-8"))["repos"]["repo-1"]
            self.assertEqual(migrated["local_sync_next_check"], 0.0)
            self.assertEqual(migrated["local_sync_status"], "Ready · upgrade check pending")
            self.assertEqual(migrated["local_sync_error"], "")
            self.assertTrue(marker_file.exists())


if __name__ == "__main__":
    unittest.main()
