"""Thin `glab` subprocess adapter for GitLab issue access."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Iterable

from tools.agentic_issue_runner.constants import PROJECT_HOST, PROJECT_PATH, PROJECT_REMOTE, PROXY_ENV_KEYS, READY_LABEL
from tools.agentic_issue_runner.dependency_parser import parse_dependency_sections
from tools.agentic_issue_runner.models import CommandResult, IssueRecord, MergeRequestRecord
from tools.logger import get_logger


logger = get_logger("[agentic-issue-runner]")


def without_proxy_env(env: dict[str, str] | None = None) -> dict[str, str]:
    cleaned = dict(os.environ if env is None else env)
    for key in PROXY_ENV_KEYS:
        cleaned.pop(key, None)
    return cleaned


def summarize_command(command: Iterable[str]) -> str:
    """Render a command for logs without dumping long issue/MR bodies."""
    redacted_next = {"-m", "--message", "--description", "--body"}
    rendered: list[str] = []
    skip_value = False
    for part in command:
        if skip_value:
            rendered.append("<redacted>")
            skip_value = False
            continue
        rendered.append(part)
        if part in redacted_next:
            skip_value = True
    return " ".join(rendered)


class GlabAdapter:
    def __init__(self, repo_root: Path, *, dry_run: bool = False) -> None:
        self.repo_root = repo_root
        self.dry_run = dry_run
        self.calls: list[tuple[str, ...]] = []

    def run(self, args: Iterable[str]) -> CommandResult:
        command = tuple(args)
        self.calls.append(command)
        logger.info("Running GitLab command: %s", summarize_command(command))
        completed = subprocess.run(
            list(command),
            cwd=self.repo_root,
            env=without_proxy_env(),
            check=False,
            capture_output=True,
            text=True,
        )
        logger.info("GitLab command finished rc=%s: %s", completed.returncode, summarize_command(command))
        return CommandResult(command, completed.returncode, completed.stdout, completed.stderr)

    def verify_access(self) -> None:
        for args in (
            ("glab", "auth", "status", "--hostname", PROJECT_HOST),
            ("glab", "repo", "view", "-R", PROJECT_REMOTE),
        ):
            result = self.run(args)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or f"command failed: {' '.join(args)}")

    def list_ready_issue_iids(self) -> list[int]:
        result = self.run(
            (
                "glab",
                "issue",
                "list",
                "-R",
                PROJECT_REMOTE,
                "-l",
                READY_LABEL,
                "--output",
                "json",
            )
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "failed to list ready issues")
        payload = json.loads(result.stdout or "[]")
        return [int(item["iid"]) for item in payload]

    def get_issue(self, iid: int) -> IssueRecord:
        result = self.run(
            (
                "glab",
                "issue",
                "view",
                str(iid),
                "-R",
                PROJECT_REMOTE,
                "--output",
                "json",
            )
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"failed to fetch issue {iid}")
        return issue_from_glab_json(json.loads(result.stdout or "{}"))

    def list_ready_issues(self) -> list[IssueRecord]:
        return [self.get_issue(iid) for iid in self.list_ready_issue_iids()]

    def list_open_merge_requests(self) -> list[MergeRequestRecord]:
        result = self.run(
            (
                "glab",
                "mr",
                "list",
                "-R",
                PROJECT_REMOTE,
                "--output",
                "json",
            )
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "failed to list open merge requests")
        payload = json.loads(result.stdout or "[]")
        return [mr_from_glab_json(item) for item in payload]

    def list_merged_merge_requests(self) -> list[MergeRequestRecord]:
        result = self.run(
            (
                "glab",
                "mr",
                "list",
                "-R",
                PROJECT_REMOTE,
                "--merged",
                "--output",
                "json",
            )
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "failed to list merged merge requests")
        payload = json.loads(result.stdout or "[]")
        return [mr_from_glab_json(item) for item in payload]

    def update_issue_labels(
        self,
        iid: int,
        *,
        add_labels: tuple[str, ...] = (),
        remove_labels: tuple[str, ...] = (),
    ) -> CommandResult:
        args = ["glab", "issue", "update", str(iid), "-R", PROJECT_REMOTE]
        for label in add_labels:
            args.extend(["--label", label])
        for label in remove_labels:
            args.extend(["--unlabel", label])
        result = self.run(tuple(args))
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"failed to update issue {iid} labels")
        return result

    def note_issue(self, iid: int, body: str) -> CommandResult:
        result = self.run(("glab", "issue", "note", str(iid), "-R", PROJECT_REMOTE, "-m", body))
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"failed to note issue {iid}")
        return result


def _labels_from_payload(payload: dict) -> frozenset[str]:
    raw = payload.get("labels") or []
    labels: list[str] = []
    for item in raw:
        if isinstance(item, str):
            labels.append(item)
        elif isinstance(item, dict) and item.get("name"):
            labels.append(str(item["name"]))
    return frozenset(labels)


def issue_from_glab_json(payload: dict) -> IssueRecord:
    description = str(payload.get("description") or "")
    deps = parse_dependency_sections(description)
    return IssueRecord(
        iid=int(payload.get("iid") or payload.get("id")),
        title=str(payload.get("title") or ""),
        description=description,
        labels=_labels_from_payload(payload),
        state=str(payload.get("state") or "opened"),
        created_at=payload.get("created_at") or payload.get("createdAt"),
        blocked_by=frozenset(deps.blocked_by),
        blocks=frozenset(deps.blocks),
    )


def mr_from_glab_json(payload: dict) -> MergeRequestRecord:
    source_branch = payload.get("source_branch") or payload.get("sourceBranch") or ""
    target_branch = payload.get("target_branch") or payload.get("targetBranch") or ""
    web_url = payload.get("web_url") or payload.get("webUrl") or payload.get("url") or ""
    issue_iid = payload.get("issue_iid") or payload.get("issueIid")
    return MergeRequestRecord(
        iid=int(payload.get("iid") or payload.get("id")),
        title=str(payload.get("title") or ""),
        source_branch=str(source_branch),
        target_branch=str(target_branch),
        state=str(payload.get("state") or "opened"),
        web_url=str(web_url),
        description=str(payload.get("description") or ""),
        issue_iid=int(issue_iid) if issue_iid is not None else None,
    )
