"""Small data containers for issue-runner planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def parse_created_at(value: str | None) -> datetime:
    """Parse GitLab-ish timestamps, returning max time for missing values."""
    if not value:
        return datetime.max.replace(tzinfo=timezone.utc)
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class DependencySections:
    blocked_by: tuple[int, ...] = ()
    blocks: tuple[int, ...] = ()


@dataclass(frozen=True)
class IssueRecord:
    iid: int
    title: str
    description: str = ""
    labels: frozenset[str] = field(default_factory=frozenset)
    state: str = "opened"
    created_at: str | None = None
    blocked_by: frozenset[int] = field(default_factory=frozenset)
    blocks: frozenset[int] = field(default_factory=frozenset)

    @property
    def created_datetime(self) -> datetime:
        return parse_created_at(self.created_at)


@dataclass(frozen=True)
class ScheduledIssue:
    issue: IssueRecord
    lane: str
    branch: str
    target_branch: str
    blocked_by: tuple[int, ...]
    stack_mrs: tuple[int, ...] = ()


@dataclass(frozen=True)
class SchedulePlan:
    selected: tuple[ScheduledIssue, ...]
    blocked: tuple[IssueRecord, ...]
    skipped: tuple[IssueRecord, ...]
    blocked_reasons: dict[int, str] = field(default_factory=dict)
    skipped_reasons: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MergeRequestRecord:
    iid: int
    title: str
    source_branch: str
    target_branch: str
    state: str = "opened"
    web_url: str = ""
    description: str = ""
    issue_iid: int | None = None


@dataclass(frozen=True)
class StackDependency:
    issue_iid: int
    mr_iid: int
    source_branch: str
    target_branch: str
    state: str = "opened"
    web_url: str = ""


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class MergeRequestPlan:
    issue_iid: int
    source_branch: str
    target_branch: str
    title: str
    description: str
    push_args: tuple[str, ...]
    create_args: tuple[str, ...]
