from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import messagebox, ttk

from .git_service import GitService
from .models import AppConfig, RepoConfig
from .resources import resource_path
from .scheduler import (
    complete_all_enabled,
    ensure_today_state,
    launch_worker,
    next_scheduled_time,
    run_complete_today,
    run_one_pulse,
    set_start_with_windows,
    stop_worker,
    validate_repo,
    worker_pid,
)
from .storage import load_config, load_history, save_config
from .utils import parse_repo_url

APP_NAME = "GreenPulse"
TAGLINE = "Touch grass. Digitally."
VERSION = "4.1.0"

BG = "#060A08"
PANEL = "#0B1510"
PANEL_2 = "#0E1A14"
PANEL_3 = "#122119"
BORDER = "#193727"
TEXT = "#F3F7F5"
MUTED = "#8FA69A"
ACCENT = "#00E676"
ACCENT_HOVER = "#00C968"
ACCENT_DARK = "#073A24"
DANGER = "#D65353"
DANGER_BG = "#40191D"
WARNING = "#D8B25C"

FONT = "Segoe UI"
MONO = "Cascadia Mono"


class GPButton(tk.Button):
    def __init__(
        self,
        master,
        text: str,
        command=None,
        kind: str = "secondary",
        width: int | None = None,
        **kwargs,
    ) -> None:
        if kind == "primary":
            bg, fg, active = ACCENT, "#031108", ACCENT_HOVER
        elif kind == "danger":
            bg, fg, active = DANGER_BG, "#FFD7DA", "#5A2329"
        elif kind == "ghost":
            bg, fg, active = PANEL, MUTED, PANEL_3
        else:
            bg, fg, active = PANEL_3, TEXT, BORDER
        super().__init__(
            master,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=active,
            activeforeground=fg,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER if kind != "primary" else ACCENT,
            highlightcolor=BORDER if kind != "primary" else ACCENT,
            cursor="hand2",
            font=(FONT, 10, "bold"),
            padx=14,
            pady=9,
            width=width,
            **kwargs,
        )


class EntryField(tk.Frame):
    def __init__(self, master, label: str, variable: tk.StringVar, hint: str = "", width: int | None = None) -> None:
        super().__init__(master, bg=BG)
        tk.Label(self, text=label, bg=BG, fg=TEXT, font=(FONT, 9, "bold")).pack(anchor="w", pady=(0, 5))
        self.entry = tk.Entry(
            self,
            textvariable=variable,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=(FONT, 10),
            width=width,
        )
        self.entry.pack(fill="x", ipady=9)
        if hint:
            tk.Label(self, text=hint, bg=BG, fg=MUTED, font=(FONT, 8)).pack(anchor="w", pady=(4, 0))


class RepoDialog(tk.Toplevel):
    def __init__(self, parent: "GreenPulseApp", repo: RepoConfig | None = None) -> None:
        super().__init__(parent)
        self.parent = parent
        self.original = repo
        self.result: RepoConfig | None = None
        self.title("GreenPulse — Repository setup")
        self.geometry("650x740")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.transient(parent)
        self.update_idletasks()
        try:
            x = parent.winfo_rootx() + max(20, (parent.winfo_width() - 650) // 2)
            y = parent.winfo_rooty() + max(20, (parent.winfo_height() - 740) // 2)
            self.geometry(f"650x740+{x}+{y}")
        except Exception:
            pass
        self.grab_set()
        try:
            self.iconbitmap(str(resource_path("assets", "greenpulse.ico")))
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
        self.start_var = tk.StringVar(value=item.start_time)
        self.end_var = tk.StringVar(value=item.end_time)
        self.branch_var = tk.StringVar(value=item.branch)
        self.enabled_var = tk.BooleanVar(value=item.enabled)
        self.status_var = tk.StringVar(value="")

        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=30, pady=26)

        tk.Label(outer, text="Repository setup", bg=BG, fg=TEXT, font=(FONT, 21, "bold")).pack(anchor="w")
        tk.Label(
            outer,
            text="Each repository gets its own schedule and private GreenPulse cache.",
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
        ).pack(anchor="w", pady=(4, 20))

        EntryField(outer, "Display name", self.name_var, "Shown only inside GreenPulse").pack(fill="x", pady=(0, 13))
        EntryField(outer, "GitHub repository", self.url_var, "https://github.com/username/repository").pack(fill="x", pady=(0, 13))
        EntryField(outer, "GitHub commit email", self.email_var, "Use an email associated with your GitHub account").pack(fill="x", pady=(0, 13))

        trio = tk.Frame(outer, bg=BG)
        trio.pack(fill="x", pady=(0, 13))
        for i in range(3):
            trio.grid_columnconfigure(i, weight=1)
        count_field = EntryField(trio, "Commits / day", self.count_var)
        start_field = EntryField(trio, "Start", self.start_var)
        end_field = EntryField(trio, "End", self.end_var)
        count_field.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        start_field.grid(row=0, column=1, sticky="ew", padx=8)
        end_field.grid(row=0, column=2, sticky="ew", padx=(8, 0))

        EntryField(outer, "Branch (optional)", self.branch_var, "Leave blank to use the repository default branch").pack(fill="x", pady=(0, 12))

        check = tk.Checkbutton(
            outer,
            text="Enable automatic commits for this repository",
            variable=self.enabled_var,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=PANEL_2,
            font=(FONT, 9),
        )
        check.pack(anchor="w", pady=(0, 12))

        tk.Label(
            outer,
            textvariable=self.status_var,
            bg=BG,
            fg=MUTED,
            justify="left",
            wraplength=590,
            font=(FONT, 8),
        ).pack(anchor="w", pady=(0, 12))

        buttons = tk.Frame(outer, bg=BG)
        buttons.pack(fill="x", side="bottom")
        GPButton(buttons, "Test connection", self._test_connection, kind="secondary").pack(side="left")
        GPButton(buttons, "Cancel", self.destroy, kind="ghost").pack(side="right", padx=(8, 0))
        GPButton(buttons, "Save repository", self._save, kind="primary").pack(side="right")

        self.bind("<Return>", lambda _event: self._save())
        self.after(100, self.focus_force)

    def _repo_from_form(self) -> RepoConfig:
        base_id = self.original.id if self.original else RepoConfig().id
        item = RepoConfig(
            id=base_id,
            name=self.name_var.get().strip(),
            repo_url=self.url_var.get().strip(),
            commit_email=self.email_var.get().strip(),
            commits_per_day=int(self.count_var.get().strip()),
            start_time=self.start_var.get().strip(),
            end_time=self.end_var.get().strip(),
            branch=self.branch_var.get().strip(),
            enabled=bool(self.enabled_var.get()),
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
        except ValueError:
            messagebox.showerror("GreenPulse", "Commits per day must be a whole number.", parent=self)
            return None
        errors = validate_repo(item)
        if errors:
            messagebox.showerror("GreenPulse", "\n\n".join(errors), parent=self)
            return None
        return item

    def _test_connection(self) -> None:
        item = self._validate_form()
        if not item:
            return
        self.status_var.set("Checking repository access...")

        def work() -> None:
            ok, message = GitService().test_connection(item)
            text = ("Connected. " if ok else "Could not connect. ") + message
            self.after(0, lambda: self.status_var.set(text))

        threading.Thread(target=work, daemon=True).start()

    def _save(self) -> None:
        item = self._validate_form()
        if not item:
            return
        self.result = item
        self.destroy()


class GreenPulseApp(tk.Tk):
    def __init__(self, demo: bool = False) -> None:
        super().__init__()
        self.demo = demo
        self.title("GreenPulse — Touch grass. Digitally.")
        self.geometry("1220x790")
        self.minsize(1080, 720)
        self.configure(bg=BG)
        try:
            self.iconbitmap(str(resource_path("assets", "greenpulse.ico")))
        except Exception:
            pass

        self.config_data = self._demo_config() if demo else load_config()
        self.selected_repo_id: str | None = self.config_data.repositories[0].id if self.config_data.repositories else None
        self.status_var = tk.StringVar(value="Stopped")
        self.summary_var = tk.StringVar(value="No repositories configured")
        self.auto_var = tk.BooleanVar(value=self.config_data.start_with_windows)
        self._history_demo = self._demo_history() if demo else None
        self._history_current: list[dict] = []
        self.logo_img: tk.PhotoImage | None = None
        self.worker_badge: tk.Label | None = None

        self._configure_tree_style()
        self._build_layout()
        self._refresh_all()
        if not demo:
            self.after(2400, self._poll)

    @staticmethod
    def _demo_config() -> AppConfig:
        return AppConfig(
            repositories=[
                RepoConfig(
                    id="demo-private",
                    name="Private",
                    repo_url="https://github.com/anamta-JINX/Private",
                    commit_email="anamta.gohar25@gmail.com",
                    commits_per_day=20,
                    start_time="10:00",
                    end_time="23:59",
                    enabled=True,
                ),
                RepoConfig(
                    id="demo-icebreaker",
                    name="IceBreaker",
                    repo_url="https://github.com/anamta-JINX/IceBreaker-YourWingmanForLinkedIn",
                    commit_email="anamta.gohar25@gmail.com",
                    commits_per_day=8,
                    start_time="12:00",
                    end_time="22:30",
                    enabled=True,
                ),
            ],
            start_with_windows=True,
        )

    @staticmethod
    def _demo_history() -> list[dict]:
        date = datetime.now().astimezone().strftime("%Y-%m-%d")
        return [
            {"timestamp": f"{date}T20:41:18+05:00", "repo_name": "Private", "repo_url": "https://github.com/anamta-JINX/Private", "pulse": "11/20", "commit": "a91c42d7f0", "status": "Pushed", "commit_url": "https://github.com/anamta-JINX/Private/commit/a91c42d7f0"},
            {"timestamp": f"{date}T20:06:02+05:00", "repo_name": "IceBreaker", "repo_url": "https://github.com/anamta-JINX/IceBreaker-YourWingmanForLinkedIn", "pulse": "5/8", "commit": "76b1a0358d", "status": "Pushed", "commit_url": ""},
            {"timestamp": f"{date}T19:52:43+05:00", "repo_name": "Private", "repo_url": "https://github.com/anamta-JINX/Private", "pulse": "10/20", "commit": "35b07f55ce", "status": "Pushed", "commit_url": ""},
            {"timestamp": f"{date}T19:17:11+05:00", "repo_name": "Private", "repo_url": "https://github.com/anamta-JINX/Private", "pulse": "9/20", "commit": "8f66d4f731", "status": "Pushed", "commit_url": ""},
            {"timestamp": f"{date}T18:45:30+05:00", "repo_name": "IceBreaker", "repo_url": "https://github.com/anamta-JINX/IceBreaker-YourWingmanForLinkedIn", "pulse": "4/8", "commit": "cc128642e9", "status": "Pushed", "commit_url": ""},
        ]

    def _configure_tree_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "GreenPulse.Treeview",
            background=PANEL_2,
            fieldbackground=PANEL_2,
            foreground=TEXT,
            rowheight=34,
            borderwidth=0,
            font=(FONT, 9),
        )
        style.configure(
            "GreenPulse.Treeview.Heading",
            background=PANEL_3,
            foreground=MUTED,
            relief="flat",
            borderwidth=0,
            font=(FONT, 8, "bold"),
        )
        style.map("GreenPulse.Treeview", background=[("selected", ACCENT_DARK)], foreground=[("selected", TEXT)])
        style.map("GreenPulse.Treeview.Heading", background=[("active", PANEL_3)])
        style.configure(
            "GreenPulse.Vertical.TScrollbar",
            background=PANEL_3,
            troughcolor=PANEL,
            bordercolor=PANEL,
            arrowcolor=MUTED,
            darkcolor=PANEL_3,
            lightcolor=PANEL_3,
        )
        style.map("GreenPulse.Vertical.TScrollbar", background=[("active", BORDER)])

    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_sidebar()
        self._build_main()

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=BG, height=86)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=28, pady=(20, 10))
        header.grid_columnconfigure(1, weight=1)

        try:
            self.logo_img = tk.PhotoImage(file=str(resource_path("assets", "greenpulse-logo.png"))).subsample(8, 8)
            tk.Label(header, image=self.logo_img, bg=BG).grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 14))
        except Exception:
            pass

        tk.Label(header, text=APP_NAME, bg=BG, fg=TEXT, font=(FONT, 23, "bold")).grid(row=0, column=1, sticky="sw")
        tk.Label(header, text=TAGLINE, bg=BG, fg=MUTED, font=(FONT, 10)).grid(row=1, column=1, sticky="nw", pady=(1, 0))
        self.worker_badge = tk.Label(
            header,
            textvariable=self.status_var,
            bg=ACCENT_DARK,
            fg=ACCENT,
            font=(FONT, 9, "bold"),
            padx=14,
            pady=7,
        )
        self.worker_badge.grid(row=0, column=2, rowspan=2, sticky="e")

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, width=330)
        sidebar.grid(row=1, column=0, sticky="nsew", padx=(28, 10), pady=(0, 26))
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(2, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)

        top = tk.Frame(sidebar, bg=PANEL)
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        top.grid_columnconfigure(0, weight=1)
        tk.Label(top, text="Repositories", bg=PANEL, fg=TEXT, font=(FONT, 13, "bold")).grid(row=0, column=0, sticky="w")
        GPButton(top, "+ Add", self._add_repo, kind="primary").grid(row=0, column=1, sticky="e")

        tk.Label(sidebar, textvariable=self.summary_var, bg=PANEL, fg=MUTED, font=(FONT, 8)).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 8))

        list_shell = tk.Frame(sidebar, bg=PANEL)
        list_shell.grid(row=2, column=0, sticky="nsew", padx=9, pady=(0, 10))
        list_shell.grid_rowconfigure(0, weight=1)
        list_shell.grid_columnconfigure(0, weight=1)
        self.repo_canvas = tk.Canvas(list_shell, bg=PANEL, highlightthickness=0, bd=0)
        self.repo_canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_shell, orient="vertical", command=self.repo_canvas.yview, style="GreenPulse.Vertical.TScrollbar")
        scroll.grid(row=0, column=1, sticky="ns")
        self.repo_canvas.configure(yscrollcommand=scroll.set)
        self.repo_list = tk.Frame(self.repo_canvas, bg=PANEL)
        self.repo_window = self.repo_canvas.create_window((0, 0), window=self.repo_list, anchor="nw")
        self.repo_list.bind("<Configure>", lambda _e: self.repo_canvas.configure(scrollregion=self.repo_canvas.bbox("all")))
        self.repo_canvas.bind("<Configure>", lambda e: self.repo_canvas.itemconfigure(self.repo_window, width=e.width))

        controls = tk.Frame(sidebar, bg=PANEL_2, highlightthickness=1, highlightbackground=BORDER)
        controls.grid(row=3, column=0, sticky="ew", padx=13, pady=(0, 13))

        tk.Checkbutton(
            controls,
            text="Start with Windows",
            variable=self.auto_var,
            command=self._toggle_autostart,
            bg=PANEL_2,
            fg=TEXT,
            activebackground=PANEL_2,
            activeforeground=TEXT,
            selectcolor=PANEL_3,
            font=(FONT, 9),
        ).pack(anchor="w", padx=13, pady=(12, 8))

        row = tk.Frame(controls, bg=PANEL_2)
        row.pack(fill="x", padx=13, pady=(0, 8))
        GPButton(row, "Start", self._start_worker, kind="primary").pack(side="left", fill="x", expand=True, padx=(0, 4))
        GPButton(row, "Stop", self._stop_worker, kind="danger").pack(side="left", fill="x", expand=True, padx=(4, 0))
        GPButton(controls, "Complete all repositories now", self._complete_all, kind="secondary").pack(fill="x", padx=13, pady=(0, 13))

    def _build_main(self) -> None:
        main = tk.Frame(self, bg=BG)
        main.grid(row=1, column=1, sticky="nsew", padx=(10, 28), pady=(0, 26))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        self.detail = tk.Frame(main, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        self.detail.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.detail.grid_columnconfigure(0, weight=1)

        history_panel = tk.Frame(main, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        history_panel.grid(row=1, column=0, sticky="nsew")
        history_panel.grid_rowconfigure(1, weight=1)
        history_panel.grid_columnconfigure(0, weight=1)

        history_header = tk.Frame(history_panel, bg=PANEL)
        history_header.grid(row=0, column=0, sticky="ew", padx=18, pady=(15, 9))
        history_header.grid_columnconfigure(0, weight=1)
        tk.Label(history_header, text="Commit history", bg=PANEL, fg=TEXT, font=(FONT, 13, "bold")).grid(row=0, column=0, sticky="w")
        GPButton(history_header, "Refresh", self._refresh_history, kind="ghost").grid(row=0, column=1, sticky="e")

        columns = ("time", "repo", "pulse", "commit", "status")
        self.history_tree = ttk.Treeview(history_panel, columns=columns, show="headings", style="GreenPulse.Treeview")
        for key, title in zip(columns, ("TIME", "REPOSITORY", "PULSE", "COMMIT", "STATUS")):
            self.history_tree.heading(key, text=title)
        self.history_tree.column("time", width=150, minwidth=125, anchor="w")
        self.history_tree.column("repo", width=260, minwidth=170, anchor="w")
        self.history_tree.column("pulse", width=90, minwidth=70, anchor="center")
        self.history_tree.column("commit", width=120, minwidth=90, anchor="center")
        self.history_tree.column("status", width=100, minwidth=80, anchor="center")
        self.history_tree.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 10))
        self.history_tree.bind("<Double-1>", self._open_history_commit)

        tk.Label(
            history_panel,
            text="Double-click a pushed commit to open it on GitHub.",
            bg=PANEL,
            fg=MUTED,
            font=(FONT, 8),
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(0, 13))

    def _demo_state(self) -> dict:
        return {
            "date": datetime.now().astimezone().strftime("%Y-%m-%d"),
            "repos": {
                "demo-private": {"times": ["10:14", "10:56", "11:29", "12:18", "13:04", "13:51", "14:38", "15:09", "16:02", "16:47", "17:21", "17:59", "18:41", "19:22", "20:08", "20:44", "21:31", "22:10", "22:52", "23:37"], "done": [str(i) for i in range(1, 12)], "last_error": ""},
                "demo-icebreaker": {"times": ["12:25", "13:47", "15:02", "16:28", "18:05", "19:39", "21:00", "22:14"], "done": [str(i) for i in range(1, 6)], "last_error": ""},
            },
        }

    def _selected_repo(self) -> RepoConfig | None:
        for repo in self.config_data.repositories:
            if repo.id == self.selected_repo_id:
                return repo
        return None

    def _state(self) -> dict:
        return self._demo_state() if self.demo else ensure_today_state(self.config_data)

    def _refresh_repo_list(self) -> None:
        for widget in self.repo_list.winfo_children():
            widget.destroy()

        state = self._state()
        enabled = [repo for repo in self.config_data.repositories if repo.enabled]
        total_target = sum(int(repo.commits_per_day) for repo in enabled)
        total_done = sum(len(state.get("repos", {}).get(repo.id, {}).get("done", [])) for repo in enabled)
        self.summary_var.set(f"{len(enabled)} active  ·  {total_done}/{total_target} commits today" if self.config_data.repositories else "No repositories configured")

        for repo in self.config_data.repositories:
            selected = repo.id == self.selected_repo_id
            card_bg = ACCENT_DARK if selected else PANEL_2
            card = tk.Frame(self.repo_list, bg=card_bg, highlightthickness=1, highlightbackground=ACCENT if selected else BORDER, cursor="hand2")
            card.pack(fill="x", padx=2, pady=5)
            card.grid_columnconfigure(0, weight=1)

            item = state.get("repos", {}).get(repo.id, {})
            done = len(item.get("done", []))
            target = int(repo.commits_per_day)
            title = repo.name or parse_repo_url(repo.repo_url)[1]
            title_label = tk.Label(card, text=title, bg=card_bg, fg=TEXT, font=(FONT, 10, "bold"), cursor="hand2")
            title_label.grid(row=0, column=0, sticky="w", padx=11, pady=(9, 1))
            sub = tk.Label(card, text=f"{done}/{target} today  ·  {next_scheduled_time(repo, state)} next", bg=card_bg, fg=MUTED, font=(FONT, 8), cursor="hand2")
            sub.grid(row=1, column=0, sticky="w", padx=11, pady=(0, 9))
            dot = tk.Label(card, text="●", bg=card_bg, fg=ACCENT if repo.enabled else MUTED, font=(FONT, 8))
            dot.grid(row=0, column=1, rowspan=2, sticky="e", padx=10)
            for widget in (card, title_label, sub):
                widget.bind("<Button-1>", lambda _e, repo_id=repo.id: self._select_repo(repo_id))

    def _build_detail_content(self) -> None:
        for widget in self.detail.winfo_children():
            widget.destroy()

        repo = self._selected_repo()
        if not repo:
            box = tk.Frame(self.detail, bg=PANEL)
            box.pack(fill="both", expand=True, padx=28, pady=34)
            tk.Label(box, text="Add your first repository", bg=PANEL, fg=TEXT, font=(FONT, 17, "bold")).pack(anchor="w")
            tk.Label(
                box,
                text="GreenPulse can manage multiple repositories independently, each with its own daily target and time window.",
                bg=PANEL,
                fg=MUTED,
                font=(FONT, 9),
                wraplength=700,
                justify="left",
            ).pack(anchor="w", pady=(6, 16))
            GPButton(box, "Add repository", self._add_repo, kind="primary").pack(anchor="w")
            return

        state = self._state()
        item = state.get("repos", {}).get(repo.id, {})
        done = len(item.get("done", []))
        target = int(repo.commits_per_day)
        next_time = next_scheduled_time(repo, state)

        top = tk.Frame(self.detail, bg=PANEL)
        top.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 4))
        top.grid_columnconfigure(0, weight=1)
        tk.Label(top, text=repo.name or parse_repo_url(repo.repo_url)[1], bg=PANEL, fg=TEXT, font=(FONT, 17, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(
            top,
            text="Active" if repo.enabled else "Paused",
            bg=ACCENT_DARK if repo.enabled else PANEL_3,
            fg=ACCENT if repo.enabled else MUTED,
            font=(FONT, 8, "bold"),
            padx=10,
            pady=4,
        ).grid(row=0, column=1, sticky="e")

        tk.Label(self.detail, text=repo.repo_url, bg=PANEL, fg=MUTED, font=(FONT, 8)).grid(row=1, column=0, sticky="w", padx=20)

        metrics = tk.Frame(self.detail, bg=PANEL)
        metrics.grid(row=2, column=0, sticky="ew", padx=20, pady=(15, 10))
        for i in range(3):
            metrics.grid_columnconfigure(i, weight=1)
        self._metric(metrics, 0, "TODAY", f"{done}/{target}")
        self._metric(metrics, 1, "NEXT PULSE", next_time)
        self._metric(metrics, 2, "WINDOW", f"{repo.start_time} — {repo.end_time}")

        progress_shell = tk.Frame(self.detail, bg=PANEL_3, height=7)
        progress_shell.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 16))
        progress_shell.grid_propagate(False)
        if target:
            ratio = min(1.0, done / target)
            self.detail.update_idletasks()
            width = max(1, int((self.detail.winfo_width() - 40) * ratio))
            tk.Frame(progress_shell, bg=ACCENT, width=width).pack(side="left", fill="y")

        buttons = tk.Frame(self.detail, bg=PANEL)
        buttons.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 18))
        GPButton(buttons, "Commit now", self._pulse_selected, kind="primary").pack(side="left", padx=(0, 7))
        GPButton(buttons, "Complete today", self._complete_selected, kind="secondary").pack(side="left", padx=(0, 7))
        GPButton(buttons, "Open GitHub", lambda: webbrowser.open(repo.repo_url), kind="secondary").pack(side="left", padx=(0, 7))
        GPButton(buttons, "Edit", self._edit_selected, kind="ghost").pack(side="left", padx=(0, 7))
        GPButton(buttons, "Remove", self._remove_selected, kind="danger").pack(side="left")

    def _metric(self, parent, column: int, label: str, value: str) -> None:
        box = tk.Frame(parent, bg=PANEL_2, highlightthickness=1, highlightbackground=BORDER)
        box.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0 if column == 2 else 6))
        tk.Label(box, text=label, bg=PANEL_2, fg=MUTED, font=(FONT, 7, "bold")).pack(anchor="w", padx=12, pady=(9, 1))
        tk.Label(box, text=value, bg=PANEL_2, fg=TEXT, font=(FONT, 11, "bold")).pack(anchor="w", padx=12, pady=(0, 10))

    def _refresh_history(self) -> None:
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        history = self._history_demo if self._history_demo is not None else load_history(300)
        self._history_current = history or []
        for entry in self._history_current:
            raw_time = str(entry.get("timestamp", ""))
            display_time = raw_time
            try:
                display_time = datetime.fromisoformat(raw_time).astimezone().strftime("%b %d  %H:%M")
            except Exception:
                pass
            self.history_tree.insert(
                "",
                "end",
                values=(
                    display_time,
                    entry.get("repo_name", ""),
                    entry.get("pulse", ""),
                    entry.get("commit", "") or "—",
                    entry.get("status", ""),
                ),
            )

    def _open_history_commit(self, _event=None) -> None:
        selected = self.history_tree.selection()
        if not selected:
            return
        index = self.history_tree.index(selected[0])
        if 0 <= index < len(self._history_current):
            url = self._history_current[index].get("commit_url")
            if url:
                webbrowser.open(url)

    def _refresh_worker_status(self) -> None:
        running = True if self.demo else bool(worker_pid())
        self.status_var.set("Running" if running else "Stopped")
        if self.worker_badge:
            self.worker_badge.configure(bg=ACCENT_DARK if running else PANEL_3, fg=ACCENT if running else MUTED)

    def _refresh_all(self) -> None:
        self._refresh_worker_status()
        self._refresh_repo_list()
        self._build_detail_content()
        self._refresh_history()

    def _poll(self) -> None:
        self.config_data = load_config()
        if self.selected_repo_id and not any(repo.id == self.selected_repo_id for repo in self.config_data.repositories):
            self.selected_repo_id = self.config_data.repositories[0].id if self.config_data.repositories else None
        self.auto_var.set(self.config_data.start_with_windows)
        self._refresh_all()
        self.after(3500, self._poll)

    def _select_repo(self, repo_id: str) -> None:
        self.selected_repo_id = repo_id
        self._refresh_repo_list()
        self._build_detail_content()

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
        if not messagebox.askyesno("GreenPulse", f"Remove {label} from GreenPulse?\n\nThis does not delete the GitHub repository.", parent=self):
            return
        self.config_data.repositories = [item for item in self.config_data.repositories if item.id != repo.id]
        save_config(self.config_data)
        self.selected_repo_id = self.config_data.repositories[0].id if self.config_data.repositories else None
        threading.Thread(target=lambda: GitService().remove_cache(repo), daemon=True).start()
        self._refresh_all()

    def _run_async(self, status: str, work, success_message=None) -> None:
        self.status_var.set(status)
        if self.worker_badge:
            self.worker_badge.configure(bg="#312913", fg=WARNING)

        def runner() -> None:
            try:
                result = work()

                def ok() -> None:
                    self._refresh_all()
                    if success_message:
                        messagebox.showinfo("GreenPulse", success_message(result), parent=self)

                self.after(0, ok)
            except Exception as exc:
                def fail() -> None:
                    self._refresh_all()
                    messagebox.showerror("GreenPulse", str(exc), parent=self)
                self.after(0, fail)

        threading.Thread(target=runner, daemon=True).start()

    def _pulse_selected(self) -> None:
        if self.demo:
            return
        repo = self._selected_repo()
        if not repo:
            return
        self._run_async(
            "Committing...",
            lambda: run_one_pulse(repo, mode="Commit now"),
            lambda result: result.message if not result.created else f"Pushed {result.repo_name} pulse {result.count}/{result.target} as {result.commit_hash}.",
        )

    def _complete_selected(self) -> None:
        if self.demo:
            return
        repo = self._selected_repo()
        if not repo:
            return
        if not messagebox.askyesno("GreenPulse", f"Create every remaining GreenPulse commit for {repo.name or repo.repo_url} right now?", parent=self):
            return
        self._run_async(
            "Completing today...",
            lambda: run_complete_today(repo),
            lambda results: "Today's target was already complete." if not results else f"Created and pushed {len(results)} remaining commit(s).",
        )

    def _complete_all(self) -> None:
        if self.demo:
            return
        if not self.config_data.repositories:
            messagebox.showinfo("GreenPulse", "Add at least one repository first.", parent=self)
            return
        if not messagebox.askyesno("GreenPulse", "Create every remaining GreenPulse commit for all enabled repositories right now?", parent=self):
            return
        self._run_async(
            "Completing all...",
            lambda: complete_all_enabled(self.config_data),
            lambda results: "Finished all enabled repositories.\n\n" + "\n".join(
                f"{next((r.name for r in self.config_data.repositories if r.id == repo_id), repo_id)}: {message}"
                for repo_id, message in results.items()
            ),
        )

    def _toggle_autostart(self) -> None:
        if self.demo:
            return
        self.config_data.start_with_windows = bool(self.auto_var.get())
        save_config(self.config_data)
        set_start_with_windows(self.config_data.start_with_windows)

    def _start_worker(self) -> None:
        if self.demo:
            return
        if not self.config_data.repositories:
            messagebox.showinfo("GreenPulse", "Add at least one repository first.", parent=self)
            return
        for repo in self.config_data.repositories:
            if repo.enabled:
                errors = validate_repo(repo)
                if errors:
                    messagebox.showerror("GreenPulse", f"{repo.name or repo.repo_url}:\n\n" + "\n".join(errors), parent=self)
                    return
        save_config(self.config_data)
        set_start_with_windows(self.config_data.start_with_windows)
        launch_worker()
        self._refresh_all()

    def _stop_worker(self) -> None:
        if self.demo:
            return
        stop_worker()
        self._refresh_all()
