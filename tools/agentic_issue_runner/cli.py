"""CLI entrypoint for the Agentic Issue Runner."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from tools.agentic_issue_runner.dry_run import render_dry_run
from tools.agentic_issue_runner.git_planner import execute_startup_gate
from tools.agentic_issue_runner.glab_adapter import make_adapter
from tools.agentic_issue_runner.constants import MAIN_BRANCH, RUNNER_MODE
from tools.agentic_issue_runner.runner import PromptRunConfig, build_plan_with_remote_evidence, run_prompt_rounds
from tools.logger import get_logger

_ROOT = Path(__file__).resolve().parents[2]
logger = get_logger("[agentic-issue-runner]")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch fixed-permission Codex or Claude Code workers for tracker issues.")
    parser.add_argument("--dry-run", action="store_true", help="print the scheduler plan without launching workers")
    parser.add_argument("--run", action="store_true", help="launch one worker per selected issue sequentially")
    parser.add_argument("--run-id", help="stable run id for comments and prompt artifacts")
    parser.add_argument("--issue", type=int, help="ask the scheduler to select only this issue IID")
    parser.add_argument("--max-issues", type=int, default=1, help="cap attempted issue count")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=0.0,
        help="per-worker timeout; 0 disables the worker timeout",
    )
    parser.add_argument(
        "--skip-access-check",
        action="store_true",
        help="skip adapter access verification, useful for mocked tests",
    )
    parser.add_argument(
        "--worker-cli",
        choices=("codex", "claude"),
        default="codex",
        help="worker CLI to launch (default: codex)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="model id for the claude worker (only valid with --worker-cli=claude; default claude-sonnet-4-6)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.dry_run and args.run:
        parser.error("choose only one of --dry-run or --run")
    if not args.dry_run and not args.run:
        parser.error("choose --dry-run or --run")
    if args.max_issues < 1:
        parser.error("--max-issues must be at least 1")
    if args.model is not None and args.worker_cli != "claude":
        parser.error("--model is only valid with --worker-cli=claude")
    if args.worker_cli == "claude" and args.model is None:
        args.model = "claude-sonnet-4-6"

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logger.info(
        "Starting agentic issue runner mode=%s run_id=%s max_issues=%d issue=%s worker_cli=%s model=%s",
        "dry-run" if args.dry_run else "run",
        run_id,
        args.max_issues,
        args.issue or "auto",
        args.worker_cli,
        args.model or "n/a",
    )

    if args.run:
        if RUNNER_MODE == "local":
            logger.info("Checking startup gate: clean worktree, current %s (local mode, skipping fetch/merge).", MAIN_BRANCH)
        else:
            logger.info("Checking startup gate: clean worktree, current %s, fetch origin, ff-only merge.", MAIN_BRANCH)
        gate = execute_startup_gate(_ROOT)
        if not gate.ok:
            logger.info("Startup gate failed: %s", gate.reason)
            print(f"Startup gate failed: {gate.reason}")
            return 2
        logger.info("Startup gate passed.")

    adapter = make_adapter(_ROOT, dry_run=args.dry_run)
    if args.run and not args.skip_access_check:
        if RUNNER_MODE == "local":
            logger.info("Verifying local issues directory.")
        else:
            logger.info("Verifying GitLab access for unattended run.")
        adapter.verify_access()
        logger.info("Access check passed.")
    logger.info("Building scheduler plan with remote issue and MR evidence.")
    plan = build_plan_with_remote_evidence(adapter, issue_iid=args.issue, max_issues=args.max_issues)
    logger.info(
        "Scheduler plan built: selected=%d blocked=%d skipped=%d.",
        len(plan.selected),
        len(plan.blocked),
        len(plan.skipped),
    )
    if args.dry_run:
        print(render_dry_run(plan, max_issues=args.max_issues))
        return 0

    logger.info("Launching prompt rounds.")
    result = run_prompt_rounds(
        PromptRunConfig(
            repo_root=_ROOT,
            run_id=run_id,
            max_issues=args.max_issues,
            issue_iid=args.issue,
            timeout_seconds=args.timeout_seconds,
            worker_cli=args.worker_cli,
            model=args.model,
        ),
        plan=plan,
        adapter=adapter,
    )
    print(f"Attempted issues: {result.attempted}")
    print(f"Blocked by launcher: {result.blocked}")
    for path in result.prompt_paths:
        print(f"Prompt: {path.relative_to(_ROOT)}")
    for path in result.worker_output_paths:
        print(f"Worker output: {path.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
