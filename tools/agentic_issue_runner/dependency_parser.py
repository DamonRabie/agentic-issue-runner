"""Parse explicit dependency sections from GitLab issue descriptions."""

from __future__ import annotations

import re

from tools.agentic_issue_runner.models import DependencySections

_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_ISSUE_REF_RE = re.compile(r"(?<![!])#(\d+)\b")


def _normalize_heading(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _section_bodies(markdown: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in markdown.splitlines():
        match = _HEADER_RE.match(line)
        if match:
            heading = _normalize_heading(match.group(2))
            current = heading if heading in {"blocked by", "blocks"} else None
            if current:
                sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return sections


def _issue_refs(section_lines: list[str]) -> tuple[int, ...]:
    text = "\n".join(section_lines).strip()
    if not text or text.lower() == "none":
        return ()
    refs = {int(match.group(1)) for match in _ISSUE_REF_RE.finditer(text)}
    return tuple(sorted(refs))


def parse_dependency_sections(markdown: str | None) -> DependencySections:
    """Return dependencies from explicit markdown dependency sections only."""
    sections = _section_bodies(markdown or "")
    return DependencySections(
        blocked_by=_issue_refs(sections.get("blocked by", [])),
        blocks=_issue_refs(sections.get("blocks", [])),
    )
