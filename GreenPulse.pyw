from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Keep the project root importable in source runs and frozen builds.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from greenpulse import __version__
from greenpulse.scheduler import worker_main


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
    # the complete local greenpulse package before the build is considered good.
    if args.self_test:
        from greenpulse.git_service import GitService  # noqa: F401
        from greenpulse.models import AppConfig, RepoConfig  # noqa: F401
        from greenpulse.storage import ensure_dirs  # noqa: F401
        from greenpulse.ui import GreenPulseApp  # noqa: F401
        return

    if args.worker:
        worker_main()
        return

    from greenpulse.ui import GreenPulseApp

    app = GreenPulseApp(demo=args.demo)
    app.mainloop()


if __name__ == "__main__":
    main()
