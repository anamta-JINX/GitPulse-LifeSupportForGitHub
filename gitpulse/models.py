from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class RepoConfig:
    id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    repo_url: str = ""
    commit_email: str = ""
    commits_per_day: int = 20
    start_time: str = "10:00"
    end_time: str = "23:59"
    branch: str = ""
    enabled: bool = True
    local_path: str = ""
    local_sync_enabled: bool = False
    calendar_plan: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RepoConfig":
        allowed = {key: data[key] for key in cls.__dataclass_fields__ if key in data}
        item = cls(**allowed)
        item.commits_per_day = int(item.commits_per_day)
        item.enabled = bool(item.enabled)
        item.local_sync_enabled = bool(item.local_sync_enabled)
        if not isinstance(item.calendar_plan, dict):
            item.calendar_plan = {}
        else:
            item.calendar_plan = {
                str(date_key): [str(value) for value in values]
                for date_key, values in item.calendar_plan.items()
                if isinstance(values, list)
            }
        return item

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AppConfig:
    repositories: list[RepoConfig] = field(default_factory=list)
    start_with_windows: bool = True
    launch_minimized: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        repos = [RepoConfig.from_dict(item) for item in data.get("repositories", [])]

        # Migrate the single-repository configuration used by GitPulse 3.x.
        # This lets an existing installation open v4 without losing its saved repo.
        if not repos and data.get("repo_url"):
            repo_url = str(data.get("repo_url", ""))
            name = repo_url.rstrip("/").removesuffix(".git").split("/")[-1] or "Repository"
            repos = [
                RepoConfig(
                    name=name,
                    repo_url=repo_url,
                    commit_email=str(data.get("commit_email", "")),
                    commits_per_day=int(data.get("commits_per_day", 20)),
                    start_time=str(data.get("start_time", "10:00")),
                    end_time=str(data.get("end_time", "23:59")),
                    branch=str(data.get("branch", "")),
                    enabled=True,
                )
            ]

        return cls(
            repositories=repos,
            start_with_windows=bool(data.get("start_with_windows", True)),
            launch_minimized=bool(data.get("launch_minimized", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repositories": [repo.to_dict() for repo in self.repositories],
            "start_with_windows": self.start_with_windows,
            "launch_minimized": self.launch_minimized,
        }
