"""Git safety and branch planning helpers for the agentic issue runner."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from tools.agentic_issue_runner.constants import AGENT_BRANCH_PREFIX, MAIN_BRANCH, RUNNER_MODE
from tools.agentic_issue_runner.models import CommandResult
from tools.logger import get_logger

CommandRunner = Callable[[list[str], Path], CommandResult]
logger = get_logger("[agentic-issue-runner]")

_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_FORBIDDEN_COMMAND_PATTERNS = (
    ("rm",),
    ("rmdir",),
    ("unlink",),
    ("sudo",),
    ("su",),
    ("ssh",),
    ("scp",),
    ("rsync",),
    ("curl",),
    ("wget",),
    ("chmod",),
    ("chown",),
    ("pip", "install"),
    ("python", "-m", "pip", "install"),
    ("npm", "install"),
    ("uv", "add"),
    ("poetry", "add"),
    ("conda", "install"),
    ("docker", "run"),
    ("docker", "compose", "up"),
    ("docker-compose", "up"),
    ("kubectl",),
    ("terraform",),
    ("git", "reset"),
    ("git", "clean"),
    ("git", "restore"),
    ("git", "push", "origin", MAIN_BRANCH),
    ("git", "push", "origin", f"HEAD:{MAIN_BRANCH}"),
    ("git", "branch", "-D"),
    ("git", "branch", "-d"),
    ("git", "worktree", "remove"),
    ("glab", "mr", "merge"),
    ("glab", "mr", "approve"),
    ("glab", "issue", "close"),
)


@dataclass(frozen=True)
class StartupGate:
    ok: bool
    reason: str
    planned_commands: tuple[tuple[str, ...], ...]


def default_runner(args: list[str], cwd: Path) -> CommandResult:
    logger.info("Running git startup command: %s", " ".join(args))
    completed = subprocess.run(args, cwd=cwd, check=False, capture_output=True, text=True)
    logger.info("Git startup command finished rc=%s: %s", completed.returncode, " ".join(args))
    return CommandResult(tuple(args), completed.returncode, completed.stdout, completed.stderr)


def slugify(value: str, max_length: int = 48) -> str:
    slug = _SLUG_CHARS_RE.sub("-", value.strip().lower()).strip("-")
    if not slug:
        return "issue"
    return slug[:max_length].strip("-") or "issue"


def branch_name(issue_iid: int, title: str) -> str:
    return f"{AGENT_BRANCH_PREFIX}/{issue_iid}-{slugify(title)}"


def command_is_forbidden(args: tuple[str, ...] | list[str]) -> bool:
    command = tuple(args)
    for pattern in _FORBIDDEN_COMMAND_PATTERNS:
        if command[: len(pattern)] == pattern:
            return True
    if command[:2] == ("git", "checkout") and "--" in command:
        return True
    if command[:3] == ("git", "push", "origin") and any(part.endswith(f":{MAIN_BRANCH}") for part in command[3:]):
        return True
    if command[:3] == ("git", "push", "origin") and any(part in {"--force", "-f"} for part in command[3:]):
        return True
    return False


def assert_safe_command_plan(commands: list[tuple[str, ...]]) -> None:
    for command in commands:
        if command_is_forbidden(command):
            raise ValueError(f"forbidden command planned: {' '.join(command)}")


def startup_gate(repo_root: Path, runner: CommandRunner = default_runner) -> StartupGate:
    status = runner(["git", "status", "--porcelain"], repo_root)
    planned = (
        ("git", "fetch", "origin"),
        ("git", "merge", "--ff-only", f"origin/{MAIN_BRANCH}"),
    )
    assert_safe_command_plan(list(planned))
    if status.returncode != 0:
        return StartupGate(False, status.stderr.strip() or "git status failed", planned)
    if status.stdout.strip():
        return StartupGate(False, "main worktree is dirty", planned)

    ff_check = runner(["git", "merge-base", "--is-ancestor", MAIN_BRANCH, f"origin/{MAIN_BRANCH}"], repo_root)
    if ff_check.returncode != 0:
        return StartupGate(False, f"local {MAIN_BRANCH} cannot fast-forward from origin/{MAIN_BRANCH}", planned)
    return StartupGate(True, "startup gate passed", planned)


def execute_startup_gate(repo_root: Path, runner: CommandRunner = default_runner) -> StartupGate:
    if RUNNER_MODE == "local":
        planned: tuple[tuple[str, ...], ...] = ()
    else:
        planned = (
            ("git", "fetch", "origin"),
            ("git", "merge", "--ff-only", f"origin/{MAIN_BRANCH}"),
        )
    assert_safe_command_plan(list(planned))

    status = runner(["git", "status", "--porcelain"], repo_root)
    if status.returncode != 0:
        return StartupGate(False, status.stderr.strip() or "git status failed", planned)
    if status.stdout.strip():
        return StartupGate(False, "main worktree is dirty", planned)

    branch = runner(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    if branch.returncode != 0:
        return StartupGate(False, branch.stderr.strip() or "failed to read current branch", planned)
    if branch.stdout.strip() != MAIN_BRANCH:
        return StartupGate(False, f"main worktree must be on {MAIN_BRANCH} before unattended run", planned)

    for command in planned:
        result = runner(list(command), repo_root)
        if result.returncode != 0:
            return StartupGate(False, result.stderr.strip() or f"command failed: {' '.join(command)}", planned)
    return StartupGate(True, "startup gate passed", planned)
