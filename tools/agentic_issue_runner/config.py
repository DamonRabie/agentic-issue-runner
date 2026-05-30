"""Public-safe configuration loading for the Agentic Issue Runner.

Operator-specific GitLab hosts, project paths, labels, and remote runtime
details should live in ignored local config rather than in publishable source.
This loader falls back to harmless example values so the repository can be
shared publicly before a real project is configured.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_ENV_KEY = "AIR_CONFIG"
DEFAULT_CONFIG_PATH = Path(".agent-library") / "agentic_issue_runner.toml"


@dataclass(frozen=True)
class GitLabConfig:
    host: str = "gitlab.example.com"
    project_path: str = "group/project"

    @property
    def remote(self) -> str:
        return f"{self.host}/{self.project_path}"


@dataclass(frozen=True)
class LabelConfig:
    ready: str = "ready-for-agent"
    in_progress: str = "agent-in-progress"
    mr_opened: str = "agent-mr-opened"
    needs_review: str = "agent-needs-review"
    blocked: str = "agent-blocked"
    completed: str = "completed"
    local_only: str = "local-only"
    expensive_compute: str = "expensive-compute"
    long_running: str = "long-running"
    external_service: str = "needs-external-service"
    high_priority: tuple[str, ...] = ("priority::high", "priority-high")

    @property
    def managed(self) -> frozenset[str]:
        return frozenset({self.in_progress, self.mr_opened, self.needs_review, self.blocked})

    @property
    def resource(self) -> frozenset[str]:
        return frozenset({self.local_only, self.expensive_compute, self.long_running, self.external_service})

    @property
    def heavy_resource(self) -> frozenset[str]:
        return frozenset({self.expensive_compute, self.long_running, self.external_service})


@dataclass(frozen=True)
class RunnerConfig:
    main_branch: str = "main"
    branch_prefix: str = "agent"
    remote_python_module_prefixes: tuple[str, ...] = ("tools.", "src.", "project.")


@dataclass(frozen=True)
class AgenticIssueRunnerConfig:
    gitlab: GitLabConfig = field(default_factory=GitLabConfig)
    labels: LabelConfig = field(default_factory=LabelConfig)
    runner: RunnerConfig = field(default_factory=RunnerConfig)


def repo_root_from_module() -> Path:
    return Path(__file__).resolve().parents[2]


def _config_path(repo_root: Path) -> Path:
    raw = os.environ.get(CONFIG_ENV_KEY)
    if raw:
        return Path(raw).expanduser()
    return repo_root / DEFAULT_CONFIG_PATH


def _string(payload: dict, key: str, default: str) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"config field {key!r} must be a non-empty string")
    return value.strip()


def _string_tuple(payload: dict, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = payload.get(key, default)
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple)):
        values = tuple(value)
    else:
        raise ValueError(f"config field {key!r} must be a string or list of strings")
    if not all(isinstance(item, str) and item.strip() for item in values):
        raise ValueError(f"config field {key!r} must contain only non-empty strings")
    return tuple(item.strip() for item in values)


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"config file must contain a TOML table: {path}")
    return payload


def load_config(repo_root: Path | None = None) -> AgenticIssueRunnerConfig:
    root = repo_root or repo_root_from_module()
    payload = _load_toml(_config_path(root))
    gitlab_payload = payload.get("gitlab", {})
    labels_payload = payload.get("labels", {})
    runner_payload = payload.get("runner", {})
    if not isinstance(gitlab_payload, dict) or not isinstance(labels_payload, dict) or not isinstance(runner_payload, dict):
        raise ValueError("config sections gitlab, labels, and runner must be TOML tables")

    env_host = os.environ.get("AIR_GITLAB_HOST")
    env_project = os.environ.get("AIR_GITLAB_PROJECT")
    gitlab = GitLabConfig(
        host=env_host.strip() if env_host else _string(gitlab_payload, "host", GitLabConfig.host),
        project_path=env_project.strip() if env_project else _string(gitlab_payload, "project_path", GitLabConfig.project_path),
    )
    labels = LabelConfig(
        ready=_string(labels_payload, "ready", LabelConfig.ready),
        in_progress=_string(labels_payload, "in_progress", LabelConfig.in_progress),
        mr_opened=_string(labels_payload, "mr_opened", LabelConfig.mr_opened),
        needs_review=_string(labels_payload, "needs_review", LabelConfig.needs_review),
        blocked=_string(labels_payload, "blocked", LabelConfig.blocked),
        completed=_string(labels_payload, "completed", LabelConfig.completed),
        local_only=_string(labels_payload, "local_only", LabelConfig.local_only),
        expensive_compute=_string(labels_payload, "expensive_compute", LabelConfig.expensive_compute),
        long_running=_string(labels_payload, "long_running", LabelConfig.long_running),
        external_service=_string(labels_payload, "external_service", LabelConfig.external_service),
        high_priority=_string_tuple(labels_payload, "high_priority", LabelConfig.high_priority),
    )
    runner = RunnerConfig(
        main_branch=_string(runner_payload, "main_branch", RunnerConfig.main_branch),
        branch_prefix=_string(runner_payload, "branch_prefix", RunnerConfig.branch_prefix),
        remote_python_module_prefixes=_string_tuple(
            runner_payload,
            "remote_python_module_prefixes",
            RunnerConfig.remote_python_module_prefixes,
        ),
    )
    return AgenticIssueRunnerConfig(gitlab=gitlab, labels=labels, runner=runner)
