"""Issue label and comment transition helpers."""

from __future__ import annotations

from dataclasses import dataclass

from tools.agentic_issue_runner.constants import (
    AGENT_BLOCKED_LABEL,
    AGENT_IN_PROGRESS_LABEL,
    AGENT_MANAGED_LABELS,
    AGENT_MR_OPENED_LABEL,
    AGENT_NEEDS_REVIEW_LABEL,
    MAIN_BRANCH,
    READY_LABEL,
)
from tools.agentic_issue_runner.glab_adapter import GlabAdapter
from tools.agentic_issue_runner.models import IssueRecord


@dataclass(frozen=True)
class IssueTransition:
    add_labels: tuple[str, ...]
    remove_labels: tuple[str, ...]
    comment: str


def claim_transition(
    issue: IssueRecord,
    *,
    run_id: str,
    timestamp: str,
    branch: str,
    base_branch: str = MAIN_BRANCH,
) -> IssueTransition | None:
    if READY_LABEL not in issue.labels or AGENT_IN_PROGRESS_LABEL in issue.labels:
        return None
    return IssueTransition(
        add_labels=(AGENT_IN_PROGRESS_LABEL,),
        remove_labels=(READY_LABEL,),
        comment=(
            f"Agent run {run_id} claimed issue #{issue.iid} at {timestamp}.\n"
            f"Branch: {branch}\n"
            f"Base branch: {base_branch}"
        ),
    )


def success_transition(
    *,
    mr_link: str,
    validation: str,
    self_review: str,
    changed_files: list[str],
    residual_risks: str,
) -> IssueTransition:
    files = "\n".join(f"- {path}" for path in changed_files) or "- None"
    return IssueTransition(
        add_labels=(AGENT_MR_OPENED_LABEL, AGENT_NEEDS_REVIEW_LABEL),
        remove_labels=(AGENT_IN_PROGRESS_LABEL,),
        comment=(
            f"MR: {mr_link}\n\n"
            f"Validation:\n{validation}\n\n"
            f"Self-review:\n{self_review}\n\n"
            f"Changed files:\n{files}\n\n"
            f"Residual risks:\n{residual_risks}"
        ),
    )


def blocked_transition(
    *,
    blocker: str,
    commands_run: list[str],
    key_logs: str,
    next_step: str,
    branch_state: str = "",
) -> IssueTransition:
    commands = "\n".join(f"- `{command}`" for command in commands_run) or "- None"
    branch = f"\n\nBranch/worktree state:\n{branch_state}" if branch_state else ""
    return IssueTransition(
        add_labels=(AGENT_BLOCKED_LABEL,),
        remove_labels=(AGENT_IN_PROGRESS_LABEL,),
        comment=(
            f"Blocked: {blocker}\n\n"
            f"Commands run:\n{commands}\n\n"
            f"Key logs:\n{key_logs}{branch}\n\n"
            f"Recommended next step:\n{next_step}"
        ),
    )


def validate_transition(transition: IssueTransition, *, allow_ready_removal: bool = False) -> None:
    for label in transition.add_labels:
        if label not in AGENT_MANAGED_LABELS:
            raise ValueError(f"cannot add unmanaged label: {label}")
    for label in transition.remove_labels:
        if label == READY_LABEL and allow_ready_removal:
            continue
        if label not in AGENT_MANAGED_LABELS:
            raise ValueError(f"cannot remove unmanaged label: {label}")


def apply_transition(
    adapter: GlabAdapter,
    iid: int,
    transition: IssueTransition,
    *,
    allow_ready_removal: bool = False,
) -> None:
    validate_transition(transition, allow_ready_removal=allow_ready_removal)
    adapter.update_issue_labels(iid, add_labels=transition.add_labels, remove_labels=transition.remove_labels)
    adapter.note_issue(iid, transition.comment)


def claim_issue(adapter: GlabAdapter, iid: int, *, run_id: str, timestamp: str, branch: str) -> bool:
    issue = adapter.get_issue(iid)
    transition = claim_transition(issue, run_id=run_id, timestamp=timestamp, branch=branch)
    if transition is None:
        return False
    apply_transition(adapter, iid, transition, allow_ready_removal=True)
    return True


def mark_success(
    adapter: GlabAdapter,
    iid: int,
    *,
    mr_link: str,
    validation: str,
    self_review: str,
    changed_files: list[str],
    residual_risks: str,
) -> None:
    apply_transition(
        adapter,
        iid,
        success_transition(
            mr_link=mr_link,
            validation=validation,
            self_review=self_review,
            changed_files=changed_files,
            residual_risks=residual_risks,
        ),
    )


def mark_blocked(
    adapter: GlabAdapter,
    iid: int,
    *,
    blocker: str,
    commands_run: list[str],
    key_logs: str,
    next_step: str,
    branch_state: str = "",
) -> None:
    apply_transition(
        adapter,
        iid,
        blocked_transition(
            blocker=blocker,
            commands_run=commands_run,
            key_logs=key_logs,
            next_step=next_step,
            branch_state=branch_state,
        ),
    )
