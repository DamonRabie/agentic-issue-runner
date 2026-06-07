"""Local markdown issue adapter.

Drop-in replacement for ``GlabAdapter`` when the project has no GitLab remote
and issues live as files under ``docs/issues/ISSUE-NNN-*.md``.

The adapter exposes the subset of the GlabAdapter surface that ``runner.py``
and ``safe_cmd.py`` consume: ``verify_access``, ``list_ready_issues``,
``get_issue``, ``list_open_merge_requests``, ``list_merged_merge_requests``,
``update_issue_labels``, ``note_issue``.

Issue identity: ``ISSUE-007-...md`` -> ``iid = 7``. Frontmatter ``status:`` is
the source of truth for lifecycle; the adapter synthesizes the label set
expected by ``stack_evidence.py`` so dependency resolution works unchanged.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from tools.agentic_issue_runner.constants import (
    AGENT_BLOCKED_LABEL,
    AGENT_IN_PROGRESS_LABEL,
    COMPLETED_LABEL,
    READY_LABEL,
)
from tools.agentic_issue_runner.models import CommandResult, IssueRecord, MergeRequestRecord


_ISSUE_FILE_RE = re.compile(r"^ISSUE-(\d{3,})-.+\.md$")
_BLOCKED_BY_REF_RE = re.compile(r"ISSUE-(\d+)")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_ACTIVITY_HEADER = "## Activity log"


class LocalIssueAdapter:
    """Reads markdown issue files and mutates their frontmatter in place."""

    def __init__(self, repo_root: Path, issues_dir: str = "docs/issues", *, dry_run: bool = False) -> None:
        self.repo_root = repo_root
        self.issues_dir = repo_root / issues_dir
        self.dry_run = dry_run
        self.calls: list[tuple[str, ...]] = []

    def verify_access(self) -> None:
        if not self.issues_dir.is_dir():
            raise RuntimeError(f"local issues directory not found: {self.issues_dir}")

    def _iter_issue_paths(self) -> Iterable[tuple[int, Path]]:
        if not self.issues_dir.is_dir():
            return
        for path in sorted(self.issues_dir.iterdir()):
            match = _ISSUE_FILE_RE.match(path.name)
            if not match:
                continue
            yield int(match.group(1)), path

    def _path_for_iid(self, iid: int) -> Path:
        for found_iid, path in self._iter_issue_paths():
            if found_iid == iid:
                return path
        raise RuntimeError(f"no local issue file matches iid={iid} in {self.issues_dir}")

    def get_issue(self, iid: int) -> IssueRecord:
        return _parse_issue_file(iid, self._path_for_iid(iid))

    def list_ready_issues(self) -> list[IssueRecord]:
        records: list[IssueRecord] = []
        for iid, path in self._iter_issue_paths():
            record = _parse_issue_file(iid, path)
            if READY_LABEL in record.labels:
                records.append(record)
        return records

    def list_open_merge_requests(self) -> list[MergeRequestRecord]:
        return []

    def list_merged_merge_requests(self) -> list[MergeRequestRecord]:
        return []

    def update_issue_labels(
        self,
        iid: int,
        *,
        add_labels: tuple[str, ...] = (),
        remove_labels: tuple[str, ...] = (),
    ) -> CommandResult:
        path = self._path_for_iid(iid)
        new_status = _status_from_label_transition(add_labels, remove_labels)
        if new_status is not None and not self.dry_run:
            _rewrite_frontmatter_status(path, new_status)
        args = ("local", "issue", "update", path.name, *(f"+{label}" for label in add_labels), *(f"-{label}" for label in remove_labels))
        self.calls.append(args)
        return CommandResult(args, 0, "", "")

    def note_issue(self, iid: int, body: str) -> CommandResult:
        path = self._path_for_iid(iid)
        if not self.dry_run:
            _append_activity_log(path, body)
        args = ("local", "issue", "note", path.name)
        self.calls.append(args)
        return CommandResult(args, 0, "", "")


def _parse_issue_file(iid: int, path: Path) -> IssueRecord:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    title = _frontmatter_value(frontmatter, "title") or path.stem
    status = (_frontmatter_value(frontmatter, "status") or "open").strip().lower()
    blocked_by = _parse_blocked_by(frontmatter)
    labels = _synthesize_labels(status)
    state = "closed" if status == "done" else "opened"
    return IssueRecord(
        iid=iid,
        title=title,
        description=body,
        labels=labels,
        state=state,
        created_at=_frontmatter_value(frontmatter, "created"),
        blocked_by=frozenset(blocked_by),
        blocks=frozenset(),
    )


def _split_frontmatter(text: str) -> tuple[str, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return "", text
    return match.group(1), match.group(2)


def _frontmatter_value(frontmatter: str, key: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(key)}\s*:\s*(.+?)\s*$", re.MULTILINE)
    match = pattern.search(frontmatter)
    if not match:
        return None
    value = match.group(1).strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    return value


def _parse_blocked_by(frontmatter: str) -> tuple[int, ...]:
    raw = _frontmatter_value(frontmatter, "blocked_by") or ""
    if not raw or raw.strip().lower() in {"[]", "none"}:
        return ()
    refs = {int(match.group(1)) for match in _BLOCKED_BY_REF_RE.finditer(raw)}
    return tuple(sorted(refs))


def _synthesize_labels(status: str) -> frozenset[str]:
    if status == "done":
        return frozenset({COMPLETED_LABEL})
    if status == "in-progress":
        return frozenset({AGENT_IN_PROGRESS_LABEL})
    if status == "blocked":
        return frozenset({AGENT_BLOCKED_LABEL})
    return frozenset({READY_LABEL})


def _status_from_label_transition(add: tuple[str, ...], remove: tuple[str, ...]) -> str | None:
    if COMPLETED_LABEL in add:
        return "done"
    if AGENT_IN_PROGRESS_LABEL in add:
        return "in-progress"
    if AGENT_BLOCKED_LABEL in add:
        return "blocked"
    if AGENT_IN_PROGRESS_LABEL in remove and COMPLETED_LABEL not in add and AGENT_BLOCKED_LABEL not in add:
        return "open"
    return None


def _rewrite_frontmatter_status(path: Path, new_status: str) -> None:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    if not frontmatter:
        return
    if re.search(r"^status\s*:.*$", frontmatter, re.MULTILINE):
        new_frontmatter = re.sub(r"^status\s*:.*$", f"status: {new_status}", frontmatter, count=1, flags=re.MULTILINE)
    else:
        new_frontmatter = frontmatter.rstrip() + f"\nstatus: {new_status}\n"
    path.write_text(f"---\n{new_frontmatter}\n---\n{body}", encoding="utf-8")


def _append_activity_log(path: Path, body: str) -> None:
    text = path.read_text(encoding="utf-8")
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = f"\n- **{timestamp}** - {body.strip()}\n"
    if _ACTIVITY_HEADER in text:
        text = text.rstrip() + entry
    else:
        text = text.rstrip() + f"\n\n{_ACTIVITY_HEADER}\n{entry}"
    path.write_text(text, encoding="utf-8")
