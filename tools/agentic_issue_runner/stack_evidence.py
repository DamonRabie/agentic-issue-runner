"""Evidence helpers for completed agent issue dependencies."""

from __future__ import annotations

import re

from tools.agentic_issue_runner.constants import (
    AGENT_BRANCH_PREFIX,
    AGENT_MR_OPENED_LABEL,
    AGENT_NEEDS_REVIEW_LABEL,
    COMPLETED_LABEL,
)
from tools.agentic_issue_runner.models import IssueRecord, MergeRequestRecord, StackDependency

_RELATED_ISSUE_RE = re.compile(r"related issue:\s*#(?P<iid>\d+)", re.IGNORECASE)


def infer_issue_iid(mr: MergeRequestRecord) -> int | None:
    if mr.issue_iid is not None:
        return mr.issue_iid
    branch_match = re.match(rf"{re.escape(AGENT_BRANCH_PREFIX)}/(?P<iid>\d+)-", mr.source_branch)
    if branch_match:
        return int(branch_match.group("iid"))
    for value in (mr.description, mr.title):
        match = _RELATED_ISSUE_RE.search(value or "")
        if match:
            return int(match.group("iid"))
    return None


def issue_label_completed(issue: IssueRecord) -> bool:
    """Return whether issue labels alone satisfy dependency completion."""

    agent_reviewed = {AGENT_MR_OPENED_LABEL, AGENT_NEEDS_REVIEW_LABEL}.issubset(issue.labels)
    return COMPLETED_LABEL in issue.labels or agent_reviewed


def issue_label_master_ready(issue: IssueRecord) -> bool:
    """Return whether issue labels imply the dependency is already on master."""

    return COMPLETED_LABEL in issue.labels


def mr_dependency_evidence(
    issues: list[IssueRecord],
    merge_requests: list[MergeRequestRecord],
) -> dict[int, StackDependency]:
    """Return latest open/merged MR evidence keyed by related issue IID."""

    issue_by_iid = {issue.iid: issue for issue in issues}
    ready: dict[int, StackDependency] = {}
    for mr in merge_requests:
        issue_iid = infer_issue_iid(mr)
        if issue_iid is None or issue_iid not in issue_by_iid:
            continue
        if mr.state.lower() not in {"opened", "open", "merged"}:
            continue
        current = ready.get(issue_iid)
        if current is None or mr.iid > current.mr_iid:
            ready[issue_iid] = StackDependency(
                issue_iid=issue_iid,
                mr_iid=mr.iid,
                source_branch=mr.source_branch,
                target_branch=mr.target_branch,
                state=mr.state,
                web_url=mr.web_url,
            )
    return ready


def stack_ready_dependencies(
    issues: list[IssueRecord],
    merge_requests: list[MergeRequestRecord],
) -> dict[int, StackDependency]:
    """Return open MR branch evidence for stacked dependency work."""

    return {
        issue_iid: dependency
        for issue_iid, dependency in mr_dependency_evidence(issues, merge_requests).items()
        if dependency.state.lower() in {"opened", "open"}
    }


def master_ready_dependency_iids(issues: list[IssueRecord], merge_requests: list[MergeRequestRecord]) -> set[int]:
    """Return dependencies whose completed work should be available on master."""

    completed = {issue.iid for issue in issues if issue_label_master_ready(issue)}
    completed.update(
        issue_iid
        for issue_iid, dependency in mr_dependency_evidence(issues, merge_requests).items()
        if dependency.state.lower() == "merged"
    )
    return completed


def completed_dependency_iids(issues: list[IssueRecord], merge_requests: list[MergeRequestRecord]) -> set[int]:
    """Return issue IIDs that satisfy any dependency completion criterion."""

    completed = {issue.iid for issue in issues if issue_label_completed(issue)}
    completed.update(master_ready_dependency_iids(issues, merge_requests))
    return completed
