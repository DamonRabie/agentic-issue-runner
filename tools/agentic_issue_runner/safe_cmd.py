"""Permission-gated git and GitLab mutations for issue workers."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

from tools.agentic_issue_runner.constants import (
    AGENT_BRANCH_PREFIX,
    MAIN_BRANCH,
    PROJECT_REMOTE,
    REMOTE_PYTHON_MODULE_PREFIXES,
)
from tools.agentic_issue_runner.git_planner import command_is_forbidden
from tools.agentic_issue_runner.glab_adapter import GlabAdapter, without_proxy_env
from tools.agentic_issue_runner.project_spec import RemoteRuntimeSpec, load_project_runtime_spec
from tools.agentic_issue_runner.safety import assert_repo_path, is_inside_path
from tools.agentic_issue_runner.state_machine import blocked_transition, claim_transition, success_transition

_ROOT = Path(__file__).resolve().parents[2]
_AGENT_BRANCH_RE = re.compile(rf"^{re.escape(AGENT_BRANCH_PREFIX)}/[A-Za-z0-9][A-Za-z0-9._/-]*$")
_VALIDATION_EXECUTABLES = frozenset({"python", "python3", "pytest"})
_VALIDATION_MODULES = frozenset({"pytest", "unittest", "compileall"})
_REMOTE_PYTHON_MODULES = frozenset({"pytest", "unittest", "compileall"})
_REMOTE_EXECUTABLES = frozenset({"python", "python3", "pytest", "git", "rg", "sed", "ls", "pwd", "tmux"})
_REMOTE_GIT_READS = (
    ("git", "status"),
    ("git", "diff"),
    ("git", "log"),
    ("git", "show"),
    ("git", "branch", "--show-current"),
    ("git", "rev-parse"),
    ("git", "ls-files"),
)
_REMOTE_TMUX_READS = (
    ("tmux", "list-sessions"),
    ("tmux", "has-session", "-t"),
)
_REMOTE_TMUX_SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
CLAIM_FILE = "claim.json"
SUCCESS_FILE = "success.json"
BLOCK_FILE = "block.json"


def artifact_dir(repo_root: Path, run_id: str) -> Path:
    path = repo_root / "artifacts" / "agentic_issue_runner" / run_id
    assert_repo_path(repo_root, path)
    return path


def marker_path(repo_root: Path, run_id: str, filename: str) -> Path:
    path = artifact_dir(repo_root, run_id) / filename
    assert_repo_path(repo_root, path)
    return path


def load_claim(repo_root: Path, run_id: str) -> dict | None:
    path = marker_path(repo_root, run_id, CLAIM_FILE)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def has_final_marker(repo_root: Path, run_id: str) -> bool:
    return marker_path(repo_root, run_id, SUCCESS_FILE).exists() or marker_path(repo_root, run_id, BLOCK_FILE).exists()


def write_marker(repo_root: Path, run_id: str, filename: str, payload: dict) -> Path:
    path = marker_path(repo_root, run_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def is_agent_branch(value: str) -> bool:
    return bool(_AGENT_BRANCH_RE.match(value)) and ".." not in value and not value.endswith("/")


def is_allowed_base_branch(value: str) -> bool:
    return value == MAIN_BRANCH or is_agent_branch(value)


def require_agent_branch(value: str) -> None:
    if not is_agent_branch(value):
        raise ValueError(f"branch must be {AGENT_BRANCH_PREFIX}/*: {value}")


def require_base_branch(value: str) -> None:
    if not is_allowed_base_branch(value):
        raise ValueError(f"base/target branch must be {MAIN_BRANCH} or {AGENT_BRANCH_PREFIX}/*: {value}")


def run_command(args: Iterable[str], repo_root: Path = _ROOT) -> subprocess.CompletedProcess[str]:
    command = tuple(args)
    if command_is_forbidden(command):
        raise ValueError(f"forbidden command: {' '.join(command)}")
    return subprocess.run(
        list(command),
        cwd=repo_root,
        env=without_proxy_env(),
        check=False,
        capture_output=True,
        text=True,
    )


def repo_relative_path(repo_root: Path, raw: str) -> Path:
    path = Path(raw)
    resolved = path if path.is_absolute() else repo_root / path
    resolved = resolved.resolve()
    assert_repo_path(repo_root, resolved)
    if not is_inside_path(resolved, repo_root):
        raise ValueError(f"path is outside repo: {raw}")
    return resolved


def _git_ls_files(repo_root: Path, path: Path) -> subprocess.CompletedProcess[str]:
    rel = str(path.relative_to(repo_root.resolve()))
    return subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", rel],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )


def assert_add_paths(repo_root: Path, paths: list[str]) -> list[str]:
    if not paths:
        raise ValueError("git add requires at least one path")
    repo_root = repo_root.resolve()
    rels: list[str] = []
    for raw in paths:
        path = repo_relative_path(repo_root, raw)
        rel = str(path.relative_to(repo_root))
        if not path.exists() and _git_ls_files(repo_root, path).returncode == 0:
            raise ValueError(f"tracked deletion is not allowed through safe_cmd: {rel}")
        rels.append(rel)
    return rels


def assert_validation_command(command: list[str], repo_root: Path = _ROOT) -> None:
    if not command:
        raise ValueError("validation command is empty")
    if command_is_forbidden(tuple(command)):
        raise ValueError(f"forbidden validation command: {' '.join(command)}")
    if command[0] not in _VALIDATION_EXECUTABLES:
        raise ValueError(f"validation executable is not allowed: {command[0]}")
    if command[0] in {"python", "python3"}:
        if len(command) >= 3 and command[1] == "-m":
            if command[2] not in _VALIDATION_MODULES:
                raise ValueError(f"python validation module is not allowed: {command[2]}")
        elif len(command) >= 2:
            repo_relative_path(repo_root, command[1])
            if not command[1].endswith(".py"):
                raise ValueError("direct python validation must run a repo-local .py file")
    for part in command[1:]:
        if part.startswith("-"):
            continue
        if part in {"-m", *_VALIDATION_MODULES}:
            continue
        if "/" in part or part.endswith(".py"):
            repo_relative_path(repo_root, part)


def assert_remote_command(command: list[str]) -> None:
    """Validate a command that will run inside the configured remote checkout."""
    if not command:
        raise ValueError("remote command is empty")
    if command_is_forbidden(tuple(command)):
        raise ValueError(f"forbidden remote command: {' '.join(command)}")
    if command[0] not in _REMOTE_EXECUTABLES:
        raise ValueError(f"remote executable is not allowed: {command[0]}")
    if command[0] == "git" and not _remote_git_command_is_allowed(command):
        raise ValueError(f"remote git command is not allowed: {' '.join(command)}")
    if command[0] == "tmux" and not _remote_tmux_command_is_allowed(command):
        raise ValueError(f"remote tmux command is not allowed: {' '.join(command)}")
    if command[0] in {"python", "python3"} and len(command) >= 3 and command[1] == "-m":
        if command[2] not in _REMOTE_PYTHON_MODULES and not command[2].startswith(REMOTE_PYTHON_MODULE_PREFIXES):
            raise ValueError(f"python remote module is not allowed: {command[2]}")


def _remote_git_command_is_allowed(command: list[str]) -> bool:
    if any(tuple(command[: len(pattern)]) == pattern for pattern in _REMOTE_GIT_READS):
        return True
    if len(command) == 4 and command[:2] == ["git", "fetch"] and command[2] == "origin":
        return is_agent_branch(command[3])
    if len(command) == 5 and command[:3] == ["git", "switch", "-C"]:
        branch = command[3]
        remote_ref = command[4]
        return is_agent_branch(branch) and remote_ref == f"origin/{branch}"
    return False


def _remote_tmux_command_is_allowed(command: list[str]) -> bool:
    if command[:2] == list(_REMOTE_TMUX_READS[0]) and len(command) == 2:
        return True
    if command[:3] == list(_REMOTE_TMUX_READS[1]) and len(command) == 4:
        return bool(_REMOTE_TMUX_SESSION_RE.match(command[3]))
    return False


def _proxy_unset_shell_fragment() -> str:
    return " ".join(
        f"unset {key};" for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    )


def _normalized_remote_command(remote: RemoteRuntimeSpec, command: list[str]) -> list[str]:
    assert_remote_command(command)
    remote_command = list(command)
    if remote.python_path and remote_command[0] in {"python", "python3"}:
        remote_command[0] = remote.python_path
    for part in remote_command[1:]:
        if part.startswith("-") or "/" not in part:
            continue
        remote_path = PurePosixPath(part)
        if ".." in remote_path.parts:
            raise ValueError(f"remote command path may not traverse upward: {part}")
        if remote_path.is_absolute() and not str(remote_path).startswith(f"{remote.repo_path.rstrip('/')}/"):
            raise ValueError(f"remote command path must stay under {remote.repo_path}: {part}")
    return remote_command


def _remote_repo_path(remote: RemoteRuntimeSpec, raw: str, *, field_name: str) -> PurePosixPath:
    path = PurePosixPath(raw)
    if ".." in path.parts:
        raise ValueError(f"remote {field_name} may not traverse upward: {raw}")
    if path.is_absolute():
        if not str(path).startswith(f"{remote.repo_path.rstrip('/')}/"):
            raise ValueError(f"remote {field_name} must stay under {remote.repo_path}: {raw}")
        return path
    return PurePosixPath(remote.repo_path) / path


def remote_ssh_command(remote: RemoteRuntimeSpec, command: list[str]) -> tuple[str, ...]:
    remote_command = _normalized_remote_command(remote, command)
    ssh: list[str] = ["ssh"]
    if remote.jump_host:
        ssh.extend(["-J", remote.jump_host])
    unset_proxy = _proxy_unset_shell_fragment()
    remote_shell = f"cd {shlex.quote(remote.repo_path)} && {unset_proxy} {' '.join(shlex.quote(part) for part in remote_command)}"
    ssh.extend([remote.ssh_target, remote_shell])
    return tuple(ssh)


def remote_tmux_ssh_command(
    remote: RemoteRuntimeSpec,
    *,
    session_name: str,
    command: list[str],
    log_path: str | None = None,
) -> tuple[str, ...]:
    """Build an SSH command that starts an approved remote command in tmux.

    Long remote jobs should outlive the local Codex worker. This helper keeps
    the same remote command allowlist as `remote-run`, starts the job detached
    in a named tmux session, and redirects output to a repo-local log path that
    can be polled later with `remote-run`.
    """
    if not _REMOTE_TMUX_SESSION_RE.match(session_name):
        raise ValueError("tmux session must be 1-80 characters of letters, digits, '.', '_', or '-'")
    remote_command = _normalized_remote_command(remote, command)
    remote_log = _remote_repo_path(
        remote,
        log_path or f"artifacts/logs/{session_name}.log",
        field_name="tmux log path",
    )
    log_parent = str(remote_log.parent)
    unset_proxy = _proxy_unset_shell_fragment()
    command_text = " ".join(shlex.quote(part) for part in remote_command)
    inner_script = (
        f"cd {shlex.quote(remote.repo_path)} && "
        f"mkdir -p {shlex.quote(log_parent)} && "
        f"{unset_proxy} exec {command_text} >> {shlex.quote(str(remote_log))} 2>&1"
    )
    tmux_command = f"sh -lc {shlex.quote(inner_script)}"
    remote_shell = (
        f"cd {shlex.quote(remote.repo_path)} && "
        f"{unset_proxy} tmux new-session -d -s {shlex.quote(session_name)} {shlex.quote(tmux_command)} && "
        f"printf '%s\\n' {shlex.quote(f'tmux session {session_name} started; log {remote_log}')}"
    )
    ssh: list[str] = ["ssh"]
    if remote.jump_host:
        ssh.extend(["-J", remote.jump_host])
    ssh.extend([remote.ssh_target, remote_shell])
    return tuple(ssh)


def current_git_state(repo_root: Path = _ROOT) -> str:
    branch = run_command(("git", "rev-parse", "--abbrev-ref", "HEAD"), repo_root)
    status = run_command(("git", "status", "--short"), repo_root)
    branch_text = branch.stdout.strip() if branch.returncode == 0 else f"unknown ({branch.stderr.strip()})"
    status_text = status.stdout.strip() if status.returncode == 0 else f"status failed ({status.stderr.strip()})"
    return f"branch={branch_text}; status={status_text or 'clean'}"


def assert_clean_claimed_branch(repo_root: Path, claim: dict) -> None:
    branch = run_command(("git", "rev-parse", "--abbrev-ref", "HEAD"), repo_root)
    if branch.returncode != 0:
        raise ValueError(branch.stderr.strip() or "failed to read current branch")
    expected_branch = str(claim["branch"])
    if branch.stdout.strip() != expected_branch:
        raise ValueError(f"current branch {branch.stdout.strip()} does not match claimed branch {expected_branch}")
    status = run_command(("git", "status", "--short"), repo_root)
    if status.returncode != 0:
        raise ValueError(status.stderr.strip() or "git status failed")
    if status.stdout.strip():
        raise ValueError(f"worktree is dirty: {status.stdout.strip()}")


def apply_claim(args: argparse.Namespace, repo_root: Path = _ROOT) -> int:
    require_agent_branch(args.branch)
    require_base_branch(args.base_branch)
    if has_final_marker(repo_root, args.run_id):
        raise ValueError("run already has a final marker")
    adapter = GlabAdapter(repo_root)
    issue = adapter.get_issue(args.issue)
    timestamp = datetime.now(timezone.utc).isoformat()
    transition = claim_transition(issue, run_id=args.run_id, timestamp=timestamp, branch=args.branch, base_branch=args.base_branch)
    if transition is None:
        raise ValueError(f"issue #{args.issue} is not claimable")
    adapter.update_issue_labels(args.issue, add_labels=transition.add_labels, remove_labels=transition.remove_labels)
    adapter.note_issue(args.issue, transition.comment)
    write_marker(
        repo_root,
        args.run_id,
        CLAIM_FILE,
        {
            "run_id": args.run_id,
            "issue_id": args.issue,
            "branch": args.branch,
            "base_branch": args.base_branch,
            "timestamp": timestamp,
        },
    )
    return 0


def mark_blocked_from_claim(
    *,
    repo_root: Path,
    run_id: str,
    blocker: str,
    commands_run: list[str],
    key_logs: str,
    next_step: str,
    branch_state: str | None = None,
) -> bool:
    claim = load_claim(repo_root, run_id)
    if claim is None or has_final_marker(repo_root, run_id):
        return False
    adapter = GlabAdapter(repo_root)
    transition = blocked_transition(
        blocker=blocker,
        commands_run=commands_run,
        key_logs=key_logs,
        next_step=next_step,
        branch_state=branch_state or current_git_state(repo_root),
    )
    iid = int(claim["issue_id"])
    adapter.update_issue_labels(iid, add_labels=transition.add_labels, remove_labels=transition.remove_labels)
    adapter.note_issue(iid, transition.comment)
    write_marker(
        repo_root,
        run_id,
        BLOCK_FILE,
        {
            "run_id": run_id,
            "issue_id": iid,
            "blocker": blocker,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    return True


def apply_block(args: argparse.Namespace, repo_root: Path = _ROOT) -> int:
    commands = args.commands_run or []
    marked = mark_blocked_from_claim(
        repo_root=repo_root,
        run_id=args.run_id,
        blocker=args.blocker,
        commands_run=commands,
        key_logs=args.key_logs,
        next_step=args.next_step,
        branch_state=args.branch_state,
    )
    if not marked:
        raise ValueError("no unresolved claim exists for this run")
    return 0


def apply_success(args: argparse.Namespace, repo_root: Path = _ROOT) -> int:
    claim = load_claim(repo_root, args.run_id)
    if claim is None:
        raise ValueError("success requires an existing claim")
    if has_final_marker(repo_root, args.run_id):
        raise ValueError("run already has a final marker")
    assert_clean_claimed_branch(repo_root, claim)
    adapter = GlabAdapter(repo_root)
    transition = success_transition(
        mr_link=args.mr_link,
        validation=args.validation,
        self_review=args.self_review,
        changed_files=args.changed_file,
        residual_risks=args.residual_risks,
    )
    iid = int(claim["issue_id"])
    adapter.update_issue_labels(iid, add_labels=transition.add_labels, remove_labels=transition.remove_labels)
    adapter.note_issue(iid, transition.comment)
    write_marker(
        repo_root,
        args.run_id,
        SUCCESS_FILE,
        {
            "run_id": args.run_id,
            "issue_id": iid,
            "mr_link": args.mr_link,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    return 0


def apply_git(args: argparse.Namespace, repo_root: Path = _ROOT) -> int:
    if args.git_action == "branch":
        require_agent_branch(args.branch)
        require_base_branch(args.base_branch)
        result = run_command(("git", "switch", "-C", args.branch, args.base_branch), repo_root)
    elif args.git_action == "add":
        rels = assert_add_paths(repo_root, args.paths)
        result = run_command(("git", "add", "--", *rels), repo_root)
    elif args.git_action == "commit":
        if not args.message.strip():
            raise ValueError("commit message is required")
        result = run_command(("git", "commit", "-m", args.message), repo_root)
    elif args.git_action == "push":
        require_agent_branch(args.branch)
        result = run_command(("git", "push", "origin", args.branch), repo_root)
    else:
        raise ValueError(f"unknown git action: {args.git_action}")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    print(result.stdout, end="")
    return 0


def apply_mr_create(args: argparse.Namespace, repo_root: Path = _ROOT) -> int:
    require_agent_branch(args.source_branch)
    require_base_branch(args.target_branch)
    result = run_command(
        (
            "glab",
            "mr",
            "create",
            "-R",
            PROJECT_REMOTE,
            "--source-branch",
            args.source_branch,
            "--target-branch",
            args.target_branch,
            "--title",
            args.title,
            "--description",
            args.description,
        ),
        repo_root,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "MR creation failed")
    print(result.stdout, end="")
    return 0


def apply_comment(args: argparse.Namespace, repo_root: Path = _ROOT) -> int:
    adapter = GlabAdapter(repo_root)
    adapter.note_issue(args.issue, args.body)
    return 0


def apply_validate(args: argparse.Namespace, repo_root: Path = _ROOT) -> int:
    assert_validation_command(args.validation_command, repo_root=repo_root)
    result = run_command(args.validation_command, repo_root)
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    return result.returncode


def apply_remote_run(args: argparse.Namespace, repo_root: Path = _ROOT) -> int:
    command = args.remote_command
    if command and command[0] == "--":
        command = command[1:]
    spec = load_project_runtime_spec(repo_root)
    remote = spec.remote_by_name(args.remote) if args.remote else spec.default_remote()
    ssh_command = remote_ssh_command(remote, command)
    result = subprocess.run(
        list(ssh_command),
        cwd=repo_root,
        env=without_proxy_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    return result.returncode


def apply_remote_tmux(args: argparse.Namespace, repo_root: Path = _ROOT) -> int:
    command = args.remote_command
    if command and command[0] == "--":
        command = command[1:]
    spec = load_project_runtime_spec(repo_root)
    remote = spec.remote_by_name(args.remote) if args.remote else spec.default_remote()
    ssh_command = remote_tmux_ssh_command(
        remote,
        session_name=args.session,
        command=command,
        log_path=args.log_path,
    )
    result = subprocess.run(
        list(ssh_command),
        cwd=repo_root,
        env=without_proxy_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Permission-gated overnight agent command runner.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    claim = subparsers.add_parser("claim")
    claim.add_argument("--run-id", required=True)
    claim.add_argument("--issue", type=int, required=True)
    claim.add_argument("--branch", required=True)
    claim.add_argument("--base-branch", required=True)

    block = subparsers.add_parser("block")
    block.add_argument("--run-id", required=True)
    block.add_argument("--blocker", required=True)
    block.add_argument("--command", dest="commands_run", action="append", default=[])
    block.add_argument("--key-logs", required=True)
    block.add_argument("--branch-state", default=None)
    block.add_argument("--next-step", required=True)

    success = subparsers.add_parser("success")
    success.add_argument("--run-id", required=True)
    success.add_argument("--mr-link", required=True)
    success.add_argument("--validation", required=True)
    success.add_argument("--self-review", required=True)
    success.add_argument("--changed-file", action="append", default=[])
    success.add_argument("--residual-risks", required=True)

    git = subparsers.add_parser("git")
    git_sub = git.add_subparsers(dest="git_action", required=True)
    branch = git_sub.add_parser("branch")
    branch.add_argument("--branch", required=True)
    branch.add_argument("--base-branch", required=True)
    add = git_sub.add_parser("add")
    add.add_argument("paths", nargs="+")
    commit = git_sub.add_parser("commit")
    commit.add_argument("--message", required=True)
    push = git_sub.add_parser("push")
    push.add_argument("--branch", required=True)

    mr = subparsers.add_parser("mr-create")
    mr.add_argument("--source-branch", required=True)
    mr.add_argument("--target-branch", required=True)
    mr.add_argument("--title", required=True)
    mr.add_argument("--description", required=True)

    comment = subparsers.add_parser("issue-comment")
    comment.add_argument("--issue", type=int, required=True)
    comment.add_argument("--body", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("validation_command", nargs=argparse.REMAINDER)

    remote = subparsers.add_parser("remote-run")
    remote.add_argument("--remote", help="named remote runtime from project runtime specifications")
    remote.add_argument("remote_command", nargs=argparse.REMAINDER)

    remote_tmux = subparsers.add_parser("remote-tmux")
    remote_tmux.add_argument("--remote", help="named remote runtime from project runtime specifications")
    remote_tmux.add_argument("--session", required=True, help="tmux session name for the remote job")
    remote_tmux.add_argument("--log-path", help="repo-local remote log path, default artifacts/logs/<session>.log")
    remote_tmux.add_argument("remote_command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "claim":
            return apply_claim(args)
        if args.command == "block":
            return apply_block(args)
        if args.command == "success":
            return apply_success(args)
        if args.command == "git":
            return apply_git(args)
        if args.command == "mr-create":
            return apply_mr_create(args)
        if args.command == "issue-comment":
            return apply_comment(args)
        if args.command == "validate":
            command = args.validation_command
            if command and command[0] == "--":
                args.validation_command = command[1:]
            return apply_validate(args)
        if args.command == "remote-run":
            return apply_remote_run(args)
        if args.command == "remote-tmux":
            return apply_remote_tmux(args)
    except Exception as exc:
        print(str(exc))
        return 2
    raise ValueError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
