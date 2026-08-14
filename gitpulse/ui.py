from __future__ import annotations

import json
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from .git_service import GitService
from .models import AppConfig, RepoConfig
from .resources import resource_path
from .scheduler import (
    claim_local_sync_check,
    complete_all_enabled,
    ensure_today_state,
    generate_automatic_plan,
    launch_worker,
    next_scheduled_time,
    run_complete_today,
    run_local_sync,
    run_one_pulse,
    set_start_with_windows,
    stop_worker,
    validate_local_sync,
    validate_repo,
    worker_is_current,
    worker_pid,
)
from .storage import load_config, load_history, save_config, write_log
from .utils import format_datetime_12, format_hhmm_12, parse_hhmm, parse_repo_url

APP_NAME = "GitPulse"
TAGLINE = "Life support for GitHub."
VERSION = "1.5.0"

BG = "#07100C"
SIDEBAR = "#091711"
PANEL = "#0C1C15"
PANEL_2 = "#10251B"
PANEL_3 = "#163326"
BORDER = "#1E4935"
BORDER_SOFT = "#163A2A"
TEXT = "#F4FAF6"
MUTED = "#8DA99A"
SUBTLE = "#5F7D6D"
ACCENT = "#27F58A"
ACCENT_2 = "#00C96B"
ACCENT_DARK = "#0B4329"
CYAN = "#42E8D0"
DANGER = "#FF7B83"
DANGER_BG = "#3A191F"
WARNING = "#FFC96B"
WARNING_BG = "#3A2D13"

FONT = "Segoe UI"
MONO = "Cascadia Mono"


def _display_schedule_time(value: str) -> str:
    if not value or value == "Complete":
        return value or "—"
    try:
        return format_hhmm_12(value)
    except ValueError:
        return value


def _target_for_state(repo: RepoConfig, state: dict) -> int:
    times = state.get("repos", {}).get(repo.id, {}).get("times", [])
    if repo.calendar_plan:
        return len(times)
    return len(times) or int(repo.commits_per_day)


def _next_pulse_display(repo: RepoConfig, state: dict) -> str:
    raw = next_scheduled_time(repo, state)
    if repo.calendar_plan and raw == "Complete":
        dates = sorted(repo.calendar_plan)
        date_key = str(state.get("date", ""))
        if dates and date_key < dates[0]:
            start = datetime.strptime(dates[0], "%Y-%m-%d").date()
            return f"Starts {start.strftime('%b')} {start.day}"
        return "Span complete"
    return _display_schedule_time(raw)


def _round_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs):
    radius = max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=18, **kwargs)


class GPButton(tk.Canvas):
    """Rounded, keyboard-accessible button used across the dashboard."""

    def __init__(self, master, text: str, command=None, kind: str = "secondary", width: int = 132, height: int = 42, **kwargs) -> None:
        super().__init__(master, width=width, height=height, bg=master.cget("bg"), highlightthickness=0, bd=0, cursor="hand2", takefocus=True, **kwargs)
        self.label = text
        self.command = command
        self.kind = kind
        self.enabled = True
        self.loading = False
        self._loading_label = text
        self._loading_index = 0
        self._loading_job: str | None = None
        self._hovered = False
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)
        self.bind("<Return>", self._click)
        self.bind("<space>", self._click)
        self.bind("<Destroy>", self._cancel_loading)
        self._draw()

    def _palette(self) -> tuple[str, str, str]:
        if self.loading:
            return ACCENT_DARK, ACCENT, ACCENT_2
        if not self.enabled:
            return PANEL_2, SUBTLE, BORDER_SOFT
        if self.kind == "primary":
            return ("#42FF9A" if self._hovered else ACCENT), "#03110A", ACCENT
        if self.kind == "danger":
            return ("#52242B" if self._hovered else DANGER_BG), "#FFD8DB", "#6B3038"
        if self.kind == "ghost":
            return (PANEL_3 if self._hovered else PANEL), (TEXT if self._hovered else MUTED), BORDER_SOFT
        return (PANEL_3 if self._hovered else PANEL_2), TEXT, (ACCENT_2 if self._hovered else BORDER)

    def _draw(self) -> None:
        self.delete("all")
        width, height = max(20, self.winfo_width()), max(20, self.winfo_height())
        bg, fg, outline = self._palette()
        _round_rect(self, 1, 1, width - 2, height - 2, 11, fill=bg, outline=outline, width=1)
        display = self.label
        if self.loading:
            frames = ("◐", "◓", "◑", "◒")
            display = f"{frames[self._loading_index]}  {self._loading_label}"
        self.create_text(width // 2, height // 2, text=display, fill=fg, font=(FONT, 9, "bold"))

    def _enter(self, _event=None) -> None:
        self._hovered = True
        self._draw()

    def _leave(self, _event=None) -> None:
        self._hovered = False
        self._draw()

    def _click(self, _event=None) -> None:
        if self.enabled and self.command:
            self.command()

    def set_loading(self, loading: bool, label: str = "Working") -> None:
        self._cancel_loading()
        self.loading = loading
        self.enabled = not loading
        self._loading_label = label
        self._loading_index = 0
        self._draw()
        if loading:
            self._loading_job = self.after(120, self._animate_loading)

    def _animate_loading(self) -> None:
        if not self.loading:
            return
        self._loading_index = (self._loading_index + 1) % 4
        self._draw()
        self._loading_job = self.after(120, self._animate_loading)

    def _cancel_loading(self, _event=None) -> None:
        if self._loading_job:
            try:
                self.after_cancel(self._loading_job)
            except tk.TclError:
                pass
            self._loading_job = None


class ProgressBar(tk.Canvas):
    def __init__(self, master, ratio: float = 0.0, height: int = 7, **kwargs) -> None:
        super().__init__(master, height=height, bg=master.cget("bg"), bd=0, highlightthickness=0, **kwargs)
        self.ratio = max(0.0, min(1.0, ratio))
        self.bind("<Configure>", lambda _event: self._draw())

    def _draw(self) -> None:
        self.delete("all")
        width, height = max(8, self.winfo_width()), max(5, self.winfo_height())
        _round_rect(self, 0, 0, width, height, height // 2, fill=PANEL_3, outline="")
        fill_width = int(width * self.ratio)
        if fill_width > 3:
            _round_rect(self, 0, 0, fill_width, height, height // 2, fill=ACCENT, outline="")


class SlidingLabel(tk.Canvas):
    """Single-line label that pauses, then slides only when text overflows."""

    def __init__(self, master, text: str, font, bg: str, fg: str, height: int, cursor: str = "") -> None:
        super().__init__(master, height=height, bg=bg, bd=0, highlightthickness=0, cursor=cursor)
        self.label_text = text
        self.label_font = font
        self.label_fg = fg
        self._text_id = self.create_text(0, height // 2, text=text, fill=fg, font=font, anchor="w")
        self._offset = 0
        self._job: str | None = None
        self.bind("<Configure>", self._restart)
        self.bind("<Destroy>", self._cancel)

    def _cancel(self, _event=None) -> None:
        if self._job:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _restart(self, _event=None) -> None:
        self._cancel()
        self._offset = 0
        self.coords(self._text_id, 0, max(1, self.winfo_height()) // 2)
        bbox = self.bbox(self._text_id)
        if bbox and bbox[2] - bbox[0] > max(1, self.winfo_width()):
            self._job = self.after(3000, self._slide)

    def _slide(self) -> None:
        if not self.winfo_exists():
            return
        bbox = self.bbox(self._text_id)
        if not bbox:
            return
        overflow = max(0, (bbox[2] - bbox[0]) - self.winfo_width())
        if self._offset < overflow:
            step = min(2, overflow - self._offset)
            self.move(self._text_id, -step, 0)
            self._offset += step
            self._job = self.after(30, self._slide)
        else:
            self._job = self.after(3000, self._restart)


class ProgressRing(tk.Canvas):
    def __init__(self, master, done: int, target: int, size: int = 148) -> None:
        super().__init__(master, width=size, height=size, bg=master.cget("bg"), bd=0, highlightthickness=0)
        display_target = max(0, target)
        safe_target = max(1, display_target)
        pad = 11
        self.create_oval(pad, pad, size - pad, size - pad, outline=PANEL_3, width=11)
        if done:
            self.create_arc(pad, pad, size - pad, size - pad, start=90, extent=-359.9 * min(1.0, done / safe_target), style="arc", outline=ACCENT, width=11)
        self.create_text(size // 2, size // 2 - 9, text=f"{done}/{display_target}", fill=TEXT, font=(FONT, 22, "bold"))
        self.create_text(size // 2, size // 2 + 22, text="PULSES TODAY", fill=MUTED, font=(FONT, 7, "bold"))


class EntryField(tk.Frame):
    def __init__(self, master, label: str, variable: tk.StringVar, hint: str = "") -> None:
        super().__init__(master, bg=master.cget("bg"))
        tk.Label(self, text=label, bg=self.cget("bg"), fg=TEXT, font=(FONT, 9, "bold")).pack(anchor="w", pady=(0, 6))
        shell = tk.Frame(self, bg=PANEL_2, highlightthickness=1, highlightbackground=BORDER_SOFT)
        shell.pack(fill="x")
        self.entry = tk.Entry(shell, textvariable=variable, bg=PANEL_2, fg=TEXT, insertbackground=ACCENT, relief="flat", bd=0, font=(FONT, 10))
        self.entry.pack(fill="x", padx=12, pady=10)
        if hint:
            tk.Label(self, text=hint, bg=self.cget("bg"), fg=SUBTLE, font=(FONT, 8)).pack(anchor="w", pady=(5, 0))


class TimePicker(tk.Frame):
    """Validated 12-hour selector; users cannot type an invalid clock value."""

    def __init__(self, master, label: str, value: str) -> None:
        super().__init__(master, bg=master.cget("bg"))
        minutes = parse_hhmm(value)
        hour_24, minute = divmod(minutes, 60)
        self.hour_var = tk.StringVar(value=str(hour_24 % 12 or 12))
        self.minute_var = tk.StringVar(value=f"{minute:02d}")
        self.period_var = tk.StringVar(value="AM" if hour_24 < 12 else "PM")

        tk.Label(self, text=label, bg=self.cget("bg"), fg=TEXT, font=(FONT, 9, "bold")).pack(anchor="w", pady=(0, 6))
        shell = tk.Frame(self, bg=PANEL_2, highlightthickness=1, highlightbackground=BORDER_SOFT)
        shell.pack(fill="x")
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_columnconfigure(2, weight=1)
        shell.grid_columnconfigure(3, weight=1)
        ttk.Combobox(
            shell,
            textvariable=self.hour_var,
            values=tuple(str(value) for value in range(1, 13)),
            state="readonly",
            style="GitPulse.TCombobox",
            width=3,
        ).grid(row=0, column=0, sticky="ew", padx=(8, 3), pady=7)
        tk.Label(shell, text=":", bg=PANEL_2, fg=MUTED, font=(FONT, 11, "bold")).grid(row=0, column=1)
        ttk.Combobox(
            shell,
            textvariable=self.minute_var,
            values=tuple(f"{value:02d}" for value in range(60)),
            state="readonly",
            style="GitPulse.TCombobox",
            width=4,
        ).grid(row=0, column=2, sticky="ew", padx=3, pady=7)
        ttk.Combobox(
            shell,
            textvariable=self.period_var,
            values=("AM", "PM"),
            state="readonly",
            style="GitPulse.TCombobox",
            width=4,
        ).grid(row=0, column=3, sticky="ew", padx=(3, 8), pady=7)

    def get(self) -> str:
        hour = int(self.hour_var.get())
        minute = int(self.minute_var.get())
        period = self.period_var.get()
        if not 1 <= hour <= 12 or not 0 <= minute <= 59 or period not in {"AM", "PM"}:
            raise ValueError("Choose a valid hour, minute and AM/PM value.")
        hour_24 = hour % 12 + (12 if period == "PM" else 0)
        return f"{hour_24:02d}:{minute:02d}"


class RepoDialog(tk.Toplevel):
    def __init__(self, parent: "GitPulseApp", repo: RepoConfig | None = None) -> None:
        super().__init__(parent)
        self.parent = parent
        self.original = repo
        self.result: RepoConfig | None = None
        self.title("GitPulse — Repository setup")
        self.geometry("760x800")
        self.minsize(680, 620)
        self.resizable(True, True)
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        try:
            self.iconbitmap(str(resource_path("assets", "gitpulse.ico")))
        except Exception:
            pass

        item = repo or RepoConfig()
        try:
            default_name = item.name or parse_repo_url(item.repo_url)[1]
        except Exception:
            default_name = item.name
        self.name_var = tk.StringVar(value=default_name)
        self.url_var = tk.StringVar(value=item.repo_url)
        self.email_var = tk.StringVar(value=item.commit_email)
        self.count_var = tk.StringVar(value=str(item.commits_per_day))
        self.branch_var = tk.StringVar(value=item.branch)
        self.enabled_var = tk.BooleanVar(value=item.enabled)
        self.status_var = tk.StringVar(value="Ready to test")

        # The form scrolls independently while the action bar stays pinned.
        # This keeps Add/Save visible on laptops and at 125–200% Windows DPI.
        content_shell = tk.Frame(self, bg=BG)
        content_shell.grid(row=0, column=0, sticky="nsew")
        content_shell.grid_columnconfigure(0, weight=1)
        content_shell.grid_rowconfigure(0, weight=1)
        canvas = tk.Canvas(content_shell, bg=BG, highlightthickness=0, bd=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(content_shell, orient="vertical", command=canvas.yview, style="GitPulse.Vertical.TScrollbar")
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        outer = tk.Frame(canvas, bg=BG)
        form_window = canvas.create_window((34, 26), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(form_window, width=max(560, event.width - 68)))
        canvas.bind("<Enter>", lambda _event: canvas.bind_all("<MouseWheel>", lambda wheel: canvas.yview_scroll(int(-wheel.delta / 120), "units")))
        canvas.bind("<Leave>", lambda _event: canvas.unbind_all("<MouseWheel>"))

        tk.Label(outer, text="REPOSITORY CONNECTION", bg=BG, fg=ACCENT, font=(FONT, 8, "bold")).pack(anchor="w")
        heading = "Edit repository" if repo else "Connect a repository"
        tk.Label(outer, text=heading, bg=BG, fg=TEXT, font=(FONT, 23, "bold")).pack(anchor="w", pady=(3, 2))
        tk.Label(outer, text="Every repository gets an independent schedule, progress track and isolated cache.", bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(0, 22))
        EntryField(outer, "Display name", self.name_var, "A short label used only in GitPulse").pack(fill="x", pady=(0, 14))
        EntryField(outer, "GitHub repository URL", self.url_var, "Example: https://github.com/username/repository").pack(fill="x", pady=(0, 14))
        EntryField(outer, "Commit email", self.email_var, "Use an email verified on the GitHub account").pack(fill="x", pady=(0, 14))

        schedule = tk.Frame(outer, bg=BG)
        schedule.pack(fill="x", pady=(0, 14))
        schedule.grid_columnconfigure(0, weight=1)
        schedule.grid_columnconfigure(1, weight=2)
        schedule.grid_columnconfigure(2, weight=2)
        EntryField(schedule, "Pulses / day", self.count_var).grid(row=0, column=0, sticky="ew", padx=(0, 7))
        self.start_picker = TimePicker(schedule, "Start time", item.start_time)
        self.start_picker.grid(row=0, column=1, sticky="ew", padx=7)
        self.end_picker = TimePicker(schedule, "End time", item.end_time)
        self.end_picker.grid(row=0, column=2, sticky="ew", padx=(7, 0))
        EntryField(outer, "Branch (optional)", self.branch_var, "Leave blank to follow the repository default branch").pack(fill="x", pady=(0, 12))

        options = tk.Frame(outer, bg=PANEL, highlightthickness=1, highlightbackground=BORDER_SOFT)
        options.pack(fill="x", pady=(0, 16))
        tk.Checkbutton(options, text="Enable scheduled pulses", variable=self.enabled_var, bg=PANEL, fg=TEXT, activebackground=PANEL, activeforeground=TEXT, selectcolor=PANEL_3, font=(FONT, 9)).pack(side="left", padx=13, pady=12)
        tk.Label(options, textvariable=self.status_var, bg=PANEL, fg=MUTED, font=(FONT, 8)).pack(side="right", padx=13)

        actions = tk.Frame(self, bg=PANEL, highlightthickness=1, highlightbackground=BORDER_SOFT)
        actions.grid(row=1, column=0, sticky="ew")
        actions.grid_columnconfigure(1, weight=1)
        GPButton(actions, "Test connection", self._test_connection, width=142).grid(row=0, column=0, padx=(24, 8), pady=16)
        GPButton(actions, "Cancel", self.destroy, kind="ghost", width=94).grid(row=0, column=2, padx=(8, 8), pady=16)
        save_label = "Save changes" if repo else "Add repository"
        GPButton(actions, save_label, self._save, kind="primary", width=148).grid(row=0, column=3, padx=(0, 24), pady=16)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Control-s>", lambda _event: self._save())
        self.after(120, self.focus_force)

    def _repo_from_form(self) -> RepoConfig:
        base_id = self.original.id if self.original else RepoConfig().id
        try:
            commits_per_day = int(self.count_var.get().strip())
        except ValueError as exc:
            raise ValueError("Pulses per day must be a whole number.") from exc
        item = RepoConfig(
            id=base_id,
            name=self.name_var.get().strip(),
            repo_url=self.url_var.get().strip(),
            commit_email=self.email_var.get().strip(),
            commits_per_day=commits_per_day,
            start_time=self.start_picker.get(),
            end_time=self.end_picker.get(),
            branch=self.branch_var.get().strip(),
            enabled=bool(self.enabled_var.get()),
            local_path=self.original.local_path if self.original else "",
            local_sync_enabled=self.original.local_sync_enabled if self.original else False,
            calendar_plan=dict(self.original.calendar_plan) if self.original else {},
        )
        if not item.name:
            try:
                item.name = parse_repo_url(item.repo_url)[1]
            except Exception:
                pass
        return item

    def _validate_form(self) -> RepoConfig | None:
        try:
            item = self._repo_from_form()
        except ValueError as exc:
            messagebox.showerror("GitPulse", str(exc), parent=self)
            return None
        errors = validate_repo(item)
        if errors:
            messagebox.showerror("GitPulse", "\n\n".join(errors), parent=self)
            return None
        return item

    def _test_connection(self) -> None:
        item = self._validate_form()
        if not item:
            return
        self.status_var.set("Checking access…")

        def work() -> None:
            service = GitService()
            ok, message = service.test_connection(item)
            target_message = ""
            if ok:
                try:
                    target_exists, target_message = service.inspect_pulse_target(item)
                    status = "Connected · gitpulse.txt found" if target_exists else "Connected · creates file on first pulse"
                except Exception as exc:
                    ok, message = False, str(exc)
                    status = "Connection failed"
            else:
                status = "Connection failed"
            self.after(0, lambda: self.status_var.set(status))
            if ok:
                self.after(0, lambda: messagebox.showinfo("GitPulse — Repository ready", target_message, parent=self))
            else:
                self.after(0, lambda: messagebox.showerror("GitPulse", message, parent=self))

        threading.Thread(target=work, daemon=True).start()

    def _save(self) -> None:
        item = self._validate_form()
        if item:
            self.result = item
            self.destroy()


class CalendarPlanDialog(tk.Toplevel):
    """Visual automatic planner with varied daily targets and times."""

    def __init__(self, parent: "GitPulseApp", repo: RepoConfig) -> None:
        super().__init__(parent)
        self.result: RepoConfig | None = None
        self.repo = RepoConfig.from_dict(repo.to_dict())
        self.plan: dict[str, list[str]] = dict(self.repo.calendar_plan)
        self.selected_date: str | None = None
        self.generation_variant = 1 if self.plan else 0
        self.title("GitPulse — Automatic pulse span")
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        dialog_width = max(760, min(1120, screen_width - 80))
        dialog_height = max(560, min(800, screen_height - 100))
        self.geometry(f"{dialog_width}x{dialog_height}")
        self.minsize(min(760, dialog_width), min(560, dialog_height))
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        try:
            self.iconbitmap(str(resource_path("assets", "gitpulse.ico")))
        except Exception:
            pass

        today = datetime.now().astimezone().date().isoformat()
        future_dates = sorted(key for key in self.plan if key >= today)
        initial_date = future_dates[0] if future_dates else today
        self.start_date_var = tk.StringVar(value=initial_date)
        initial_days = min(30, max(1, len(future_dates) or 7))
        self.days_var = tk.StringVar(value=str(initial_days))
        initial_total = sum(len(self.plan[key]) for key in future_dates) or int(repo.commits_per_day) * initial_days
        self.total_var = tk.StringVar(value=str(initial_total))

        content = tk.Frame(self, bg=BG)
        content.grid(row=0, column=0, sticky="nsew", padx=22, pady=(18, 14))
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(4, weight=1)

        tk.Label(content, text="AUTOMATIC PULSE SPAN", bg=BG, fg=ACCENT, font=(FONT, 8, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(content, text="Plan the full span, not each day", bg=BG, fg=TEXT, font=(FONT, 22, "bold")).grid(row=1, column=0, sticky="w", pady=(3, 2))
        tk.Label(
            content,
            text="Set one pulse total for the selected dates. Every day gets at least one; GitPulse randomizes the remaining counts and times.",
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
            wraplength=690,
        ).grid(row=2, column=0, sticky="w", pady=(0, 17))

        controls = tk.Frame(content, bg=PANEL, highlightthickness=1, highlightbackground=BORDER_SOFT)
        controls.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        for column in range(6):
            controls.grid_columnconfigure(column, weight=1, uniform="span-control")
        EntryField(controls, "Start date", self.start_date_var, "YYYY-MM-DD").grid(row=0, column=0, columnspan=2, sticky="ew", padx=(14, 6), pady=(12, 6))
        EntryField(controls, "Days in span", self.days_var, "1–30").grid(row=0, column=2, sticky="ew", padx=6, pady=(12, 6))
        EntryField(controls, "Pulses for full span", self.total_var, "At least one for every day").grid(row=0, column=3, columnspan=3, sticky="ew", padx=(6, 14), pady=(12, 6))
        self.start_picker = TimePicker(controls, "Start time", repo.start_time)
        self.start_picker.grid(row=1, column=0, columnspan=3, sticky="ew", padx=(14, 6), pady=(6, 12))
        self.end_picker = TimePicker(controls, "End time", repo.end_time)
        self.end_picker.grid(row=1, column=3, columnspan=3, sticky="ew", padx=(6, 14), pady=(6, 12))

        planner = tk.Frame(content, bg=BG)
        planner.grid(row=4, column=0, sticky="nsew")
        planner.grid_columnconfigure(0, weight=1)
        planner.grid_columnconfigure(1, minsize=220)
        planner.grid_rowconfigure(0, weight=1)

        self.calendar_shell = tk.Frame(planner, bg=PANEL, highlightthickness=1, highlightbackground=BORDER_SOFT)
        self.calendar_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.calendar_shell.grid_columnconfigure(0, weight=1)
        self.detail_shell = tk.Frame(planner, bg=PANEL, highlightthickness=1, highlightbackground=BORDER_SOFT, width=220)
        self.detail_shell.grid(row=0, column=1, sticky="nsew")
        self.detail_shell.grid_propagate(False)

        actions = tk.Frame(self, bg=PANEL, highlightthickness=1, highlightbackground=BORDER_SOFT)
        actions.grid(row=1, column=0, sticky="ew")
        actions.grid_columnconfigure(2, weight=1)
        GPButton(actions, "Generate span", self._generate, kind="primary", width=150).grid(row=0, column=0, padx=(20, 7), pady=13)
        GPButton(actions, "Clear plan", self._clear, kind="ghost", width=105).grid(row=0, column=1, padx=8, pady=15)
        GPButton(actions, "Cancel", self.destroy, kind="ghost", width=92).grid(row=0, column=3, padx=7, pady=13)
        GPButton(actions, "Save & start", self._save, kind="primary", width=135).grid(row=0, column=4, padx=(0, 20), pady=13)

        if self.plan:
            self.selected_date = sorted(self.plan)[0]
            self._render_calendar()
        else:
            self.after(40, lambda: self._generate(show_errors=False))
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Control-s>", lambda _event: self._save())

    def _generate(self, show_errors: bool = True) -> None:
        try:
            try:
                days = int(self.days_var.get().strip())
                total = int(self.total_var.get().strip())
            except ValueError as exc:
                raise ValueError("Days and total pulses must be whole numbers.") from exc
            start_time = self.start_picker.get()
            end_time = self.end_picker.get()
            self.plan = generate_automatic_plan(
                self.repo,
                self.start_date_var.get().strip(),
                days,
                total,
                start_time,
                end_time,
                variant=self.generation_variant,
            )
            self.generation_variant += 1
            self.selected_date = sorted(self.plan)[0]
            self._render_calendar()
        except ValueError as exc:
            if show_errors:
                messagebox.showerror("GitPulse — Automatic pulse span", str(exc), parent=self)

    def _render_calendar(self) -> None:
        for widget in self.calendar_shell.winfo_children():
            widget.destroy()
        header = tk.Frame(self.calendar_shell, bg=PANEL)
        header.pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(header, text="SPAN CALENDAR", bg=PANEL, fg=SUBTLE, font=(FONT, 7, "bold")).pack(anchor="w")
        counts = [len(times) for times in self.plan.values()]
        summary = "No plan"
        if counts:
            summary = f"{sum(counts)} span pulses  ·  {len(counts)} days  ·  {min(counts)}–{max(counts)} per day"
        tk.Label(header, text=summary, bg=PANEL, fg=ACCENT, font=(FONT, 8, "bold"), anchor="w").pack(fill="x", pady=(3, 0))

        if not self.plan:
            tk.Label(self.calendar_shell, text="No automatic plan yet.", bg=PANEL, fg=MUTED, font=(FONT, 10)).pack(anchor="w", padx=18, pady=28)
            self._render_day_detail()
            return

        calendar_body = tk.Frame(self.calendar_shell, bg=PANEL)
        calendar_body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        calendar_body.grid_rowconfigure(0, weight=1)
        calendar_body.grid_columnconfigure(0, weight=1)
        calendar_canvas = tk.Canvas(calendar_body, bg=PANEL, highlightthickness=0, bd=0)
        calendar_canvas.grid(row=0, column=0, sticky="nsew")
        calendar_scroll = ttk.Scrollbar(calendar_body, orient="vertical", command=calendar_canvas.yview, style="GitPulse.Vertical.TScrollbar")
        calendar_scroll.grid(row=0, column=1, sticky="ns", padx=(5, 0))
        calendar_canvas.configure(yscrollcommand=calendar_scroll.set)
        grid = tk.Frame(calendar_canvas, bg=PANEL)
        grid_window = calendar_canvas.create_window((0, 0), window=grid, anchor="nw")
        grid.bind("<Configure>", lambda _event: calendar_canvas.configure(scrollregion=calendar_canvas.bbox("all")))
        calendar_canvas.bind("<Configure>", lambda event: calendar_canvas.itemconfigure(grid_window, width=event.width))
        calendar_canvas.bind("<MouseWheel>", lambda event: calendar_canvas.yview_scroll(int(-event.delta / 120), "units"))
        for column, weekday in enumerate(("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")):
            grid.grid_columnconfigure(column, weight=1, uniform="calendar-day")
            tk.Label(grid, text=weekday, bg=PANEL, fg=SUBTLE, font=(FONT, 7, "bold")).grid(row=0, column=column, sticky="ew", pady=(0, 6))

        ordered = sorted(self.plan)
        first = datetime.strptime(ordered[0], "%Y-%m-%d").date()
        offset = first.weekday()
        for index, date_key in enumerate(ordered):
            date_value = datetime.strptime(date_key, "%Y-%m-%d").date()
            times = self.plan[date_key]
            position = offset + index
            row, column = divmod(position, 7)
            selected = date_key == self.selected_date
            tile = tk.Button(
                grid,
                text=f"{date_value.strftime('%b %d').upper()}\n{len(times)} pulse{'s' if len(times) != 1 else ''}",
                command=lambda value=date_key: self._select_day(value),
                bg=ACCENT_DARK if selected else PANEL_2,
                fg=TEXT if selected else MUTED,
                activebackground=ACCENT_DARK,
                activeforeground=TEXT,
                relief="flat",
                bd=0,
                highlightthickness=1,
                highlightbackground=ACCENT if selected else BORDER_SOFT,
                font=(FONT, 8, "bold" if selected else "normal"),
                justify="left",
                anchor="nw",
                padx=3,
                pady=7,
                wraplength=52,
                cursor="hand2",
            )
            tile.grid(row=row + 1, column=column, sticky="nsew", padx=3, pady=3, ipady=5)
            tile.bind("<MouseWheel>", lambda event: calendar_canvas.yview_scroll(int(-event.delta / 120), "units"))
            grid.grid_rowconfigure(row + 1, minsize=67)
        self._render_day_detail()

    def _select_day(self, date_key: str) -> None:
        self.selected_date = date_key
        self._render_calendar()

    def _render_day_detail(self) -> None:
        for widget in self.detail_shell.winfo_children():
            widget.destroy()
        tk.Label(self.detail_shell, text="DAY SCHEDULE", bg=PANEL, fg=SUBTLE, font=(FONT, 7, "bold")).pack(anchor="w", padx=16, pady=(16, 4))
        if not self.selected_date or self.selected_date not in self.plan:
            tk.Label(self.detail_shell, text="Select a planned date.", bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=16, pady=12)
            return
        date_value = datetime.strptime(self.selected_date, "%Y-%m-%d").date()
        tk.Label(
            self.detail_shell,
            text=f"{date_value.strftime('%A')}\n{date_value.strftime('%B')} {date_value.day}, {date_value.year}",
            bg=PANEL,
            fg=TEXT,
            font=(FONT, 13, "bold"),
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 10))
        times = self.plan[self.selected_date]
        tk.Label(self.detail_shell, text=f"{len(times)} pulses planned", bg=PANEL, fg=ACCENT, font=(FONT, 8, "bold")).pack(anchor="w", padx=16, pady=(0, 9))
        list_shell = tk.Frame(self.detail_shell, bg=PANEL_2)
        list_shell.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        listing = tk.Text(list_shell, bg=PANEL_2, fg=TEXT, relief="flat", bd=0, font=(MONO, 9), padx=12, pady=10, height=20, cursor="arrow", wrap="none")
        scrollbar = ttk.Scrollbar(list_shell, orient="vertical", command=listing.yview, style="GitPulse.Vertical.TScrollbar")
        listing.configure(yscrollcommand=scrollbar.set)
        listing.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        listing.insert("1.0", "\n".join(f"{index:02d}   {format_hhmm_12(value)}" for index, value in enumerate(times, start=1)))
        listing.configure(state="disabled")

    def _clear(self) -> None:
        self.plan = {}
        self.selected_date = None
        self._render_calendar()

    def _save(self) -> None:
        self.repo.calendar_plan = dict(self.plan)
        if self.plan:
            self.repo.enabled = True
        self.result = self.repo
        self.destroy()


class LocalSyncDialog(tk.Toplevel):
    """Focused setup for the opt-in local repository automation."""

    def __init__(self, parent: "GitPulseApp", repo: RepoConfig) -> None:
        super().__init__(parent)
        self.result: RepoConfig | None = None
        self.repo = RepoConfig.from_dict(repo.to_dict())
        self.title("GitPulse — Hourly Sync")
        self.geometry("660x640")
        self.minsize(620, 610)
        self.resizable(True, False)
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        try:
            self.iconbitmap(str(resource_path("assets", "gitpulse.ico")))
        except Exception:
            pass

        self.enabled_var = tk.BooleanVar(value=self.repo.local_sync_enabled)
        self.path_var = tk.StringVar(value=self.repo.local_path)

        content = tk.Frame(self, bg=BG)
        content.grid(row=0, column=0, sticky="nsew", padx=34, pady=(28, 20))
        content.grid_columnconfigure(0, weight=1)

        tk.Label(content, text="HOURLY SYNC", bg=BG, fg=CYAN, font=(FONT, 8, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(content, text="Your staged work, pushed automatically.", bg=BG, fg=TEXT, font=(FONT, 23, "bold")).grid(row=1, column=0, sticky="w", pady=(4, 3))
        tk.Label(
            content,
            text="GitPulse checks once when you save this setup, then every hour. It commits only what you staged with git add.",
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            wraplength=570,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(0, 19))

        toggle = tk.Frame(content, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        toggle.grid(row=3, column=0, sticky="ew", pady=(0, 16))
        toggle.grid_columnconfigure(0, weight=1)
        tk.Label(toggle, text="Turn on Hourly Sync", bg=PANEL, fg=TEXT, font=(FONT, 11, "bold")).grid(row=0, column=0, sticky="w", padx=15, pady=(13, 2))
        tk.Label(toggle, text="The background worker starts automatically.", bg=PANEL, fg=MUTED, font=(FONT, 8)).grid(row=1, column=0, sticky="w", padx=15, pady=(0, 13))
        tk.Checkbutton(toggle, variable=self.enabled_var, bg=PANEL, activebackground=PANEL, selectcolor=PANEL_3, font=(FONT, 11)).grid(row=0, column=1, rowspan=2, padx=15)

        tk.Label(content, text="Local repository folder", bg=BG, fg=TEXT, font=(FONT, 9, "bold")).grid(row=4, column=0, sticky="w", pady=(0, 7))
        path_row = tk.Frame(content, bg=BG)
        path_row.grid(row=5, column=0, sticky="ew")
        path_row.grid_columnconfigure(0, weight=1)
        path_shell = tk.Frame(path_row, bg=PANEL_2, highlightthickness=1, highlightbackground=BORDER_SOFT)
        path_shell.grid(row=0, column=0, sticky="ew", padx=(0, 9))
        tk.Entry(path_shell, textvariable=self.path_var, bg=PANEL_2, fg=TEXT, insertbackground=ACCENT, relief="flat", bd=0, font=(FONT, 9)).pack(fill="x", padx=12, pady=11)
        GPButton(path_row, "Browse", self._browse, kind="ghost", width=92, height=42).grid(row=0, column=1)

        guide = tk.Frame(content, bg=PANEL, highlightthickness=1, highlightbackground=BORDER_SOFT)
        guide.grid(row=6, column=0, sticky="ew", pady=(18, 0))
        tk.Label(guide, text="HOW IT WORKS", bg=PANEL, fg=SUBTLE, font=(FONT, 7, "bold")).pack(anchor="w", padx=15, pady=(13, 7))
        for number, title in (("1", "Stage files normally with git add"), ("2", "GitPulse checks the folder every 60 minutes"), ("3", "Your staged snapshot is committed and pushed")):
            row = tk.Frame(guide, bg=PANEL)
            row.pack(fill="x", padx=15, pady=(0, 8))
            tk.Label(row, text=number, bg=ACCENT_DARK, fg=ACCENT, font=(FONT, 8, "bold"), width=3, pady=3).pack(side="left", padx=(0, 9))
            tk.Label(row, text=title, bg=PANEL, fg=TEXT, font=(FONT, 9)).pack(side="left")
        tk.Label(guide, text="GitPulse never stages, resets, or edits your local files.", bg=PANEL, fg=CYAN, font=(FONT, 8, "bold")).pack(anchor="w", padx=15, pady=(2, 13))

        actions = tk.Frame(self, bg=PANEL, highlightthickness=1, highlightbackground=BORDER_SOFT)
        actions.grid(row=1, column=0, sticky="ew")
        actions.grid_columnconfigure(0, weight=1)
        GPButton(actions, "Cancel", self.destroy, kind="ghost", width=96).grid(row=0, column=1, padx=(0, 8), pady=16)
        GPButton(actions, "Save & check now", self._save, kind="primary", width=166).grid(row=0, column=2, padx=(0, 24), pady=16)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Control-s>", lambda _event: self._save())
        self.after(100, self.focus_force)

    def _browse(self) -> None:
        options = {"parent": self, "title": "Choose your local Git repository"}
        initial = self.path_var.get().strip()
        if initial:
            options["initialdir"] = initial
        selected = filedialog.askdirectory(**options)
        if selected:
            self.path_var.set(selected)

    def _save(self) -> None:
        self.repo.local_sync_enabled = bool(self.enabled_var.get())
        self.repo.local_path = self.path_var.get().strip()
        errors = validate_local_sync(self.repo)
        if errors:
            messagebox.showerror("GitPulse — Hourly Sync", "\n\n".join(errors), parent=self)
            return
        if self.repo.local_sync_enabled:
            try:
                root, _branch, _staged = GitService().inspect_local_repository(self.repo)
                self.repo.local_path = str(root)
            except Exception as exc:
                messagebox.showerror("GitPulse — Hourly Sync", str(exc), parent=self)
                return
        self.result = self.repo
        self.destroy()


class GitPulseApp(tk.Tk):
    def __init__(self, demo: bool = False) -> None:
        super().__init__()
        self.demo = demo
        self.title("GitPulse — Life support for GitHub.")
        self.geometry("1280x820")
        self.minsize(1120, 720)
        self.configure(bg=BG)
        try:
            self.iconbitmap(str(resource_path("assets", "gitpulse.ico")))
        except Exception:
            pass
        self.config_data = self._demo_config() if demo else load_config()
        self.selected_repo_id: str | None = self.config_data.repositories[0].id if self.config_data.repositories else None
        self.status_var = tk.StringVar(value="Worker offline")
        self.summary_var = tk.StringVar(value="No repositories connected")
        self.auto_var = tk.BooleanVar(value=self.config_data.start_with_windows)
        self._history_demo = self._demo_history() if demo else None
        self._history_current: list[dict] = []
        self._content_fingerprint = ""
        self.logo_img: tk.PhotoImage | None = None
        self.worker_badge: tk.Label | None = None
        self.pulse_button: GPButton | None = None
        self._pulse_busy_repo_id: str | None = None
        self._configure_styles()
        self._build_layout()
        self._refresh_all()
        if not demo:
            self.after(500, self._auto_start_worker)
            self.after(2500, self._poll)

    @staticmethod
    def _demo_config() -> AppConfig:
        return AppConfig(repositories=[
            RepoConfig(id="demo-gitpulse", name="GitPulse", repo_url="https://github.com/anamta-JINX/GitPulse-LifeSupportForGitHub", commit_email="anamta.gohar25@gmail.com", commits_per_day=20, start_time="10:00", end_time="23:59", enabled=True, local_path=r"D:\Nerd Stuff\Projects\GitPulse", local_sync_enabled=True),
            RepoConfig(id="demo-icebreaker", name="IceBreaker — Your Wingman for LinkedIn", repo_url="https://github.com/anamta-JINX/IceBreaker-YourWingmanForLinkedIn", commit_email="anamta.gohar25@gmail.com", commits_per_day=8, start_time="12:00", end_time="22:30", enabled=True),
        ], start_with_windows=True)

    @staticmethod
    def _demo_history() -> list[dict]:
        date = datetime.now().astimezone().strftime("%Y-%m-%d")
        return [
            {"timestamp": f"{date}T20:41:18+05:00", "repo_name": "GitPulse", "pulse": "11/20", "commit": "a91c42d7f0", "status": "Pushed", "commit_url": ""},
            {"timestamp": f"{date}T20:06:02+05:00", "repo_name": "IceBreaker", "pulse": "5/8", "commit": "76b1a0358d", "status": "Pushed", "commit_url": ""},
            {"timestamp": f"{date}T19:52:43+05:00", "repo_name": "GitPulse", "pulse": "10/20", "commit": "35b07f55ce", "status": "Pushed", "commit_url": ""},
            {"timestamp": f"{date}T19:17:11+05:00", "repo_name": "GitPulse", "pulse": "9/20", "commit": "8f66d4f731", "status": "Pushed", "commit_url": ""},
        ]

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("GitPulse.Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=38, borderwidth=0, font=(FONT, 9))
        style.configure("GitPulse.Treeview.Heading", background=PANEL_2, foreground=MUTED, relief="flat", borderwidth=0, font=(FONT, 8, "bold"))
        style.map("GitPulse.Treeview", background=[("selected", ACCENT_DARK)], foreground=[("selected", TEXT)])
        style.map("GitPulse.Treeview.Heading", background=[("active", PANEL_3)])
        style.configure("GitPulse.Vertical.TScrollbar", background=PANEL_3, troughcolor=PANEL, bordercolor=PANEL, arrowcolor=MUTED, darkcolor=PANEL_3, lightcolor=PANEL_3)
        style.configure(
            "GitPulse.TCombobox",
            foreground=TEXT,
            fieldbackground=PANEL_2,
            background=PANEL_3,
            arrowcolor=ACCENT,
            bordercolor=BORDER_SOFT,
            lightcolor=BORDER_SOFT,
            darkcolor=BORDER_SOFT,
            padding=5,
        )
        style.map(
            "GitPulse.TCombobox",
            foreground=[("readonly", TEXT)],
            fieldbackground=[("readonly", PANEL_2)],
            selectforeground=[("readonly", TEXT)],
            selectbackground=[("readonly", PANEL_2)],
        )
        self.option_add("*TCombobox*Listbox.background", PANEL_2)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DARK)
        self.option_add("*TCombobox*Listbox.selectForeground", TEXT)

    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_sidebar()
        self._build_main()

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=BG, height=82)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=28, pady=(17, 12))
        header.grid_columnconfigure(1, weight=1)
        try:
            self.logo_img = tk.PhotoImage(file=str(resource_path("assets", "gitpulse-logo.png"))).subsample(9, 9)
            tk.Label(header, image=self.logo_img, bg=BG).grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 13))
        except Exception:
            tk.Label(header, text="GP", bg=ACCENT, fg=BG, font=(FONT, 13, "bold"), padx=9, pady=7).grid(row=0, column=0, rowspan=2, padx=(0, 13))
        tk.Label(header, text=APP_NAME, bg=BG, fg=TEXT, font=(FONT, 21, "bold")).grid(row=0, column=1, sticky="sw")
        tk.Label(header, text=TAGLINE, bg=BG, fg=MUTED, font=(FONT, 9)).grid(row=1, column=1, sticky="nw", pady=(1, 0))
        tk.Label(header, text=f"v{VERSION}", bg=BG, fg=SUBTLE, font=(MONO, 8)).grid(row=0, column=2, rowspan=2, padx=(0, 16))
        self.worker_badge = tk.Label(header, textvariable=self.status_var, bg=PANEL_3, fg=MUTED, font=(FONT, 8, "bold"), padx=14, pady=8)
        self.worker_badge.grid(row=0, column=3, rowspan=2, sticky="e")

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self, bg=SIDEBAR, highlightthickness=1, highlightbackground=BORDER_SOFT, width=316)
        sidebar.grid(row=1, column=0, sticky="nsew", padx=(28, 10), pady=(0, 26))
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(2, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)
        top = tk.Frame(sidebar, bg=SIDEBAR)
        top.grid(row=0, column=0, sticky="ew", padx=17, pady=(18, 8))
        top.grid_columnconfigure(0, weight=1)
        tk.Label(top, text="Repositories", bg=SIDEBAR, fg=TEXT, font=(FONT, 13, "bold")).grid(row=0, column=0, sticky="w")
        GPButton(top, "+  Add", self._add_repo, kind="primary", width=78, height=36).grid(row=0, column=1, sticky="e")
        tk.Label(sidebar, textvariable=self.summary_var, bg=SIDEBAR, fg=MUTED, font=(FONT, 8)).grid(row=1, column=0, sticky="w", padx=17, pady=(0, 9))

        list_shell = tk.Frame(sidebar, bg=SIDEBAR)
        list_shell.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        list_shell.grid_rowconfigure(0, weight=1)
        list_shell.grid_columnconfigure(0, weight=1)
        self.repo_canvas = tk.Canvas(list_shell, bg=SIDEBAR, highlightthickness=0, bd=0)
        self.repo_canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_shell, orient="vertical", command=self.repo_canvas.yview, style="GitPulse.Vertical.TScrollbar")
        scroll.grid(row=0, column=1, sticky="ns")
        self.repo_canvas.configure(yscrollcommand=scroll.set)
        self.repo_list = tk.Frame(self.repo_canvas, bg=SIDEBAR)
        self.repo_window = self.repo_canvas.create_window((0, 0), window=self.repo_list, anchor="nw")
        self.repo_list.bind("<Configure>", lambda _event: self.repo_canvas.configure(scrollregion=self.repo_canvas.bbox("all")))
        self.repo_canvas.bind("<Configure>", lambda event: self.repo_canvas.itemconfigure(self.repo_window, width=event.width))

        automation = tk.Frame(sidebar, bg=PANEL, highlightthickness=1, highlightbackground=BORDER_SOFT)
        automation.grid(row=3, column=0, sticky="ew", padx=13, pady=(0, 13))
        tk.Label(automation, text="AUTOMATION", bg=PANEL, fg=SUBTLE, font=(FONT, 7, "bold")).pack(anchor="w", padx=14, pady=(13, 2))
        tk.Label(automation, text="Background life support", bg=PANEL, fg=TEXT, font=(FONT, 10, "bold")).pack(anchor="w", padx=14)
        tk.Checkbutton(automation, text="Start in background with Windows", variable=self.auto_var, command=self._toggle_autostart, bg=PANEL, fg=MUTED, activebackground=PANEL, activeforeground=TEXT, selectcolor=PANEL_3, font=(FONT, 8)).pack(anchor="w", padx=11, pady=(8, 7))
        controls = tk.Frame(automation, bg=PANEL)
        controls.pack(fill="x", padx=12, pady=(0, 8))
        GPButton(controls, "Start", self._start_worker, kind="primary", width=124, height=39).pack(side="left", fill="x", expand=True, padx=(0, 4))
        GPButton(controls, "Stop", self._stop_worker, kind="danger", width=78, height=39).pack(side="left", padx=(4, 0))
        GPButton(automation, "Complete all repos now", self._complete_all, width=252, height=38).pack(fill="x", padx=12, pady=(0, 12))

    def _build_main(self) -> None:
        main = tk.Frame(self, bg=BG)
        main.grid(row=1, column=1, sticky="nsew", padx=(10, 28), pady=(0, 26))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)
        intro = tk.Frame(main, bg=BG)
        intro.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        intro.grid_columnconfigure(0, weight=1)
        tk.Label(intro, text="Command center", bg=BG, fg=TEXT, font=(FONT, 19, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(intro, text="Safe contribution pulses and local-project sync in one place.", bg=BG, fg=MUTED, font=(FONT, 9)).grid(row=1, column=0, sticky="w", pady=(2, 0))
        GPButton(intro, "Refresh", self._refresh_all, kind="ghost", width=90, height=35).grid(row=0, column=1, rowspan=2, sticky="e")

        self.detail = tk.Frame(main, bg=PANEL, highlightthickness=1, highlightbackground=BORDER_SOFT)
        self.detail.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.detail.grid_columnconfigure(0, weight=1)
        activity = tk.Frame(main, bg=PANEL, highlightthickness=1, highlightbackground=BORDER_SOFT)
        activity.grid(row=2, column=0, sticky="nsew")
        activity.grid_rowconfigure(1, weight=1)
        activity.grid_columnconfigure(0, weight=1)
        activity_header = tk.Frame(activity, bg=PANEL)
        activity_header.grid(row=0, column=0, sticky="ew", padx=19, pady=(15, 9))
        activity_header.grid_columnconfigure(0, weight=1)
        tk.Label(activity_header, text="Recent activity", bg=PANEL, fg=TEXT, font=(FONT, 13, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(activity_header, text="Double-click a row for details", bg=PANEL, fg=SUBTLE, font=(FONT, 8)).grid(row=0, column=1, sticky="e")
        columns = ("time", "repo", "pulse", "commit", "status")
        self.history_tree = ttk.Treeview(activity, columns=columns, show="headings", style="GitPulse.Treeview", selectmode="browse")
        for key, title in zip(columns, ("TIME", "REPOSITORY", "PULSE", "COMMIT", "STATUS")):
            self.history_tree.heading(key, text=title)
        self.history_tree.column("time", width=150, minwidth=125, anchor="w")
        self.history_tree.column("repo", width=280, minwidth=170, anchor="w")
        self.history_tree.column("pulse", width=95, minwidth=70, anchor="center")
        self.history_tree.column("commit", width=130, minwidth=90, anchor="center")
        self.history_tree.column("status", width=105, minwidth=80, anchor="center")
        self.history_tree.tag_configure("pushed", foreground=ACCENT)
        self.history_tree.tag_configure("failed", foreground=DANGER)
        self.history_tree.tag_configure("complete", foreground=CYAN)
        self.history_tree.grid(row=1, column=0, sticky="nsew", padx=19, pady=(0, 16))
        self.history_tree.bind("<Double-1>", self._open_history_commit)

    def _demo_state(self) -> dict:
        return {"date": datetime.now().astimezone().strftime("%Y-%m-%d"), "repos": {
            "demo-gitpulse": {"times": ["10:14", "10:56", "11:29", "12:18", "13:04", "13:51", "14:38", "15:09", "16:02", "16:47", "17:21", "17:59", "18:41", "19:22", "20:08", "20:44", "21:31", "22:10", "22:52", "23:37"], "done": [str(i) for i in range(1, 12)], "last_error": "", "local_sync_status": "No staged changes"},
            "demo-icebreaker": {"times": ["12:25", "13:47", "15:02", "16:28", "18:05", "19:39", "21:00", "22:14"], "done": [str(i) for i in range(1, 6)], "last_error": ""},
        }}

    def _selected_repo(self) -> RepoConfig | None:
        return next((repo for repo in self.config_data.repositories if repo.id == self.selected_repo_id), None)

    def _state(self) -> dict:
        return self._demo_state() if self.demo else ensure_today_state(self.config_data)

    def _refresh_repo_list(self, state: dict | None = None) -> None:
        for widget in self.repo_list.winfo_children():
            widget.destroy()
        state = state or self._state()
        active = [repo for repo in self.config_data.repositories if repo.enabled or repo.local_sync_enabled]
        pulse_repos = [repo for repo in self.config_data.repositories if repo.enabled]
        total_target = sum(_target_for_state(repo, state) for repo in pulse_repos)
        total_done = sum(len(state.get("repos", {}).get(repo.id, {}).get("done", [])) for repo in pulse_repos)
        self.summary_var.set(f"{len(active)} active  ·  {total_done}/{total_target} pulses" if self.config_data.repositories else "No repositories connected")
        for repo in self.config_data.repositories:
            selected = repo.id == self.selected_repo_id
            card_bg = ACCENT_DARK if selected else PANEL
            card = tk.Frame(self.repo_list, bg=card_bg, highlightthickness=1, highlightbackground=ACCENT_2 if selected else BORDER_SOFT, cursor="hand2")
            card.pack(fill="x", padx=2, pady=5)
            card.grid_columnconfigure(0, weight=1)
            item = state.get("repos", {}).get(repo.id, {})
            done = len(item.get("done", []))
            target = _target_for_state(repo, state)
            title = repo.name or parse_repo_url(repo.repo_url)[1]
            title_label = SlidingLabel(card, title, (FONT, 10, "bold"), card_bg, TEXT, height=24, cursor="hand2")
            title_label.grid(row=0, column=0, sticky="ew", padx=12, pady=(7, 0))
            sync_marker = "   ·   hourly sync" if repo.local_sync_enabled else ""
            calendar_marker = "   ·   pulse span" if repo.calendar_plan else ""
            next_display = _next_pulse_display(repo, state)
            next_phrase = next_display if next_display.startswith(("Starts", "Span")) else f"{next_display} next"
            tk.Label(card, text=f"{done}/{target} today   ·   {next_phrase}{sync_marker}{calendar_marker}", bg=card_bg, fg=MUTED, font=(FONT, 8), cursor="hand2").grid(row=1, column=0, sticky="w", padx=12, pady=(0, 8))
            tk.Label(card, text="●", bg=card_bg, fg=ACCENT if (repo.enabled or repo.local_sync_enabled) else SUBTLE, font=(FONT, 8)).grid(row=0, column=1, rowspan=2, sticky="e", padx=11)
            ProgressBar(card, done / max(1, target), height=5).grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
            for widget in (card, *card.winfo_children()):
                widget.bind("<Button-1>", lambda _event, repo_id=repo.id: self._select_repo(repo_id))

    def _build_detail_content(self, state: dict | None = None) -> None:
        for widget in self.detail.winfo_children():
            widget.destroy()
        self.pulse_button = None
        repo = self._selected_repo()
        if not repo:
            empty = tk.Frame(self.detail, bg=PANEL)
            empty.pack(fill="both", expand=True, padx=28, pady=34)
            tk.Label(empty, text="Your GitHub life-support bay is empty", bg=PANEL, fg=TEXT, font=(FONT, 17, "bold")).pack(anchor="w")
            tk.Label(empty, text="Connect a repository, choose its daily pulse target, and let GitPulse manage the schedule.", bg=PANEL, fg=MUTED, font=(FONT, 9), wraplength=720, justify="left").pack(anchor="w", pady=(6, 17))
            GPButton(empty, "Connect repository", self._add_repo, kind="primary", width=158).pack(anchor="w")
            return
        state = state or self._state()
        item = state.get("repos", {}).get(repo.id, {})
        done = len(item.get("done", []))
        target = _target_for_state(repo, state)
        next_time = _next_pulse_display(repo, state)
        last_error = str(item.get("last_error", "") or "")
        local_sync_error = str(item.get("local_sync_error", "") or "")
        local_sync_status = str(item.get("local_sync_status", "Ready") or "Ready")
        if local_sync_status == "Waiting for first check":
            local_sync_status = "Ready"
        today_times = list(item.get("times", []))
        if str(state.get("date", "")) in repo.calendar_plan and today_times:
            time_window_display = f"{format_hhmm_12(today_times[0])} — {format_hhmm_12(today_times[-1])}"
        else:
            time_window_display = f"{format_hhmm_12(repo.start_time)} — {format_hhmm_12(repo.end_time)}"
        body = tk.Frame(self.detail, bg=PANEL)
        body.grid(row=0, column=0, sticky="ew", padx=20, pady=18)
        body.grid_columnconfigure(1, weight=1)
        ProgressRing(body, done, target, size=142).grid(row=0, column=0, rowspan=4, padx=(0, 22), sticky="nw")
        title_row = tk.Frame(body, bg=PANEL)
        title_row.grid(row=0, column=1, sticky="ew")
        title_row.grid_columnconfigure(0, weight=1)
        detail_title = SlidingLabel(title_row, repo.name or parse_repo_url(repo.repo_url)[1], (FONT, 18, "bold"), PANEL, TEXT, height=38)
        detail_title.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        active = repo.enabled or repo.local_sync_enabled
        badge = " ACTIVE " if active else " PAUSED "
        tk.Label(title_row, text=badge, bg=ACCENT_DARK if active else PANEL_3, fg=ACCENT if active else MUTED, font=(FONT, 7, "bold"), padx=7, pady=5).grid(row=0, column=1, sticky="e")
        tk.Label(body, text=repo.repo_url, bg=PANEL, fg=MUTED, font=(FONT, 8)).grid(row=1, column=1, sticky="w", pady=(2, 12))
        metrics = tk.Frame(body, bg=PANEL)
        metrics.grid(row=2, column=1, sticky="ew")
        for index in range(4):
            metrics.grid_columnconfigure(index, weight=1, uniform="metric")
        self._metric(metrics, 0, "NEXT PULSE", next_time, ACCENT)
        self._metric(metrics, 1, "TIME WINDOW", time_window_display, TEXT)
        self._metric(metrics, 2, "BRANCH", repo.branch or "Default", TEXT)
        self._metric(metrics, 3, "HOURLY SYNC", local_sync_status if repo.local_sync_enabled else "Off", CYAN if repo.local_sync_enabled else MUTED)
        issues: list[str] = []
        if last_error:
            issues.append(f"Pulse issue: {last_error}")
        if local_sync_error:
            issues.append(f"Hourly Sync issue: {local_sync_error}")
        if issues:
            tk.Label(body, text="\n".join(issues), bg=DANGER_BG, fg=DANGER, font=(FONT, 8), anchor="w", justify="left", wraplength=790, padx=10, pady=7).grid(row=3, column=1, sticky="ew", pady=(9, 0))
        actions = tk.Frame(body, bg=PANEL)
        actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(17, 0))
        self.pulse_button = GPButton(actions, "Pulse now", self._pulse_selected, kind="primary", width=105)
        self.pulse_button.pack(side="left", padx=(0, 7))
        if self._pulse_busy_repo_id == repo.id:
            self.pulse_button.set_loading(True, "Pulsing")
        elif self._pulse_busy_repo_id:
            self.pulse_button.enabled = False
            self.pulse_button._draw()
        GPButton(actions, "Hourly sync", self._hourly_sync_settings, width=105).pack(side="left", padx=(0, 7))
        GPButton(actions, "Pulse span", self._calendar_settings, width=90).pack(side="left", padx=(0, 7))
        GPButton(actions, "Complete today", self._complete_selected, width=118).pack(side="left", padx=(0, 7))
        GPButton(actions, "Open GitHub", lambda: webbrowser.open(repo.repo_url), width=100).pack(side="left", padx=(0, 7))
        GPButton(actions, "Edit", self._edit_selected, kind="ghost", width=65).pack(side="left", padx=(0, 7))
        GPButton(actions, "Remove", self._remove_selected, kind="danger", width=74).pack(side="left")

    def _metric(self, parent, column: int, label: str, value: str, value_color: str) -> None:
        box = tk.Frame(parent, bg=PANEL_2, highlightthickness=1, highlightbackground=BORDER_SOFT)
        box.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 5, 0 if column == 3 else 5))
        tk.Label(box, text=label, bg=PANEL_2, fg=SUBTLE, font=(FONT, 7, "bold")).pack(anchor="w", padx=12, pady=(9, 2))
        tk.Label(box, text=value, bg=PANEL_2, fg=value_color, font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(0, 10))

    def _refresh_history(self) -> None:
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        history = self._history_demo if self._history_demo is not None else load_history(300)
        self._history_current = history or []
        for entry in self._history_current:
            raw_time = str(entry.get("timestamp", ""))
            display_time = raw_time
            try:
                display_time = format_datetime_12(datetime.fromisoformat(raw_time).astimezone())
            except Exception:
                pass
            status = str(entry.get("status", ""))
            tag = status.lower() if status.lower() in {"pushed", "failed", "complete"} else ""
            self.history_tree.insert("", "end", values=(display_time, entry.get("repo_name", ""), entry.get("pulse", ""), entry.get("commit", "") or "—", status), tags=(tag,))

    def _open_history_commit(self, _event=None) -> None:
        selected = self.history_tree.selection()
        if selected:
            index = self.history_tree.index(selected[0])
            if 0 <= index < len(self._history_current):
                url = self._history_current[index].get("commit_url")
                if url:
                    webbrowser.open(url)
                else:
                    entry = self._history_current[index]
                    message = str(entry.get("message", "") or "No additional details were recorded.")
                    title = "GitPulse — Sync details" if entry.get("mode") in {"Hourly local sync", "Manual local sync", "Setup check"} else "GitPulse — Activity details"
                    if str(entry.get("status", "")).lower() == "failed":
                        messagebox.showerror(title, message, parent=self)
                    else:
                        messagebox.showinfo(title, message, parent=self)

    def _refresh_worker_status(self) -> None:
        running = True if self.demo else worker_is_current()
        self.status_var.set("Worker online" if running else "Worker offline")
        if self.worker_badge:
            self.worker_badge.configure(bg=ACCENT_DARK if running else PANEL_3, fg=ACCENT if running else MUTED)

    def _fingerprint(self, state: dict) -> str:
        history = self._history_demo if self._history_demo is not None else load_history(300)
        return json.dumps({"config": self.config_data.to_dict(), "state": state, "history": history}, sort_keys=True, default=str)

    def _refresh_all(self) -> None:
        self._refresh_worker_status()
        state = self._state()
        self._refresh_repo_list(state)
        self._build_detail_content(state)
        self._refresh_history()
        self._content_fingerprint = self._fingerprint(state)

    def _poll(self) -> None:
        self.config_data = load_config()
        if self.selected_repo_id and not any(repo.id == self.selected_repo_id for repo in self.config_data.repositories):
            self.selected_repo_id = self.config_data.repositories[0].id if self.config_data.repositories else None
        self.auto_var.set(self.config_data.start_with_windows)
        self._refresh_worker_status()
        state = self._state()
        fingerprint = self._fingerprint(state)
        if fingerprint != self._content_fingerprint:
            self._refresh_repo_list(state)
            self._build_detail_content(state)
            self._refresh_history()
            self._content_fingerprint = fingerprint
        self.after(3500, self._poll)

    def _select_repo(self, repo_id: str) -> None:
        self.selected_repo_id = repo_id
        state = self._state()
        self._refresh_repo_list(state)
        self._build_detail_content(state)

    def _add_repo(self) -> None:
        if self.demo:
            return
        dialog = RepoDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            self.config_data.repositories.append(dialog.result)
            self.selected_repo_id = dialog.result.id
            save_config(self.config_data)
            ensure_today_state(self.config_data)
            self._refresh_all()

    def _edit_selected(self) -> None:
        if self.demo:
            return
        repo = self._selected_repo()
        if not repo:
            return
        dialog = RepoDialog(self, repo)
        self.wait_window(dialog)
        if dialog.result:
            self.config_data.repositories = [dialog.result if item.id == repo.id else item for item in self.config_data.repositories]
            save_config(self.config_data)
            ensure_today_state(self.config_data)
            self._refresh_all()

    def _remove_selected(self) -> None:
        if self.demo:
            return
        repo = self._selected_repo()
        if not repo:
            return
        label = repo.name or repo.repo_url
        if not messagebox.askyesno("GitPulse", f"Remove {label} from GitPulse?\n\nThe GitHub repository itself will not be deleted.", parent=self):
            return
        self.config_data.repositories = [item for item in self.config_data.repositories if item.id != repo.id]
        save_config(self.config_data)
        self.selected_repo_id = self.config_data.repositories[0].id if self.config_data.repositories else None
        threading.Thread(target=lambda: GitService().remove_cache(repo), daemon=True).start()
        self._refresh_all()

    def _run_async(self, status: str, work, success_message=None, on_finish=None) -> None:
        self.status_var.set(status)
        if self.worker_badge:
            self.worker_badge.configure(bg=WARNING_BG, fg=WARNING)

        def runner() -> None:
            try:
                result = work()

                def ok() -> None:
                    if on_finish:
                        on_finish()
                    self._refresh_all()
                    if success_message:
                        messagebox.showinfo("GitPulse", success_message(result), parent=self)
                self.after(0, ok)
            except Exception as exc:
                details = str(exc)

                def fail(error_message: str = details) -> None:
                    if on_finish:
                        on_finish()
                    self._refresh_all()
                    messagebox.showerror("GitPulse", error_message, parent=self)
                self.after(0, fail)
        threading.Thread(target=runner, daemon=True).start()

    def _auto_start_worker(self) -> None:
        if self.demo:
            return
        if self.config_data.start_with_windows:
            try:
                set_start_with_windows(True)
            except Exception as exc:
                write_log(f"Windows startup registration failed: {exc}")
        existing = worker_pid()
        if existing and worker_is_current(existing):
            return
        has_automation = any(repo.enabled or repo.local_sync_enabled for repo in self.config_data.repositories)
        if not existing and not has_automation:
            return
        self.status_var.set("Worker starting…")
        if self.worker_badge:
            self.worker_badge.configure(bg=WARNING_BG, fg=WARNING)

        def start() -> None:
            try:
                launch_worker()
            except Exception as exc:
                details = str(exc)
                write_log(f"Automatic worker startup failed: {details}")

                def report_failure() -> None:
                    self._refresh_worker_status()
                    messagebox.showerror(
                        "GitPulse — Background worker",
                        f"The background worker could not start.\n\n{details}",
                        parent=self,
                    )

                self.after(0, report_failure)
            else:
                self.after(0, self._refresh_worker_status)

        threading.Thread(target=start, daemon=True).start()

    def _pulse_selected(self) -> None:
        if self.demo:
            return
        repo = self._selected_repo()
        if not repo or self._pulse_busy_repo_id:
            return
        self._pulse_busy_repo_id = repo.id
        if self.pulse_button:
            self.pulse_button.set_loading(True, "Pulsing")

        def finish() -> None:
            self._pulse_busy_repo_id = None
            if self.pulse_button:
                self.pulse_button.set_loading(False)

        self._run_async(
            "Sending pulse now…",
            lambda: run_one_pulse(repo, mode="Pulse now"),
            lambda result: result.message if not result.created else f"Pushed pulse {result.count}/{result.target} as {result.commit_hash}.",
            on_finish=finish,
        )

    def _calendar_settings(self) -> None:
        if self.demo:
            return
        repo = self._selected_repo()
        if not repo:
            return
        dialog = CalendarPlanDialog(self, repo)
        self.wait_window(dialog)
        if not dialog.result:
            return
        updated = dialog.result
        self.config_data.repositories = [updated if item.id == repo.id else item for item in self.config_data.repositories]
        if updated.calendar_plan:
            self.config_data.start_with_windows = True
            self.auto_var.set(True)
        save_config(self.config_data)
        if updated.calendar_plan:
            try:
                set_start_with_windows(True)
            except Exception as exc:
                messagebox.showerror("GitPulse — Windows startup", str(exc), parent=self)
        ensure_today_state(self.config_data)
        self._refresh_all()
        if updated.calendar_plan:
            self._auto_start_worker()

    def _hourly_sync_settings(self) -> None:
        if self.demo:
            return
        repo = self._selected_repo()
        if not repo:
            return
        dialog = LocalSyncDialog(self, repo)
        self.wait_window(dialog)
        if not dialog.result:
            return
        updated = dialog.result
        self.config_data.repositories = [updated if item.id == repo.id else item for item in self.config_data.repositories]
        save_config(self.config_data)
        ensure_today_state(self.config_data)
        self._refresh_all()
        if updated.local_sync_enabled:
            claim_local_sync_check(updated.id)
            self._auto_start_worker()
            self._run_async(
                "Checking staged changes…",
                lambda: run_local_sync(updated, mode="Setup check"),
                lambda result: f"Hourly Sync is on. Pushed staged changes as {result.commit_hash}." if result.pushed else "Hourly Sync is on. Nothing is staged yet.",
            )

    def _complete_selected(self) -> None:
        if self.demo:
            return
        repo = self._selected_repo()
        if not repo or not messagebox.askyesno("GitPulse", f"Create every remaining pulse for {repo.name or repo.repo_url} now?", parent=self):
            return
        self._run_async("Completing today…", lambda: run_complete_today(repo), lambda results: "Today's target is already complete." if not results else f"Created and pushed {len(results)} remaining pulse(s).")

    def _complete_all(self) -> None:
        if self.demo:
            return
        enabled = [repo for repo in self.config_data.repositories if repo.enabled]
        if not enabled:
            messagebox.showinfo("GitPulse", "There are no enabled repositories.", parent=self)
            return
        if not messagebox.askyesno("GitPulse", "Complete today's remaining target for every enabled repository?", parent=self):
            return
        self._run_async("Completing all…", lambda: complete_all_enabled(self.config_data), lambda results: "\n".join(next((repo.name or repo.repo_url for repo in enabled if repo.id == repo_id), repo_id) + ": " + message for repo_id, message in results.items()))

    def _start_worker(self) -> None:
        if not self.demo:
            self._run_async("Starting worker…", launch_worker, lambda _result: "Background worker started.")

    def _stop_worker(self) -> None:
        if not self.demo:
            self._run_async("Stopping worker…", stop_worker, lambda _result: "Background worker stopped.")

    def _toggle_autostart(self) -> None:
        if self.demo:
            return
        enabled = bool(self.auto_var.get())
        try:
            set_start_with_windows(enabled)
            self.config_data.start_with_windows = enabled
            save_config(self.config_data)
        except Exception as exc:
            self.auto_var.set(not enabled)
            messagebox.showerror("GitPulse", str(exc), parent=self)
