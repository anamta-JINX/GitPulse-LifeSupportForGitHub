from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse


def parse_hhmm(value: str) -> int:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        raise ValueError("Use HH:MM time, for example 10:00 or 23:59.")
    hour, minute = map(int, match.groups())
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Time must be between 00:00 and 23:59.")
    return hour * 60 + minute


def format_hhmm(minutes: int) -> str:
    hour, minute = divmod(minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    raw = repo_url.strip().rstrip("/")
    if not raw:
        raise ValueError("Enter a GitHub repository URL.")

    if raw.startswith("git@github.com:"):
        path = raw.split(":", 1)[1]
        parts = path.removesuffix(".git").split("/")
    else:
        if raw.startswith("github.com/"):
            raw = "https://" + raw
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"github.com", "www.github.com"}:
            raise ValueError("Repository must be a GitHub repository URL.")
        parts = [piece for piece in parsed.path.split("/") if piece]
        if parts:
            parts[-1] = parts[-1].removesuffix(".git")

    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", piece) for piece in parts):
        raise ValueError("Repository URL must point directly to a GitHub repository.")
    return parts[0], parts[1]


def normalize_remote(url: str) -> str:
    raw = url.strip().rstrip("/").removesuffix(".git")
    if raw.startswith("git@github.com:"):
        raw = "https://github.com/" + raw.split(":", 1)[1]
    return raw.lower()


def safe_repo_name(repo_url: str, repo_id: str) -> str:
    try:
        owner, repo = parse_repo_url(repo_url)
        base = f"{owner}-{repo}"
    except Exception:
        base = "repository"
    digest = hashlib.sha1(f"{repo_id}|{repo_url}".encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-") or "repository"
    return f"{safe}-{digest}"


def repo_commit_url(repo_url: str, commit_hash: str) -> str:
    owner, repo = parse_repo_url(repo_url)
    return f"https://github.com/{owner}/{repo}/commit/{commit_hash}"


def today_key() -> str:
    from datetime import datetime

    return datetime.now().astimezone().strftime("%Y-%m-%d")


def path_for_display(path: Path) -> str:
    try:
        return str(path.resolve())
    except Exception:
        return str(path)
