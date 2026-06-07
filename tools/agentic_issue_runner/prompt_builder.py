"""Prompt generation for unattended issue workers.

The tracked worker template is the reviewable safety contract. This module only
fills run-specific fields such as issue metadata, dependency evidence, and the
repo-local runtime specification block.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

from tools.agentic_issue_runner.models import ScheduledIssue
from tools.agentic_issue_runner.project_spec import ProjectRuntimeSpec

WORKER_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "worker_system.md"


def load_worker_system_prompt(path: Path = WORKER_SYSTEM_PROMPT_PATH) -> Template:
    """Load the tracked worker prompt template used by unattended runs."""

    return Template(path.read_text(encoding="utf-8"))


def _selected_issue_metadata(item: ScheduledIssue, *, blockers: str, stack: str) -> str:
    return "\n".join(
        (
            f"- Issue: #{item.issue.iid} {item.issue.title}",
            f"- Lane: {item.lane}",
            f"- Branch: {item.branch}",
            f"- MR target branch: {item.target_branch}",
            f"- Blocked by: {blockers}",
            f"- Dependency MRs: {stack}",
        )
    )


def _dependency_evidence(item: ScheduledIssue, *, stack: str) -> str:
    if not (item.blocked_by and item.stack_mrs):
        return ""
    return (
        "\nDependency evidence: the scheduler selected this issue because its blocker "
        f"has completion evidence ({stack}). Use the planned target branch "
        f"`{item.target_branch}` unless the issue is no longer claimable.\n"
        "Do not reinterpret dependency status inside this worker.\n"
    )


def build_worker_prompt(
    item: ScheduledIssue,
    *,
    run_id: str,
    prompt_path: str,
    project_spec: ProjectRuntimeSpec | None = None,
    worker_label: str = "Codex worker",
) -> str:
    """Render the worker prompt.

    `worker_label` is substituted into the prompt's opening sentence so a single
    tracked template covers both Codex and Claude Code workers. Callers pass
    "Codex worker" or "Claude Code worker"; the rest of the template (workflow,
    safety, runtime block) stays CLI-agnostic.
    """
    blockers = ", ".join(f"#{iid}" for iid in item.blocked_by) or "None"
    stack = ", ".join(f"!{iid}" for iid in item.stack_mrs) or "None"
    return load_worker_system_prompt().substitute(
        run_id=run_id,
        prompt_path=prompt_path,
        worker_label=worker_label,
        runtime_spec_block=(project_spec or ProjectRuntimeSpec()).prompt_block(),
        selected_issue_metadata=_selected_issue_metadata(item, blockers=blockers, stack=stack),
        dependency_evidence=_dependency_evidence(item, stack=stack),
        issue_iid=str(item.issue.iid),
        branch=item.branch,
        target_branch=item.target_branch,
        claim_command=(
            "python -m tools.agentic_issue_runner.safe_cmd claim "
            f"--run-id {run_id} --issue {item.issue.iid} --branch {item.branch} "
            f"--base-branch {item.target_branch}"
        ),
        branch_command=(
            "python -m tools.agentic_issue_runner.safe_cmd git branch "
            f"--branch {item.branch} --base-branch {item.target_branch}"
        ),
        issue_body=item.issue.description,
    )
