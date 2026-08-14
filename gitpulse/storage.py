from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import AppConfig

APP_DIR = Path(os.environ.get("GITPULSE_DATA_DIR", "")).expanduser() if os.environ.get("GITPULSE_DATA_DIR") else Path.home() / ".gitpulse"
REPOS_DIR = APP_DIR / "repos"
CONFIG_FILE = APP_DIR / "config.json"
STATE_FILE = APP_DIR / "state.json"
HISTORY_FILE = APP_DIR / "history.jsonl"
LOG_FILE = APP_DIR / "gitpulse.log"
PID_FILE = APP_DIR / "worker.pid"
WORKER_META_FILE = APP_DIR / "worker.json"
BRAND_MIGRATION_FILE = APP_DIR / ".gitpulse-brand-v1"
HOURLY_SYNC_MIGRATION_FILE = APP_DIR / ".gitpulse-hourly-v2"
SYNC_ENGINE_MIGRATION_FILE = APP_DIR / ".gitpulse-sync-engine-v3"


def _replace_legacy_brand(value: str) -> str:
    legacy_name = "Green" + "Pulse"
    return re.sub(re.escape(legacy_name), "GitPulse", value, flags=re.IGNORECASE)


def _migrate_visible_branding() -> None:
    """Upgrade old saved display names/history without changing remote URLs."""
    if BRAND_MIGRATION_FILE.exists():
        return
    if CONFIG_FILE.is_file():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            changed = False
            for repo in raw.get("repositories", []):
                if isinstance(repo, dict) and isinstance(repo.get("name"), str):
                    updated = _replace_legacy_brand(repo["name"])
                    if updated != repo["name"]:
                        repo["name"] = updated
                        changed = True
            if changed:
                CONFIG_FILE.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        except Exception:
            pass
    if HISTORY_FILE.is_file():
        try:
            source_lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
            output_lines: list[str] = []
            changed = False
            for line in source_lines:
                try:
                    record = json.loads(line)
                except Exception:
                    output_lines.append(line)
                    continue
                if isinstance(record, dict):
                    for key in ("repo_name", "message"):
                        if isinstance(record.get(key), str):
                            updated = _replace_legacy_brand(record[key])
                            if updated != record[key]:
                                record[key] = updated
                                changed = True
                output_lines.append(json.dumps(record, ensure_ascii=False))
            if changed:
                HISTORY_FILE.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")
        except Exception:
            pass
    if STATE_FILE.is_file():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                for item in state.get("repos", {}).values():
                    if isinstance(item, dict):
                        item["local_sync_last_check"] = 0.0
                        item["local_sync_status"] = "Ready"
                STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            pass
    try:
        BRAND_MIGRATION_FILE.write_text("GitPulse\n", encoding="utf-8")
    except OSError:
        pass


def _migrate_previous_brand_data() -> None:
    """Carry settings/history forward once without copying stale workers."""
    legacy_dir = Path.home() / ("." + "green" + "pulse")
    if APP_DIR.exists() or not legacy_dir.exists():
        return
    APP_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "state.json", "history.jsonl"):
        source = legacy_dir / name
        if source.is_file():
            try:
                shutil.copy2(source, APP_DIR / name)
            except OSError:
                pass


def _migrate_hourly_sync_state() -> None:
    """Make upgraded installations perform one fresh hourly check."""
    if HOURLY_SYNC_MIGRATION_FILE.exists():
        return
    if STATE_FILE.is_file():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                for item in state.get("repos", {}).values():
                    if isinstance(item, dict):
                        item["local_sync_last_check"] = 0.0
                        item["local_sync_next_check"] = 0.0
                        item["local_sync_status"] = "Ready"
                        item["local_sync_error"] = ""
                STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            pass
    try:
        HOURLY_SYNC_MIGRATION_FILE.write_text("hourly-sync-v2\n", encoding="utf-8")
    except OSError:
        pass


def _migrate_sync_engine_state() -> None:
    """Force one immediate check after installing the self-updating worker."""
    if SYNC_ENGINE_MIGRATION_FILE.exists():
        return
    if STATE_FILE.is_file():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                for item in state.get("repos", {}).values():
                    if isinstance(item, dict):
                        item["local_sync_last_check"] = 0.0
                        item["local_sync_next_check"] = 0.0
                        item["local_sync_status"] = "Ready · upgrade check pending"
                        item["local_sync_error"] = ""
                STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            pass
    try:
        SYNC_ENGINE_MIGRATION_FILE.write_text("sync-engine-v3\n", encoding="utf-8")
    except OSError:
        pass


def ensure_dirs() -> None:
    _migrate_previous_brand_data()
    APP_DIR.mkdir(parents=True, exist_ok=True)
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    _migrate_visible_branding()
    _migrate_hourly_sync_state()
    _migrate_sync_engine_state()


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
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %I:%M:%S %p")
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def is_windows() -> bool:
    return os.name == "nt"
