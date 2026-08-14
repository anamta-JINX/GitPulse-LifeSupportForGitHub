# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve()

hiddenimports = collect_submodules("gitpulse")

a = Analysis(
    [str(ROOT / "GitPulse.pyw")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "assets" / "gitpulse-logo.png"), "assets"),
        (str(ROOT / "assets" / "gitpulse.ico"), "assets"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="GitPulse",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "gitpulse.ico"),
)
