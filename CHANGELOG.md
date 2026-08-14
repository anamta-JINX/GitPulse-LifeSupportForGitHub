# Changelog

## 1.5.0 — Product-level desktop refresh

- Rebuilt the dashboard as a clearer command center with modern controls,
  progress rings, health badges, stronger hierarchy and responsive progress.
- Replaced the old slogan with **GitPulse — Life support for GitHub.**
- Rebuilt `GitPulse.exe` as a valid appended-archive Windows GUI launcher.
- Fixed shortcut/source-launch working-directory handling.
- Pinned Add/Edit actions so **Add repository** and **Save changes** stay visible
  at high Windows display scaling; the form now scrolls independently.
- Added per-repository **Hourly Sync** for changes explicitly staged with
  `git add`; unstaged and untracked files are never added automatically.
- Added repository-root, origin, branch and behind-remote safety checks plus an
  immediate setup check and hourly status indicator.
- Added a three-second delayed sliding animation for long repository names.
- Replaced the crowded local-sync options with a dedicated **Hourly Sync**
  setup screen and one **Save & check now** action.
- Fixed Hourly Sync remaining idle when the worker was offline; enabled Hourly
  Sync starts its worker when GitPulse opens and checks immediately after setup.
- Restored the original bottom-left **Start**, **Stop**, **Start in background
  with Windows**, and **Complete all repos now** controls.
- Added a locked animated **Pulse now** state so a manual pulse starts
  immediately and cannot be triggered twice while Git is working.
- Repository connection now reports whether `gitpulse.txt` exists or will be
  created by the first pulse; later pulses preserve the file and update it.
- Failed Activity rows now open their exact error details on double-click.
- Migrates visible legacy product names in saved repository labels and history
  to GitPulse without changing actual Git remote URLs.
- Added visible native startup errors plus `%USERPROFILE%\.gitpulse\startup-error.log`.
- Stopped unchanged polling cycles from rebuilding the complete interface.
- Improved the repository connection flow and error presentation.
- Added a Windows CI build that tests and packages a standalone executable.
- Replaced branded commit subjects with the natural **Update** and **Update
  files** messages.
- Fixed Hourly Sync stopping when the connected local branch was behind GitHub;
  branch alignment now preserves staged, unstaged and untracked local work.
- Added five-minute retries after failed hourly checks and visible worker
  startup failures instead of reporting a worker as started when it exited.
- Added a worker build signature so a running older worker is stopped and
  replaced automatically after an application upgrade.
- Expanded Hourly Sync to safely fast-forward or rebase behind/diverged local
  branches while preserving the exact staged, unstaged and untracked worktree.
- Added the full Hourly Sync error directly to the selected repository panel.
- Converted every visible application time to 12-hour AM/PM formatting.
- Added a visual pulse calendar that generates different schedules for each of
  1–30 future dates and lets users inspect every day's times before saving.
- Rebuilt the calendar as a responsive, scrollable **Automatic pulse span**
  planner with fixed actions and a compact two-row form that does not overflow.
- Replaced pulses-per-day in automatic mode with one **Pulses for full span**
  total. GitPulse randomly distributes the exact total across 1–30 selected
  dates, guarantees at least one pulse per date and varies their times.
- Made the saved span the complete automatic schedule, so normal per-day pulses
  do not run outside the selected dates while a span exists.
- Persisted span progress across daily rollovers and Windows restarts, with one
  completion event after every planned date reaches its target.
- Added a native Windows notification-area icon with Open/Exit actions and a
  completion notification that works while the dashboard is closed.
- Added silent Windows sign-in startup through a per-user Startup VBS; **Save &
  start** enables it and immediately launches or upgrades the background worker.
- Replaced free-text start/end time fields with read-only hour (1–12), minute
  (00–59) and AM/PM selectors so invalid clock values cannot be entered.
- Rebuilt the README banner, product previews and tutorial GIF.
- Added architecture, workflow, data-model and directory documentation.
- Added explicit Anamta Gohar copyright and brand attribution.
