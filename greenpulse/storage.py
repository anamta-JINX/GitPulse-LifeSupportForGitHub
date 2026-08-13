from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import AppConfig

APP_DIR = Path.home() / ".greenpulse"
REPOS_DIR = APP_DIR / "repos"
CONFIG_FILE = APP_DIR / "config.json"
STATE_FILE = APP_DIR / "state.json"
HISTORY_FILE = APP_DIR / "history.jsonl"
LOG_FILE = APP_DIR / "greenpulse.log"
PID_FILE = APP_DIR / "worker.pid"


def ensure_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    REPOS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> AppConfig:
    ensure_dirs()
    if not CONFIG_FILE.exists():
        return AppConfig()
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return AppConfig.from_dict(raw)
    except Exception:
        return AppConfig()


def save_config(config: AppConfig) -> None:
    ensure_dirs()
    CONFIG_FILE.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")


def load_state() -> dict[str, Any]:
    ensure_dirs()
    if not STATE_FILE.exists():
        return {"date": "", "repos": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"date": "", "repos": {}}
        data.setdefault("date", "")
        data.setdefault("repos", {})
        return data
    except Exception:
        return {"date": "", "repos": {}}


def save_state(state: dict[str, Any]) -> None:
    ensure_dirs()
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def append_history(entry: dict[str, Any]) -> None:
    ensure_dirs()
    record = dict(entry)
    record.setdefault("timestamp", datetime.now().astimezone().isoformat(timespec="seconds"))
    with HISTORY_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_history(limit: int = 250) -> list[dict[str, Any]]:
    ensure_dirs()
    if not HISTORY_FILE.exists():
        return []
    try:
        lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    output: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                output.append(value)
        except Exception:
            continue
    return list(reversed(output))


def write_log(message: str) -> None:
    ensure_dirs()
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def is_windows() -> bool:
    return os.name == "nt"
