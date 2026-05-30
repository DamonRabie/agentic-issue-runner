---
name: commit
description: Use when the user asks to commit changes to git, create one or more commits, choose commit messages, apply the repo's gitmoji commit convention, or cleanly finish the current reviewed worktree into one or more well-explained commits.
origin: Local
---

# Commit

Commit the reviewed worktree cleanly and leave no ambiguous leftovers.

## Workflow

1. Run `git status --short` and inspect the changed files.
2. Review the relevant diffs before staging so the commit plan matches the actual changes.
3. Check whether the reviewed changes materially update durable project knowledge that future agents should know, such as architecture, workflows, routing, feature phases, operating constraints, or repo-specific conventions.
4. If the answer is yes, update the project's durable memory or instruction file before any commit. Prefer the canonical source file when instruction files are symlinks or generated mirrors. Preserve the existing format, tone, and section structure; keep the edit concise; record only stable guidance or milestones rather than transient implementation chatter.
5. Partition the worktree into one or more coherent commits. Split by behavior or purpose, not by file count.
6. If the user asks to "clean the worktree", plan to finish all reviewed remaining changes in this run, usually as multiple commits when the leftovers are unrelated.
7. Decide the full commit plan up front. If the remaining files are unclear, mixed with unrelated user edits, or not safe to commit, stop before making any commit and ask.
8. Stage only the files for the current commit. Prefer narrow pathspecs over broad staging when unrelated leftovers exist.
9. If the wrong file is staged, unstage it and fix the boundary before committing.
10. Commit with the repo convention:

```text
:git_moji: TYPE: Commit description
```

Examples of `TYPE`:

- `feat` for new capability
- `fix` for a bug fix
- `chore` for maintenance, wiring, dependencies, or cleanup
- `docs` for documentation-only changes
- `refactor` for behavior-preserving restructuring
- `test` for test-only changes

11. Continue committing the remaining reviewed file groups until `git status --short` is clean for the agreed scope.

## Memory Update Rules

- Treat a memory update as required when the diff adds or changes stable project knowledge:
  - new architectural patterns or service boundaries
  - new canonical routes, entry points, or workflow shapes
  - completed phases or major capability milestones
  - repo-specific constraints or conventions future agents should follow
- Skip memory edits for purely local fixes, refactors with no workflow impact, test-only changes, formatting churn, or temporary debugging work.
- Prefer editing the source file behind repo instruction or memory files:
  - if an instruction file is a symlink, edit the symlink target
  - if one file is documented as generated from another memory file, edit the source file instead of the generated mirror
  - do not break, replace, or remove the link while updating memory
- Keep memory concise. Extend existing sections when possible instead of creating sprawling new sections.
- Preserve the document's current structure and naming unless the repo already uses a different memory format.
- Avoid logging unstable details such as one-off bug fixes, temporary commands, or short-lived branch context.

## Message Rules

- Pick the gitmoji and type that best fit the staged change.
- Use a clear, concise subject.
- Use imperative mood where practical.
- Do not end the subject with a period.
- Always add a second `-m` body when the commit touches multiple files, non-trivial logic, migrations, config, or user-visible behavior.
- Make the second `-m` informative rather than generic. Explain what changed, why it changed, and which files or areas carry the important parts.
- Mention the key files in the body with one short line per file or file group when that improves scanability.
- Keep the body specific to the staged diff. Do not repeat boilerplate.

Preferred body shape:

```text
Why:
- <reason for the change>

What:
- <file or area>: <change and impact>
- <file or area>: <change and impact>
```

Example:

```text
git commit -m ":bug: fix: validate artifact schema" -m "Why:
- catch malformed artifacts before downstream training starts

What:
- data builder: write the manifest fields required by downstream consumers
- tests: cover missing-column and checksum mismatch failures"
```

## Guardrails

- Do not commit before handling any required memory update.
- Default to finishing the reviewed worktree in this run. Do not stop after the first commit if reviewed changes still remain.
- Do not stage unrelated user changes unless the user explicitly asked to commit everything.
- Do not rewrite, reset, or discard changes to make staging easier.
- If the user asked to clean the worktree, prefer separate commits over one mixed commit when that keeps unrelated changes understandable.
- If one leftover file or deletion is outside the main change set but is still clearly safe to commit, commit it separately rather than leaving the tree dirty.
- If the worktree contains pre-existing unrelated changes, do not start committing around them blindly. Either isolate a safe reviewed subset and explain what remains, or ask before proceeding.
- If the user asked to commit the current work and the remaining reviewed changes can be grouped safely, create as many commits as needed so the worktree is clean at the end.
- If no files are changed or staged, stop and report that there is nothing to commit.

## Validation

- If a memory update may be needed, inspect repo instruction files and any linked source file before staging to determine the correct edit target.
- After a memory edit, review that diff as part of the commit plan and confirm the link or generated-file relationship still holds.
- After staging, run `git diff --cached --stat`.
- If the staged diff is ambiguous, inspect `git diff --cached`.
- If the staged diff includes files outside the intended commit boundary, unstage them before committing.
- After each commit, run `git status --short` again to confirm what remains.
- Before finishing, verify whether the worktree is clean. If it is not clean, explain exactly which files remain and why they were not committed.
- After committing, report the short commit hash, subject, body summary, and files included.
