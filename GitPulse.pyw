from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

# Keep the project root importable in source runs and frozen builds.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gitpulse import __version__


def _report_startup_failure(exc: BaseException) -> None:
    """Never let a windowed launch fail silently.

    ``pythonw`` and a windowed PyInstaller build do not have a terminal, so an
    import or Tk error used to look exactly like "nothing happened".  Keep a
    diagnostic log and show a native Windows dialog when possible.
    """
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log_dir = Path.home() / ".gitpulse"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "startup-error.log").open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}]\n{details}\n")
    except Exception:
        pass

    message = (
        "GitPulse could not start.\n\n"
        f"{type(exc).__name__}: {exc}\n\n"
        "A diagnostic was saved to:\n"
        f"{log_dir / 'startup-error.log'}"
    )
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("GitPulse — Startup problem", message, parent=root)
        root.destroy()
    except Exception:
        if os.name == "nt":
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(None, message, "GitPulse — Startup problem", 0x10)
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--version", action="store_true")
    args, _unknown = parser.parse_known_args()

    if args.version:
        print(__version__)
        return

    # Used by build_exe.bat to verify that the frozen executable can import
    # the complete local gitpulse package before the build is considered good.
    if args.self_test:
        from gitpulse.git_service import GitService  # noqa: F401
        from gitpulse.models import AppConfig, RepoConfig  # noqa: F401
        from gitpulse.storage import ensure_dirs  # noqa: F401
        from gitpulse.ui import GitPulseApp  # noqa: F401
        return

    if args.worker:
        from gitpulse.scheduler import worker_main

        worker_main()
        return

    from gitpulse.ui import GitPulseApp

    app = GitPulseApp(demo=args.demo)
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as error:
        _report_startup_failure(error)
        raise SystemExit(1)
