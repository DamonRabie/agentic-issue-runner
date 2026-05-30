"""Dependency-aware issue scheduling."""

from __future__ import annotations

from collections import defaultdict, deque

from tools.agentic_issue_runner.constants import (
    AGENT_IN_PROGRESS_LABEL,
    HEAVY_RESOURCE_LABELS,
    HIGH_PRIORITY_LABELS,
    MAIN_BRANCH,
    READY_LABEL,
)
from tools.agentic_issue_runner.git_planner import branch_name
from tools.agentic_issue_runner.models import IssueRecord, ScheduledIssue, SchedulePlan


def is_eligible(issue: IssueRecord) -> bool:
    return (
        issue.state in {"open", "opened"}
        and READY_LABEL in issue.labels
        and AGENT_IN_PROGRESS_LABEL not in issue.labels
    )


def resource_lane(issue: IssueRecord) -> str:
    if issue.labels & HEAVY_RESOURCE_LABELS:
        return "heavy"
    return "light"


def _ineligible_reason(issue: IssueRecord) -> str:
    if issue.state not in {"open", "opened"}:
        return "issue is not open"
    if READY_LABEL not in issue.labels:
        return "missing ready-for-agent label"
    if AGENT_IN_PROGRESS_LABEL in issue.labels:
        return "already agent-in-progress"
    return "not eligible"


def _dependency_map(issues: list[IssueRecord]) -> dict[int, set[int]]:
    blockers = {issue.iid: set(issue.blocked_by) for issue in issues}
    for issue in issues:
        for downstream in issue.blocks:
            blockers.setdefault(downstream, set()).add(issue.iid)
    return blockers


def _downstream_counts(issues: list[IssueRecord], blockers: dict[int, set[int]]) -> dict[int, int]:
    graph: dict[int, set[int]] = defaultdict(set)
    issue_ids = {issue.iid for issue in issues}
    for issue_id, blocker_ids in blockers.items():
        for blocker in blocker_ids:
            if blocker in issue_ids and issue_id in issue_ids:
                graph[blocker].add(issue_id)

    counts: dict[int, int] = {}
    for issue in issues:
        seen: set[int] = set()
        queue = deque(graph.get(issue.iid, set()))
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(graph.get(current, set()))
        counts[issue.iid] = len(seen | set(issue.blocks))
    return counts


def _sort_key(issue: IssueRecord, downstream_counts: dict[int, int]) -> tuple[int, int, object, int]:
    high_priority = int(bool(issue.labels & HIGH_PRIORITY_LABELS))
    return (-high_priority, -downstream_counts.get(issue.iid, 0), issue.created_datetime, issue.iid)


def _branch_target_for_blockers(
    blockers: set[int],
    *,
    selected_branches: dict[int, str],
    dependency_branches: dict[int, str],
    master_ready_iids: set[int],
) -> tuple[str | None, str | None]:
    branch_targets = {
        selected_branches[blocker]
        for blocker in blockers
        if blocker in selected_branches
    }
    branch_targets.update(
        dependency_branches[blocker]
        for blocker in blockers
        if blocker in dependency_branches
    )
    if len(branch_targets) > 1:
        return None, "multiple unmerged blocker branches require human integration"
    missing_base = sorted(
        blocker
        for blocker in blockers
        if blocker not in selected_branches
        and blocker not in dependency_branches
        and blocker not in master_ready_iids
    )
    if missing_base:
        blockers_text = ", ".join(f"#{iid}" for iid in missing_base)
        return None, f"missing branch evidence for resolved blocker(s): {blockers_text}"
    if branch_targets:
        return next(iter(branch_targets)), None
    return MAIN_BRANCH, None


def plan_schedule(
    issues: list[IssueRecord],
    *,
    max_issues: int | None = None,
    completed_iids: set[int] | None = None,
    dependency_mrs: dict[int, int] | None = None,
    dependency_branches: dict[int, str] | None = None,
    master_ready_iids: set[int] | None = None,
    light_capacity: int = 4,
    heavy_capacity: int = 1,
) -> SchedulePlan:
    completed = set(completed_iids or set())
    known_dependency_mrs = dict(dependency_mrs or {})
    known_dependency_branches = dict(dependency_branches or {})
    master_ready = set(master_ready_iids) if master_ready_iids is not None else set(completed)
    candidates = [issue for issue in issues if is_eligible(issue)]
    blockers = _dependency_map(issues)
    downstream_counts = _downstream_counts(candidates, blockers)
    selected: list[ScheduledIssue] = []
    selected_branches: dict[int, str] = {}
    selected_iids: set[int] = set()
    blocked_iids: set[int] = set()
    blocked_reasons: dict[int, str] = {}
    lane_counts = {"light": 0, "heavy": 0}
    limit = max_issues if max_issues is not None else len(candidates)

    while len(selected) < limit:
        runnable = [
            issue
            for issue in candidates
            if issue.iid not in selected_iids
            and issue.iid not in blocked_iids
            and blockers.get(issue.iid, set()).issubset(completed | selected_iids)
        ]
        runnable.sort(key=lambda issue: _sort_key(issue, downstream_counts))

        picked = None
        picked_blockers: set[int] = set()
        for issue in runnable:
            lane = resource_lane(issue)
            if selected and lane == "heavy":
                continue
            capacity = light_capacity if lane == "light" else heavy_capacity
            if lane_counts[lane] < capacity:
                issue_blockers = blockers.get(issue.iid, set())
                picked = issue
                picked_blockers = issue_blockers
                break
        if picked is None:
            break

        lane = resource_lane(picked)
        branch = branch_name(picked.iid, picked.title)
        target_branch, blocked_reason = _branch_target_for_blockers(
            picked_blockers,
            selected_branches=selected_branches,
            dependency_branches=known_dependency_branches,
            master_ready_iids=master_ready,
        )
        if blocked_reason:
            blocked_iids.add(picked.iid)
            blocked_reasons[picked.iid] = blocked_reason
            continue
        stack_mrs = tuple(
            mr_iid
            for blocker in sorted(picked_blockers)
            for mr_iid in (known_dependency_mrs.get(blocker),)
            if mr_iid
        )
        selected.append(
            ScheduledIssue(
                issue=picked,
                lane=lane,
                branch=branch,
                target_branch=str(target_branch),
                blocked_by=tuple(sorted(picked_blockers)),
                stack_mrs=stack_mrs,
            )
        )
        selected_iids.add(picked.iid)
        selected_branches[picked.iid] = branch
        lane_counts[lane] += 1
        if lane == "heavy":
            break

    blocked = [
        issue
        for issue in candidates
        if issue.iid in blocked_iids
        or (
            issue.iid not in selected_iids
            and not blockers.get(issue.iid, set()).issubset(completed | selected_iids)
        )
    ]
    skipped = [issue for issue in issues if issue not in candidates or issue.iid not in selected_iids and issue not in blocked]
    candidate_iids = {issue.iid for issue in candidates}
    selected_lanes = {item.lane for item in selected}
    skipped_reasons: dict[int, str] = {}
    for issue in skipped:
        if issue.iid in completed:
            skipped_reasons[issue.iid] = "completed dependency"
        elif issue.iid not in candidate_iids:
            skipped_reasons[issue.iid] = _ineligible_reason(issue)
        elif "heavy" in selected_lanes:
            skipped_reasons[issue.iid] = "top-ranked selected issue is heavy; heavy runs alone"
        elif resource_lane(issue) == "heavy" and "light" in selected_lanes:
            skipped_reasons[issue.iid] = "heavy issue skipped because this batch selected light issues"
        elif len(selected) >= limit:
            skipped_reasons[issue.iid] = "max-issues limit reached"
        elif lane_counts[resource_lane(issue)] >= (light_capacity if resource_lane(issue) == "light" else heavy_capacity):
            skipped_reasons[issue.iid] = f"{resource_lane(issue)} capacity reached"
        else:
            skipped_reasons[issue.iid] = "not selected by scheduler"
    return SchedulePlan(tuple(selected), tuple(blocked), tuple(skipped), blocked_reasons, skipped_reasons)
