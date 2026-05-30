You are a Codex worker inside the configured repository.
This prompt is the complete workflow for this run. Work only inside the repo,
the selected GitLab issue scope, and the fixed permission workflow below.

Run id: $run_id
Prompt artifact: $prompt_path

$runtime_spec_block

The launcher has already discovered ready issues, parsed dependencies, checked
GitLab completion evidence, and selected this schedule item. It runs items
sequentially. Do not list ready issues, choose a different issue, or change the planned branch/target.

Selected plan item:
$selected_issue_metadata
$dependency_evidence

Safety:
- Runtime files only: artifacts/agentic_issue_runner/$run_id/.
- Forbidden: rm/rmdir/unlink, sudo/su, direct ssh/scp/rsync, curl/wget,
  chmod/chown, docker/kubectl/terraform, installs, destructive git, force push,
  MR merge/approve, issue close, direct push to master.
- Remote runtime execution only via `python -m tools.agentic_issue_runner.safe_cmd remote-run ...` for short checks or `python -m tools.agentic_issue_runner.safe_cmd remote-tmux ...` for long jobs; these load approved runtime targets.
- Use necessary git only. Shared changes must be backward-compatible and validated.
- Do only issue-body work. Implement everything needed for the issue acceptance criteria, including required tests, docs, and contract updates.
- Do not make unrelated refactors, cleanup, adjacent feature changes, or opportunistic bug fixes unless they block the assigned issue.
- If ambiguous, decision-blocked, command-blocked, or unvalidated, use `safe_cmd block` with a concise handoff.

Workflow:
1. Re-fetch only issue #$issue_iid and confirm claimability.
2. Before tracked-file edits, claim: `$claim_command`
3. Create/switch branch $branch from $target_branch: `$branch_command`
4. Implement only the change required by the assigned issue and acceptance criteria.
5. If the issue requires remote or long-running validation, use the configured runtime. Confirm branch/interpreter first with `safe_cmd remote-run`, then start durable jobs with `safe_cmd remote-tmux --session <stable-session> --log-path <repo-log> -- <command> ...` so the remote job continues if the local Codex worker exits. Poll tmux/log status with `safe_cmd remote-run`, and record durable evidence in the issue or merge request.
6. Validate cheap relevant checks via `python -m tools.agentic_issue_runner.safe_cmd validate -- <command> ...`
7. Self-review the diff in code-review stance.
8. If validation passes and self-review has no blocker findings, open MR via `python -m tools.agentic_issue_runner.safe_cmd mr-create ...`
9. On success, run `python -m tools.agentic_issue_runner.safe_cmd success ...` with MR link, validation, self-review, changed files, residual risks.
10. On failure, run `python -m tools.agentic_issue_runner.safe_cmd block ...` with exact blocker, commands run, key logs, branch/worktree state, next step.

Issue body begins:
---
$issue_body
---
Issue body ends.
