"""Safety checks for unattended issue-runner execution."""

from __future__ import annotations

from pathlib import Path

from tools.agentic_issue_runner.git_planner import command_is_forbidden

_ALLOWED_EXECUTABLES = frozenset(
    {
        "codex",
        "git",
        "glab",
        "python",
        "python3",
        "pytest",
        "rg",
        "sed",
        "ls",
        "pwd",
    }
)


def is_inside_path(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def assert_repo_path(repo_root: Path, path: Path) -> None:
    if not is_inside_path(path, repo_root):
        raise ValueError(f"path is outside repo: {path}")


def assert_runtime_path(repo_root: Path, path: Path, *, run_id: str) -> None:
    allowed_root = repo_root / "artifacts" / "agentic_issue_runner" / run_id
    if not is_inside_path(path, allowed_root):
        raise ValueError(f"runtime path must stay under {allowed_root}: {path}")


def assert_unattended_command(args: tuple[str, ...] | list[str]) -> None:
    command = tuple(args)
    if not command:
        raise ValueError("empty command is not allowed")
    if command_is_forbidden(command):
        raise ValueError(f"hard-blocked unattended command: {' '.join(command)}")
    if command[0] not in _ALLOWED_EXECUTABLES:
        raise ValueError(f"unapproved unattended executable: {command[0]}")
