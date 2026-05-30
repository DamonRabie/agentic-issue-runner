"""Load project-specific runtime facts for guarded remote execution.

The issue runner is intentionally generic about project operations. Stable
project facts such as approved remote runtimes and checkout paths live in
repo-local runtime specs or agent memory, then this loader extracts the small
structured subset needed by worker prompts and guarded remote execution.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RemoteRuntimeSpec:
    name: str
    ssh_target: str
    repo_path: str
    jump_host: str | None = None
    python_path: str | None = None
    description: str = ""

    def prompt_lines(self) -> list[str]:
        jump = f", jump host `{self.jump_host}`" if self.jump_host else ""
        python = f", python `{self.python_path}`" if self.python_path else ""
        description = f" ({self.description})" if self.description else ""
        return [
            f"- `{self.name}`: SSH target `{self.ssh_target}`{jump}, repo path `{self.repo_path}`{python}{description}",
        ]


@dataclass(frozen=True)
class ProjectRuntimeSpec:
    remotes: tuple[RemoteRuntimeSpec, ...] = ()

    def remote_by_name(self, name: str) -> RemoteRuntimeSpec:
        for remote in self.remotes:
            if remote.name == name:
                return remote
        known = ", ".join(remote.name for remote in self.remotes) or "none"
        raise ValueError(f"unknown remote runtime {name!r}; known runtimes: {known}")

    def default_remote(self) -> RemoteRuntimeSpec:
        if not self.remotes:
            raise ValueError("no remote runtimes are defined in project runtime specifications")
        return self.remotes[0]

    def prompt_block(self) -> str:
        if not self.remotes:
            return "Project runtime specifications: none loaded."
        lines = ["Project runtime specifications loaded from repo-local runtime specs:"]
        for remote in self.remotes:
            lines.extend(remote.prompt_lines())
        return "\n".join(lines)


SPEC_HEADING = "Project Runtime Specifications"
_BULLET_RE = re.compile(r"^-\s+`(?P<name>[^`]+)`:\s*(?P<body>.+)$")
_FIELD_RE = re.compile(r"(?P<key>ssh_target|jump_host|repo_path|python_path|description)=`(?P<value>[^`]+)`")


def agent_memory_path(repo_root: Path) -> Path:
    return repo_root / ".agent-library" / "AGENT_MEMORY.md"


def runtime_spec_dir(repo_root: Path) -> Path:
    return repo_root / ".agent-library" / "runtime_specs"


def runtime_spec_paths(repo_root: Path) -> tuple[Path, ...]:
    directory = runtime_spec_dir(repo_root)
    if not directory.exists():
        return ()
    return tuple(sorted(directory.glob("*.local.toml")))


def _section_lines(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            start = index + 1
            break
    if start is None:
        return []

    section: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        section.append(line)
    return section


def parse_project_runtime_spec(text: str) -> ProjectRuntimeSpec:
    remotes: list[RemoteRuntimeSpec] = []
    for line in _section_lines(text, SPEC_HEADING):
        match = _BULLET_RE.match(line.strip())
        if not match:
            continue
        fields = {field.group("key"): field.group("value") for field in _FIELD_RE.finditer(match.group("body"))}
        if "ssh_target" not in fields or "repo_path" not in fields:
            raise ValueError(
                f"runtime spec entry {match.group('name')!r} must include ssh_target and repo_path fields"
            )
        remotes.append(
            _validated_remote(
                name=match.group("name"),
                ssh_target=fields["ssh_target"],
                jump_host=fields.get("jump_host"),
                repo_path=fields["repo_path"],
                python_path=fields.get("python_path"),
                description=fields.get("description", ""),
            )
        )
    return ProjectRuntimeSpec(tuple(remotes))


def parse_toml_runtime_spec(text: str) -> ProjectRuntimeSpec:
    payload = tomllib.loads(text)
    remote_payloads = payload.get("remotes", [])
    if not isinstance(remote_payloads, list):
        raise ValueError("runtime spec field 'remotes' must be an array of tables")
    remotes: list[RemoteRuntimeSpec] = []
    for raw_remote in remote_payloads:
        if not isinstance(raw_remote, dict):
            raise ValueError("each runtime spec remote must be a table")
        name = _required_string(raw_remote, "name")
        ssh_target = _required_string(raw_remote, "ssh_target")
        repo_path = _required_string(raw_remote, "repo_path")
        jump_host = _optional_string(raw_remote, "jump_host")
        python_path = _optional_string(raw_remote, "python_path")
        description = _optional_string(raw_remote, "description") or ""
        remotes.append(
            _validated_remote(
                name=name,
                ssh_target=ssh_target,
                repo_path=repo_path,
                jump_host=jump_host,
                python_path=python_path,
                description=description,
            )
        )
    return ProjectRuntimeSpec(tuple(remotes))


def _required_string(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"runtime spec remote must include non-empty string field {key!r}")
    return value.strip()


def _optional_string(payload: dict, key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"runtime spec field {key!r} must be a non-empty string when provided")
    return value.strip()


def _validated_remote(
    *,
    name: str,
    ssh_target: str,
    repo_path: str,
    jump_host: str | None = None,
    python_path: str | None = None,
    description: str = "",
) -> RemoteRuntimeSpec:
    if not repo_path.startswith("/"):
        raise ValueError(f"runtime spec entry {name!r} repo_path must be absolute: {repo_path}")
    if python_path is not None and not python_path.startswith(f"{repo_path.rstrip('/')}/"):
        raise ValueError(f"runtime spec entry {name!r} python_path must stay under repo_path: {python_path}")
    return RemoteRuntimeSpec(
        name=name,
        ssh_target=ssh_target,
        jump_host=jump_host,
        repo_path=repo_path,
        python_path=python_path,
        description=description,
    )


def merge_project_runtime_specs(specs: list[ProjectRuntimeSpec]) -> ProjectRuntimeSpec:
    remotes: dict[str, RemoteRuntimeSpec] = {}
    for spec in specs:
        for remote in spec.remotes:
            remotes[remote.name] = remote
    return ProjectRuntimeSpec(tuple(remotes.values()))


def load_project_runtime_spec(repo_root: Path) -> ProjectRuntimeSpec:
    specs: list[ProjectRuntimeSpec] = []
    path = agent_memory_path(repo_root)
    if path.exists():
        specs.append(parse_project_runtime_spec(path.read_text(encoding="utf-8")))
    for runtime_path in runtime_spec_paths(repo_root):
        specs.append(parse_toml_runtime_spec(runtime_path.read_text(encoding="utf-8")))
    return merge_project_runtime_specs(specs)
