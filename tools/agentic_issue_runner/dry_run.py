"""Dry-run plan rendering."""

from __future__ import annotations

from tools.agentic_issue_runner.constants import READY_LABEL
from tools.agentic_issue_runner.models import IssueRecord, SchedulePlan
from tools.agentic_issue_runner.scheduler import plan_schedule


def filter_issue(issues: list[IssueRecord], issue_iid: int | None) -> list[IssueRecord]:
    if issue_iid is None:
        return issues
    return [issue for issue in issues if issue.iid == issue_iid]


def build_dry_run_plan(
    issues: list[IssueRecord],
    *,
    issue_iid: int | None = None,
    max_issues: int | None = None,
    completed_iids: set[int] | None = None,
    dependency_mrs: dict[int, int] | None = None,
    dependency_branches: dict[int, str] | None = None,
    master_ready_iids: set[int] | None = None,
) -> SchedulePlan:
    return plan_schedule(
        filter_issue(issues, issue_iid),
        max_issues=max_issues,
        completed_iids=completed_iids,
        dependency_mrs=dependency_mrs,
        dependency_branches=dependency_branches,
        master_ready_iids=master_ready_iids,
    )


def render_dry_run(plan: SchedulePlan, *, max_issues: int | None = None) -> str:
    lines = ["Agentic issue runner dry-run", ""]
    lines.append(f"Max issues: {max_issues if max_issues is not None else 'unbounded'}")
    lines.append("")
    lines.append("Selected issues:")
    if not plan.selected:
        lines.append("- None")
    for item in plan.selected:
        blocker_text = ", ".join(f"#{iid}" for iid in item.blocked_by) or "None"
        stack_text = f" | dependency_mrs={','.join(f'!{iid}' for iid in item.stack_mrs)}" if item.stack_mrs else ""
        lines.append(
            f"- #{item.issue.iid} [{item.lane}] {item.issue.title} | "
            f"branch={item.branch} | target={item.target_branch} | blocked_by={blocker_text}{stack_text}"
        )
    lines.append("")
    lines.append("Blocked issues:")
    if not plan.blocked:
        lines.append("- None")
    for issue in plan.blocked:
        blockers = ", ".join(f"#{iid}" for iid in sorted(issue.blocked_by)) or "None"
        reason = plan.blocked_reasons.get(issue.iid)
        reason_text = f" | reason={reason}" if reason else ""
        lines.append(f"- #{issue.iid} {issue.title} | blocked_by={blockers}{reason_text}")
    lines.append("")
    lines.append("Skipped issues:")
    if not plan.skipped:
        lines.append("- None")
    for issue in plan.skipped:
        labels = ",".join(sorted(issue.labels)) or "None"
        reason = plan.skipped_reasons.get(issue.iid, "not selected by scheduler")
        lines.append(f"- #{issue.iid} {issue.title} | labels={labels} | reason={reason}")
    lines.append("")
    lines.append("Label transition preview:")
    for item in plan.selected:
        lines.append(
            f"- #{item.issue.iid}: remove `{READY_LABEL}`, add `agent-in-progress`; "
            "on success add `agent-mr-opened`, `agent-needs-review`; on failure add `agent-blocked`"
        )
    if not plan.selected:
        lines.append("- None")
    return "\n".join(lines)
