"""
runner.py
Unattended loop orchestration for the agentic issue runner.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tools.agentic_issue_runner.constants import PROJECT_HOST
from tools.agentic_issue_runner.dry_run import build_dry_run_plan
from tools.agentic_issue_runner.glab_adapter import GlabAdapter, without_proxy_env
from tools.agentic_issue_runner.models import IssueRecord, SchedulePlan, ScheduledIssue, StackDependency
from tools.agentic_issue_runner.prompt_builder import build_worker_prompt
from tools.agentic_issue_runner.project_spec import load_project_runtime_spec
from tools.agentic_issue_runner.safe_cmd import SUCCESS_FILE, has_final_marker, load_claim, mark_blocked_from_claim, marker_path
from tools.agentic_issue_runner.safety import assert_runtime_path, assert_unattended_command
from tools.agentic_issue_runner.scheduler import is_eligible
from tools.agentic_issue_runner.stack_evidence import (
    completed_dependency_iids,
    master_ready_dependency_iids,
    stack_ready_dependencies,
)
from tools.logger import get_logger


logger = get_logger("[agentic-issue-runner]")


@dataclass(frozen=True)
class RunConfig:
    repo_root: Path
    run_id: str
    max_issues: int | None
    issue_iid: int | None
    loop: bool
    poll_interval_seconds: float
    worker_command: tuple[str, ...]


@dataclass(frozen=True)
class RunResult:
    attempted: int
    prompt_paths: tuple[Path, ...]


@dataclass(frozen=True)
class PromptRunConfig:
    repo_root: Path
    run_id: str
    max_issues: int
    issue_iid: int | None
    timeout_seconds: float


@dataclass(frozen=True)
class WorkerResult:
    returncode: int | None
    output: str
    approval_requested: bool = False
    timed_out: bool = False


@dataclass(frozen=True)
class PromptRunResult:
    attempted: int
    prompt_paths: tuple[Path, ...]
    blocked: int = 0
    worker_output_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ExecpolicyCheck:
    name: str
    command: tuple[str, ...]
    expected_decision: str


REQUIRED_EXECPOLICY_CHECKS = (
    ExecpolicyCheck("direct ssh stays forbidden", ("ssh", "example.com"), "forbidden"),
    ExecpolicyCheck(
        "safe_cmd remote-run stays allowed",
        (
            "python",
            "-m",
            "tools.agentic_issue_runner.safe_cmd",
            "remote-run",
            "--",
            "python",
            "-m",
            "pytest",
            "tests/agentic_issue_runner",
        ),
        "allow",
    ),
    ExecpolicyCheck(
        "safe_cmd remote-tmux stays allowed",
        (
            "python",
            "-m",
            "tools.agentic_issue_runner.safe_cmd",
            "remote-tmux",
            "--session",
            "overnight-smoke",
            "--",
            "python",
            "-m",
            "pytest",
            "tests/agentic_issue_runner",
        ),
        "allow",
    ),
    ExecpolicyCheck("apply_patch stays allowed", ("apply_patch",), "allow"),
)


def codex_command(repo_root: Path) -> tuple[str, ...]:
    return ("codex", "-a", "untrusted", "exec", "-C", str(repo_root), "-s", "workspace-write", "--json", "-")


def worker_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return the environment inherited by Codex workers.

    Workers need the operator's shell environment, including any proxy needed to
    reach external model/API services. GitLab and git mutations still go through
    GlabAdapter/safe_cmd, which remove proxy variables at the command boundary.
    Read-only GitLab commands from the worker inherit the proxy variables, so
    keep the self-managed GitLab host in NO_PROXY/no_proxy as a second guard.
    """
    inherited = dict(os.environ if env is None else env)
    for key in ("NO_PROXY", "no_proxy"):
        hosts = [part.strip() for part in inherited.get(key, "").split(",") if part.strip()]
        if PROJECT_HOST not in hosts:
            hosts.append(PROJECT_HOST)
        inherited[key] = ",".join(hosts)
    return inherited


def rules_path(repo_root: Path) -> Path:
    return repo_root / ".codex" / "rules" / "overnight-agent.rules"


def execpolicy_check_command(repo_root: Path, check: ExecpolicyCheck) -> tuple[str, ...]:
    return (
        "codex",
        "execpolicy",
        "check",
        "--pretty",
        "--rules",
        str(rules_path(repo_root).resolve()),
        "--",
        *check.command,
    )


def execpolicy_decision_from_output(stdout: str) -> str | None:
    """Extract the Codex execpolicy decision from its JSON output."""

    decoder = json.JSONDecoder()
    for index, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("decision") is not None:
            return str(payload["decision"])
    return None


def verify_execpolicy_preflight(repo_root: Path) -> str | None:
    """Fail closed unless the checked-in overnight execpolicy has expected decisions."""

    policy_path = rules_path(repo_root)
    if not policy_path.exists():
        return f"execpolicy rules file is missing: {policy_path.relative_to(repo_root)}"

    for check in REQUIRED_EXECPOLICY_CHECKS:
        command = execpolicy_check_command(repo_root, check)
        logger.info("Checking worker execpolicy: %s", " ".join(command))
        try:
            completed = subprocess.run(
                list(command),
                cwd=repo_root,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return "codex executable was not found while checking worker execpolicy"

        decision = execpolicy_decision_from_output(completed.stdout)
        if completed.returncode != 0 or decision != check.expected_decision:
            command_text = " ".join(check.command)
            details = (
                f"{check.name}: expected {check.expected_decision}, got {decision or 'unknown'} "
                f"for `{command_text}`"
            )
            if completed.stderr.strip():
                details = f"{details}; stderr={completed.stderr.strip()[-500:]}"
            if completed.stdout.strip():
                details = f"{details}; stdout={completed.stdout.strip()[-500:]}"
            return details
        logger.info("Execpolicy check passed: %s decision=%s.", check.name, decision)
    return None


def approval_requested_event(line: str) -> bool:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    values: list[str] = []
    for key in ("type", "event", "name", "status", "message", "reason"):
        value = payload.get(key)
        if value is not None:
            values.append(str(value).lower())
    nested = payload.get("msg") or payload.get("data") or payload.get("payload")
    if isinstance(nested, dict):
        for key in ("type", "event", "name", "message", "reason"):
            value = nested.get(key)
            if value is not None:
                values.append(str(value).lower())
    text = " ".join(values)
    return "approval" in text and any(token in text for token in ("request", "requested", "required", "ask"))


def summarize_worker_event(line: str) -> str:
    """Return a compact progress message for a Codex JSON event line."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return line.strip()[:240] or "blank output line"
    if not isinstance(payload, dict):
        return str(payload)[:240]

    parts: list[str] = []
    for key in ("type", "event", "name", "status", "reason", "message"):
        value = payload.get(key)
        if value is not None:
            parts.append(f"{key}={str(value)[:120]}")

    nested = payload.get("msg") or payload.get("data") or payload.get("payload")
    if isinstance(nested, dict):
        for key in ("type", "event", "name", "status", "reason", "message"):
            value = nested.get(key)
            if value is not None:
                parts.append(f"{key}={str(value)[:120]}")

    return "; ".join(parts)[:500] or f"json keys={','.join(sorted(payload)[:12])}"


def run_codex_worker(repo_root: Path, prompt: str, *, timeout_seconds: float) -> WorkerResult:
    command = codex_command(repo_root)
    logger.info("Starting Codex worker command: %s", " ".join(command))
    process = subprocess.Popen(
        list(command),
        cwd=repo_root,
        env=worker_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdin is not None
    process.stdin.write(prompt)
    process.stdin.close()

    output: list[str] = []
    approval_requested = False
    started = time.monotonic()
    last_progress = started
    last_output_at: float | None = None
    stdout = process.stdout
    while True:
        now = time.monotonic()
        elapsed = now - started
        if timeout_seconds > 0 and elapsed > timeout_seconds:
            logger.info("Codex worker timed out after %.1f seconds; terminating.", elapsed)
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            return WorkerResult(process.returncode, "".join(output), timed_out=True)
        if now - last_progress >= 30:
            if last_output_at is None:
                logger.info("Codex worker still running after %.1f seconds; no output received yet.", elapsed)
            else:
                logger.info(
                    "Codex worker still running after %.1f seconds; last output %.1f seconds ago.",
                    elapsed,
                    now - last_output_at,
                )
            last_progress = now
        if stdout is not None:
            readable, _, _ = select.select([stdout], [], [], 0.1)
            if readable:
                line = stdout.readline()
                if line:
                    output.append(line)
                    last_output_at = time.monotonic()
                    logger.info("Codex worker output: %s", summarize_worker_event(line))
                    if approval_requested_event(line):
                        approval_requested = True
                        logger.info("Codex worker requested approval; terminating fixed-permission run.")
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        break
                elif process.poll() is not None:
                    break
        if process.poll() is not None:
            if stdout is not None:
                remainder = stdout.read()
                if remainder:
                    output.append(remainder)
            break
    logger.info(
        "Codex worker exited returncode=%s approval_requested=%s output_lines=%d.",
        process.returncode,
        approval_requested,
        len(output),
    )
    return WorkerResult(process.returncode, "".join(output), approval_requested=approval_requested)


def _block_reason(result: WorkerResult) -> str:
    if result.approval_requested:
        return "worker requested an approval outside fixed permissions"
    if result.timed_out:
        return "worker timed out"
    if result.returncode not in (0, None):
        return f"worker exited nonzero with code {result.returncode}"
    return "worker exited without marking success or block"


def post_run_safety_failure(repo_root: Path, claim: dict) -> tuple[str, str] | None:
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_root,
        env=without_proxy_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    if branch.returncode != 0:
        state = f"branch=unknown ({branch.stderr.strip() or branch.stdout.strip()})"
        return "post-run safety check failed: could not read current branch", state
    current_branch = branch.stdout.strip()
    expected_branch = str(claim.get("branch") or "")
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo_root,
        env=without_proxy_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    status_text = status.stdout.strip() if status.returncode == 0 else f"status failed ({status.stderr.strip()})"
    state = f"branch={current_branch}; status={status_text or 'clean'}"
    if current_branch != expected_branch:
        return f"post-run safety check failed: current branch {current_branch} does not match claim {expected_branch}", state
    if status.returncode != 0:
        return "post-run safety check failed: could not read worktree status", state
    if status.stdout.strip():
        return "post-run safety check failed: worktree is dirty after worker exit", state
    return None


def finalize_unresolved_claim(repo_root: Path, *, run_id: str, result: WorkerResult) -> bool:
    claim = load_claim(repo_root, run_id)
    if claim is None or has_final_marker(repo_root, run_id):
        logger.info("No unresolved claim to finalize for run_id=%s.", run_id)
        return False
    logger.info("Finalizing unresolved claim for run_id=%s issue=%s.", run_id, claim.get("issue_id"))
    safety_failure = None
    if result.returncode == 0 and not result.approval_requested and not result.timed_out:
        safety_failure = post_run_safety_failure(repo_root, claim)
    blocker = _block_reason(result)
    branch_state = None
    if safety_failure is not None:
        blocker, branch_state = safety_failure
        logger.info("Post-run safety failure for run_id=%s: %s; %s", run_id, blocker, branch_state)
    else:
        logger.info("Blocking unresolved claim for run_id=%s: %s", run_id, blocker)
    return mark_blocked_from_claim(
        repo_root=repo_root,
        run_id=run_id,
        blocker=blocker,
        commands_run=[" ".join(codex_command(repo_root))],
        key_logs=result.output[-4000:] or "No worker output captured.",
        next_step="Inspect the worker output and re-run after fixing the blocker.",
        branch_state=branch_state,
    )


def write_worker_output(repo_root: Path, *, run_id: str, result: WorkerResult) -> Path:
    """Persist the raw Codex worker output beside the run state markers."""
    output_dir = repo_root / "artifacts" / "agentic_issue_runner" / run_id
    output_path = output_dir / "worker-output.log"
    assert_runtime_path(repo_root, output_path, run_id=run_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.output or "No worker output captured.\n", encoding="utf-8")
    logger.info("Persisted worker output to %s.", output_path.relative_to(repo_root))
    return output_path


def has_success_marker(repo_root: Path, run_id: str) -> bool:
    return marker_path(repo_root, run_id, SUCCESS_FILE).exists()


def preflight_scheduled_issue(
    adapter: GlabAdapter,
    item: ScheduledIssue,
) -> str | None:
    """Re-check issue claimability and dependency readiness before launching Codex."""

    issue = adapter.get_issue(item.issue.iid)
    if not is_eligible(issue):
        labels = ", ".join(sorted(issue.labels)) or "none"
        return f"issue #{issue.iid} is no longer claimable (state={issue.state}, labels={labels})"

    unresolved_blockers = set(issue.blocked_by)
    if not unresolved_blockers:
        return None

    blocker_issues = [adapter.get_issue(iid) for iid in sorted(unresolved_blockers)]
    candidate_issues = [issue, *blocker_issues]
    merge_requests = [*adapter.list_open_merge_requests(), *adapter.list_merged_merge_requests()]
    completed = completed_dependency_iids(candidate_issues, merge_requests)
    master_ready = master_ready_dependency_iids(candidate_issues, merge_requests)
    stack_dependencies = stack_ready_dependencies(candidate_issues, merge_requests)
    missing = sorted(iid for iid in unresolved_blockers if iid not in completed)
    if missing:
        blockers_text = ", ".join(f"#{iid}" for iid in missing)
        return f"dependencies are not complete: {blockers_text}"
    branch_targets = {
        stack_dependencies[iid].source_branch
        for iid in unresolved_blockers
        if iid in stack_dependencies
    }
    if len(branch_targets) > 1:
        return "dependencies require multiple unmerged blocker branches; human integration is required"
    if branch_targets and item.target_branch != next(iter(branch_targets)):
        return f"dependency branch changed; expected base branch {next(iter(branch_targets))}"
    missing_branch_evidence = sorted(
        iid
        for iid in unresolved_blockers
        if iid not in master_ready and iid not in stack_dependencies
    )
    if missing_branch_evidence:
        blockers_text = ", ".join(f"#{iid}" for iid in missing_branch_evidence)
        return f"dependencies have no branch evidence for stacking: {blockers_text}"
    if not branch_targets and item.target_branch != "master":
        return "dependency branch changed; expected base branch master"
    return None


def run_prompt_rounds(
    config: PromptRunConfig,
    *,
    plan: SchedulePlan,
    adapter: GlabAdapter | None = None,
) -> PromptRunResult:
    attempted = 0
    blocked = 0
    prompt_paths: list[Path] = []
    worker_output_paths: list[Path] = []
    if not plan.selected:
        logger.info("No selected issues in scheduler plan; no workers will be launched.")
    else:
        execpolicy_failure = verify_execpolicy_preflight(config.repo_root)
        if execpolicy_failure is not None:
            logger.info("Stopping before worker launch because execpolicy preflight failed: %s", execpolicy_failure)
            return PromptRunResult(
                attempted=attempted,
                prompt_paths=tuple(prompt_paths),
                blocked=blocked,
                worker_output_paths=tuple(worker_output_paths),
            )
    for index, item in enumerate(plan.selected, start=1):
        round_run_id = config.run_id if len(plan.selected) == 1 else f"{config.run_id}-round-{index}"
        logger.info(
            "Starting round %d/%d run_id=%s issue=#%d branch=%s target=%s lane=%s.",
            index,
            len(plan.selected),
            round_run_id,
            item.issue.iid,
            item.branch,
            item.target_branch,
            item.lane,
        )
        if adapter is not None:
            preflight_failure = preflight_scheduled_issue(adapter, item)
            if preflight_failure is not None:
                logger.info(
                    "Stopping before worker launch for issue #%d: %s",
                    item.issue.iid,
                    preflight_failure,
                )
                break
            logger.info("Preflight dependency check passed for issue #%d.", item.issue.iid)

        prompt_path = write_prompt(config.repo_root, item, run_id=round_run_id)
        prompt_paths.append(prompt_path)
        logger.info("Wrote worker prompt to %s.", prompt_path.relative_to(config.repo_root))
        result = run_codex_worker(
            config.repo_root,
            prompt_path.read_text(encoding="utf-8"),
            timeout_seconds=config.timeout_seconds,
        )
        worker_output_paths.append(write_worker_output(config.repo_root, run_id=round_run_id, result=result))
        claim = load_claim(config.repo_root, round_run_id)
        if claim is not None:
            attempted += 1
            logger.info("Worker claimed issue #%s for run_id=%s.", claim.get("issue_id"), round_run_id)
        else:
            logger.info("Worker exited before claim for run_id=%s.", round_run_id)
        blocked_by_launcher = finalize_unresolved_claim(config.repo_root, run_id=round_run_id, result=result)
        if blocked_by_launcher:
            blocked += 1
            logger.info("Launcher marked run_id=%s as blocked.", round_run_id)
        elif claim is not None and has_success_marker(config.repo_root, round_run_id):
            logger.info(
                "Issue #%d marked successful in this run; downstream dependencies still require GitLab completion evidence.",
                item.issue.iid,
            )
        if (
            claim is None
            or blocked_by_launcher
            or result.approval_requested
            or result.timed_out
            or result.returncode not in (0, None)
        ):
            logger.info(
                "Stopping prompt rounds after run_id=%s claim_exists=%s blocked_by_launcher=%s "
                "approval_requested=%s timed_out=%s returncode=%s.",
                round_run_id,
                claim is not None,
                blocked_by_launcher,
                result.approval_requested,
                result.timed_out,
                result.returncode,
            )
            break
        logger.info("Completed round %d/%d run_id=%s.", index, len(plan.selected), round_run_id)
    return PromptRunResult(
        attempted=attempted,
        prompt_paths=tuple(prompt_paths),
        blocked=blocked,
        worker_output_paths=tuple(worker_output_paths),
    )


def expand_with_blockers(adapter: GlabAdapter, issues: list[IssueRecord]) -> list[IssueRecord]:
    issue_by_iid = {issue.iid: issue for issue in issues}
    queue = [blocker for issue in issues for blocker in issue.blocked_by if blocker not in issue_by_iid]
    while queue:
        iid = queue.pop(0)
        if iid in issue_by_iid:
            continue
        blocker = adapter.get_issue(iid)
        issue_by_iid[iid] = blocker
        queue.extend(parent for parent in blocker.blocked_by if parent not in issue_by_iid)
    return list(issue_by_iid.values())


def collect_dependency_evidence(
    adapter: GlabAdapter,
    issues: list[IssueRecord],
) -> tuple[set[int], dict[int, StackDependency], set[int]]:
    merge_requests = [*adapter.list_open_merge_requests(), *adapter.list_merged_merge_requests()]
    return (
        completed_dependency_iids(issues, merge_requests),
        stack_ready_dependencies(issues, merge_requests),
        master_ready_dependency_iids(issues, merge_requests),
    )


def build_plan_with_remote_evidence(
        adapter: GlabAdapter,
        *,
        issue_iid: int | None = None,
        max_issues: int | None = None,
) -> SchedulePlan:
    ready_issues = adapter.list_ready_issues()
    planning_issues = expand_with_blockers(adapter, ready_issues)
    completed_iids, stack_dependencies, master_ready_iids = collect_dependency_evidence(adapter, planning_issues)
    dependency_mrs = {iid: dep.mr_iid for iid, dep in stack_dependencies.items()}
    dependency_branches = {iid: dep.source_branch for iid, dep in stack_dependencies.items()}
    return build_dry_run_plan(
        planning_issues,
        issue_iid=issue_iid,
        max_issues=max_issues,
        completed_iids=completed_iids,
        dependency_mrs=dependency_mrs,
        dependency_branches=dependency_branches,
        master_ready_iids=master_ready_iids,
    )


def write_prompt(repo_root: Path, item: ScheduledIssue, *, run_id: str) -> Path:
    prompt_dir = repo_root / "artifacts" / "agentic_issue_runner" / run_id
    prompt_path = prompt_dir / f"issue-{item.issue.iid}.prompt.md"
    assert_runtime_path(repo_root, prompt_path, run_id=run_id)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt = build_worker_prompt(
        item,
        run_id=run_id,
        prompt_path=str(prompt_path.relative_to(repo_root)),
        project_spec=load_project_runtime_spec(repo_root),
    )
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def run_worker(repo_root: Path, command: tuple[str, ...], prompt: str) -> subprocess.CompletedProcess[str]:
    assert_unattended_command(command)
    return subprocess.run(
        list(command),
        cwd=repo_root,
        env=worker_env(),
        input=prompt,
        text=True,
        check=False,
        capture_output=True,
    )


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_unattended(adapter: GlabAdapter, config: RunConfig) -> RunResult:
    raise RuntimeError("legacy scheduler-owned run_unattended is disabled; use run_prompt_rounds")
