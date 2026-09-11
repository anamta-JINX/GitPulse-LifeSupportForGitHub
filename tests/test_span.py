"""Regression coverage for span editing and the GUI's preview/save path."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from gitpulse.models import AppConfig, RepoConfig
from gitpulse import scheduler
from gitpulse.scheduler import schedule_for_day, validate_repo
from gitpulse.span import update_span
from gitpulse.ui import CalendarPlanDialog, RepoDialog, _next_pulse_display
from gitpulse.utils import parse_hhmm, pulse_target_for_date

TODAY = date(2026, 9, 8)


def repo_fixture():
    return RepoConfig(id="span-test", repo_url="https://github.com/example/project", commit_email="dev@example.com", schedule_mode="span")


class SpanTests(unittest.TestCase):
    def setUp(self):
        self.repo = repo_fixture()

    def update(self, repo=None, **changes):
        settings = dict(start_date=TODAY.isoformat(), days=15, total_pulses=30, start_time="09:00", end_time="17:00", today=TODAY)
        settings.update(changes)
        return update_span(repo or self.repo, **settings)

    def test_30_pulses_for_15_days_exact_total_unique_times_and_minimum(self):
        updated = self.update()
        self.assertEqual(len(updated.calendar_plan), 15)
        self.assertEqual(sum(map(len, updated.calendar_plan.values())), 30)
        self.assertGreater(len(set(map(len, updated.calendar_plan.values()))), 1)
        for times in updated.calendar_plan.values():
            self.assertGreaterEqual(len(times), 1)
            self.assertEqual(times, sorted(set(times)))
            self.assertTrue(all(540 <= parse_hhmm(value) <= 1020 for value in times))
        self.assertEqual(self.repo.calendar_plan, {})
        self.assertEqual((updated.start_time, updated.end_time), ("09:00", "17:00"))

    def test_one_per_day_and_one_day_boundaries(self):
        self.assertTrue(all(len(times) == 1 for times in self.update(total_pulses=15).calendar_plan.values()))
        self.assertEqual(sum(map(len, self.update(days=1, total_pulses=1).calendar_plan.values())), 1)
        self.assertEqual(len(self.update(days=365, total_pulses=365).calendar_plan), 365)

    def test_rejects_invalid_inputs_without_mutating_saved_repo(self):
        original = deepcopy(self.repo.to_dict())
        for changes in [dict(total_pulses=14), dict(total_pulses=""), dict(days="1.5"), dict(days=0), dict(days=366), dict(start_date="wrong"), dict(start_time="18:00"), dict(days=2, total_pulses=5, start_time="10:00", end_time="10:01"), dict(start_date="9999-12-31")]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.update(**changes)
            self.assertEqual(self.repo.to_dict(), original)

    def test_unchanged_save_and_reopen_preserve_exact_allocation(self):
        first = self.update()
        loaded = RepoConfig.from_dict(first.to_dict())
        second = self.update(loaded)
        self.assertEqual(second.calendar_plan, first.calendar_plan)
        self.assertIsNot(second.calendar_plan, first.calendar_plan)
        second.calendar_plan[TODAY.isoformat()].append("23:00")
        self.assertNotEqual(second.calendar_plan, first.calendar_plan)

    def test_changed_days_total_and_window_are_all_persisted(self):
        first = self.update()
        updated = self.update(first, days=20, total_pulses=70, start_time="6:00 PM", end_time="9:00 PM")
        loaded = RepoConfig.from_dict(updated.to_dict())
        self.assertEqual(len(loaded.calendar_plan), 20)
        self.assertEqual(sum(map(len, loaded.calendar_plan.values())), 70)
        self.assertEqual((loaded.start_time, loaded.end_time), ("18:00", "21:00"))
        self.assertTrue(all(1080 <= parse_hhmm(t) <= 1260 for times in loaded.calendar_plan.values() for t in times))
        self.assertEqual(schedule_for_day(loaded, "2026-09-28"), [])
        self.assertEqual(pulse_target_for_date(loaded, "2026-09-28"), 0)

    def test_legacy_daily_settings_cannot_block_or_override_span(self):
        updated = self.update(start_time="10:00", end_time="10:01")
        updated.commits_per_day = 100
        self.assertEqual(validate_repo(updated), [])
        self.assertEqual(len(schedule_for_day(updated, TODAY.isoformat())), 2)
        self.assertEqual(pulse_target_for_date(updated, TODAY.isoformat()), 2)

    def test_no_span_means_no_fallback_daily_pulses(self):
        self.assertEqual(schedule_for_day(self.repo, TODAY.isoformat()), [])
        self.assertEqual(pulse_target_for_date(self.repo, TODAY.isoformat()), 0)
        legacy = RepoConfig.from_dict({"commits_per_day": 7})
        self.assertEqual(len(schedule_for_day(legacy, TODAY.isoformat())), 7)

    def test_today_target_cannot_fall_below_already_completed_pulses(self):
        updated = self.update(completed_today=5)
        self.assertGreaterEqual(len(updated.calendar_plan[TODAY.isoformat()]), 5)
        self.assertEqual(sum(map(len, updated.calendar_plan.values())), 30)
        with self.assertRaisesRegex(ValueError, "at least 19"):
            self.update(total_pulses=15, completed_today=5)

    def test_active_span_preserves_past_dates_and_exact_total(self):
        first = self.update()
        changed = self.update(first, total_pulses=45, today=TODAY + timedelta(days=3), start_time="12:00")
        for offset in range(3):
            key = (TODAY + timedelta(days=offset)).isoformat()
            self.assertEqual(changed.calendar_plan[key], first.calendar_plan[key])
        self.assertEqual(sum(map(len, changed.calendar_plan.values())), 45)
        self.assertEqual(len(changed.calendar_plan), 15)

    def test_new_past_dates_rejected_and_finished_span_can_be_reopened(self):
        with self.assertRaisesRegex(ValueError, "today or later"):
            self.update(start_date="2026-09-07")
        first = self.update()
        later = TODAY + timedelta(days=30)
        self.assertEqual(self.update(first, today=later).calendar_plan, first.calendar_plan)
        with self.assertRaisesRegex(ValueError, "has ended"):
            self.update(first, total_pulses=40, today=later)

    def test_edit_preserves_completed_span_dates(self):
        first = self.update()
        state = {"spans": {}}
        scheduler._sync_span_state(state, AppConfig(repositories=[first]))
        state["spans"][first.id]["completed_dates"] = [TODAY.isoformat()]
        changed = self.update(first, total_pulses=45, today=TODAY + timedelta(days=1))
        scheduler._sync_span_state(state, AppConfig(repositories=[changed]))
        self.assertEqual(state["spans"][first.id]["completed_dates"], [TODAY.isoformat()])

    def test_day_complete_shows_next_date_not_span_complete(self):
        repo = self.update()
        times = repo.calendar_plan[TODAY.isoformat()]
        state = {"date": TODAY.isoformat(), "repos": {repo.id: {"times": times, "done": [str(i + 1) for i in range(len(times))]}}}
        self.assertIn("Sep 9", _next_pulse_display(repo, state))
        repo.enabled = False
        self.assertEqual(_next_pulse_display(repo, state), "Paused")


class EditorRegressionTests(unittest.TestCase):
    """Exercise real form-to-preview-to-save methods without requiring a display."""

    def dialog(self):
        tomorrow = date.today() + timedelta(days=1)
        dialog = SimpleNamespace(
            repo=repo_fixture(), result=None, preview_result=None, plan={},
            selected_date=None, completed_today=0, _preview_job="pending-preview",
            start_date_var=Mock(get=Mock(return_value=tomorrow.isoformat())),
            days_var=Mock(get=Mock(return_value="15")),
            total_var=Mock(get=Mock(return_value="30")),
            start_picker=Mock(get=Mock(return_value="09:00")),
            end_picker=Mock(get=Mock(return_value="17:00")),
            error_var=Mock(), preview_note=Mock(), save_button=Mock(),
            after_cancel=Mock(), _render_calendar=Mock(), destroy=Mock(),
            master=Mock(_state=Mock(return_value={"repos": {}})),
        )
        dialog._generate = lambda: CalendarPlanDialog._generate(dialog)
        return dialog

    def test_save_uses_new_fields_even_before_debounced_preview_runs(self):
        dialog = self.dialog()
        dialog._generate()
        old = deepcopy(dialog.plan)
        dialog.days_var.get.return_value = "20"
        dialog.total_var.get.return_value = "60"
        dialog.start_picker.get.return_value = "18:00"
        dialog.end_picker.get.return_value = "22:00"
        dialog._preview_job = "new-pending-preview"
        CalendarPlanDialog._save(dialog)
        self.assertEqual(len(dialog.result.calendar_plan), 20)
        self.assertEqual(sum(map(len, dialog.result.calendar_plan.values())), 60)
        self.assertEqual(dialog.result.start_time, "18:00")
        self.assertEqual(dialog.result.end_time, "22:00")
        self.assertNotEqual(old, dialog.result.calendar_plan)
        dialog.after_cancel.assert_called_with("new-pending-preview")
        dialog.destroy.assert_called_once()

    def test_invalid_changed_field_cannot_save_old_preview(self):
        dialog = self.dialog()
        dialog._generate()
        dialog.total_var.get.return_value = "2"
        CalendarPlanDialog._save(dialog)
        self.assertIsNone(dialog.result)
        self.assertIsNone(dialog.preview_result)
        self.assertEqual(dialog.plan, {})
        self.assertFalse(dialog.save_button.enabled)
        dialog.destroy.assert_not_called()
        self.assertIn("at least 15", dialog.error_var.set.call_args.args[0])

    def test_connection_edits_preserve_schedule_and_new_repos_start_empty(self):
        repo = update_span(repo_fixture(), (date.today() + timedelta(days=1)).isoformat(), 15, 30, "09:00", "17:00")
        original = deepcopy(repo.to_dict())
        form = SimpleNamespace(original=repo, name_var=Mock(get=Mock(return_value="Renamed")), url_var=Mock(get=Mock(return_value=repo.repo_url)), email_var=Mock(get=Mock(return_value=repo.commit_email)), branch_var=Mock(get=Mock(return_value="main")))
        result = RepoDialog._repo_from_form(form)
        self.assertEqual(result.calendar_plan, repo.calendar_plan)
        self.assertEqual((result.start_time, result.end_time), ("09:00", "17:00"))
        self.assertEqual(repo.to_dict(), original)
        form.original = None
        new = RepoDialog._repo_from_form(form)
        self.assertEqual(new.schedule_mode, "span")
        self.assertEqual(new.calendar_plan, {})
        self.assertFalse(new.enabled)

    def test_saving_existing_paused_span_keeps_it_paused(self):
        dialog = self.dialog()
        dialog._generate()
        dialog.repo = dialog.preview_result
        dialog.repo.enabled = False
        CalendarPlanDialog._save(dialog)
        self.assertFalse(dialog.result.enabled)


if __name__ == "__main__":
    unittest.main()
