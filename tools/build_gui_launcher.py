"""Build the lightweight GitPulse Windows GUI launcher.

The distlib Windows stub is not a complete executable by itself.  It requires
an interpreter shebang followed by an appended ZIP containing ``__main__.py``.
This script produces that complete format and validates the embedded archive.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent.parent
STUB = ROOT / "tools" / "w64-launcher.exe"
OUTPUT = ROOT / "GitPulse.exe"

MAIN = r'''from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys

launcher = Path(sys.argv[0]).resolve()
root = launcher.parent
entry = root / "GitPulse.pyw"

os.chdir(root)
if str(root) not in sys.path:
    sys.path.insert(0, str(root))
sys.argv[0] = str(entry)
runpy.run_path(str(entry), run_name="__main__")
'''


def build() -> Path:
    stub = STUB.read_bytes()
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("__main__.py", MAIN.encode("utf-8"))

    # The GUI launcher searches PATH for the standard Windows Python Launcher.
    # This mirrors the already-working run_gitpulse.bat path without a console.
    payload = stub + b"#!pyw.exe -3\n" + stream.getvalue()
    OUTPUT.write_bytes(payload)

    with ZipFile(OUTPUT) as archive:
        names = archive.namelist()
        if names != ["__main__.py"]:
            raise RuntimeError(f"Unexpected launcher archive: {names}")
        if archive.read("__main__.py").decode("utf-8") != MAIN:
            raise RuntimeError("Launcher archive verification failed.")
    return OUTPUT


if __name__ == "__main__":
    result = build()
    print(f"Built {result} ({result.stat().st_size} bytes)")
