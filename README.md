# Agentic Issue Runner

Agentic Issue Runner is a GitLab-first workflow kit for turning a rough idea into agent-ready work, then running Codex workers against that work with fixed permissions, explicit labels, dependency-aware scheduling, and durable handoff notes.

The planning flow is based on Matt Pocock's AI Hero / `mattpocock/skills` workflow: use `grill-me` to pressure-test the problem, `to-prd` to write the resolved context down, and `to-issues` to break the PRD into small vertical implementation slices. This repository adds a GitLab execution layer around that planning workflow.

The kit is designed for repositories where humans keep control of scope and review, while agents handle bounded implementation work:

- shapes ambiguous work through reusable skills before implementation starts
- turns the plan into a PRD and then dependency-aware issues
- discovers GitLab issues marked with a configured ready label
- parses explicit `Blocked by` / `Blocks` issue sections
- plans issue order from live issue and merge request evidence
- launches one Codex worker per selected issue
- routes sensitive Git, GitLab, validation, and remote-runtime commands through `safe_cmd`
- writes prompt, claim, success, block, and worker-output artifacts under `artifacts/agentic_issue_runner/`

## Status

This repository is a reusable kit, not a hosted service. It targets GitLab projects through `glab`, or local markdown issues for solo / pre-tracker projects.

## Workflow

1. Use `grill-me` to design the problem. The goal is to resolve the decision tree before writing implementation tasks.
2. Use `to-prd` to capture the agreed context, problem, solution, user stories, implementation decisions, testing decisions, and out-of-scope boundaries.
3. Use `to-issues` to split the PRD into small, dependency-aware issue slices. Agents generally do better with narrow batches than broad open-ended instructions.
4. Publish or update the issues in GitLab, using explicit dependency sections and workflow labels.
5. Run the issue runner. It reads GitLab state, chooses claimable issues, writes a worker prompt, and launches Codex with a constrained command policy.
6. Review the merge request and handoff notes produced by the agent.

## Included Skills

The `.agent-library/skills/` directory includes the planning skills used before execution:

- `grill-me`: interrogates a plan until the important product and technical decisions are explicit.
- `to-prd`: turns the resolved conversation and repo context into a PRD.
- `to-issues`: breaks a PRD or plan into small vertical issue slices with dependencies.

These skills are intentionally separate from the runner. They make the work clear; the runner makes execution traceable.

## Project Customization Surface

Everything in `tools/agentic_issue_runner/project/` is the per-project customization surface. The `.py` modules in `tools/agentic_issue_runner/` are reusable across projects and should not be edited per-project.

```text
tools/agentic_issue_runner/
├── project/
│   ├── project.toml        # tracked — edit in place to configure your project
│   └── worker_system.md    # tracked — edit in place to encode project conventions
└── *.py                    # reusable runner logic — don't fork these per project
```

Both files are tracked: fork the repo (or copy `tools/agentic_issue_runner/` into your project) and commit your edits like any other source. The split keeps the runner generic while letting each consuming repo own its config, label vocabulary, and prompt voice.

## Requirements

- Python 3.11+
- `git`
- `glab` authenticated for your GitLab host (only for `mode = "glab"`)
- `codex` or `claude` available on `PATH` (pick one per run via `--worker-cli`)

## Configuration

### 1. Edit your project config

The runner loads `tools/agentic_issue_runner/project/project.toml` by default. The shipped file is a working starter — open it and change the values to fit your project.

If you'd rather keep the shipped file untouched and point the runner at a config elsewhere on disk:

```bash
AIR_CONFIG=/path/to/my.toml python -m tools.agentic_issue_runner.cli --dry-run
```

Individual values can also be overridden via env vars:

```bash
AIR_GITLAB_HOST=git.example.com AIR_GITLAB_PROJECT=group/project AIR_MODE=glab \
    python -m tools.agentic_issue_runner.cli --dry-run
```

### 2. Pick a runner mode

`[runner].mode` is the most important switch. Two modes are supported:

| mode      | issue source                                | when to use                                                  |
|-----------|---------------------------------------------|--------------------------------------------------------------|
| `"glab"`  | GitLab issues via `glab` CLI                | real team workflow with MRs, labels, dependency sections     |
| `"local"` | markdown files under `[local].issues_dir`   | solo projects, prototypes, repos without GitLab              |

In `local` mode the runner reads `ISSUE-NNN-*.md` files whose frontmatter looks like:

```yaml
---
title: Add greeting module
status: open                # open | in-progress | done | blocked
blocked_by: [ISSUE-001]     # or [] / "None"
---
```

State transitions (`status:` rewrites plus an `## Activity log` append) are owned by `safe_cmd`; do not edit them by hand.

### 3. Sample config keys

`project.toml` ships with every supported key set to a sensible default. The most-edited sections in practice:

- `[runner].mode` — `"glab"` or `"local"`
- `[runner].main_branch` — usually `"main"` or `"master"`
- `[runner].branch_prefix` — prefix used for agent branches (`agent/123-...`)
- `[runner].remote_python_module_prefixes` — module prefixes `safe_cmd remote-run` will accept (project-specific)
- `[local].issues_dir` — where local markdown issues live (e.g. `"docs/issues"` or `"artifacts/issues"`)
- `[gitlab].host` / `[gitlab].project_path` — required for `mode = "glab"`
- `[labels].*` — your project's label vocabulary; rename only if your tracker uses different names

## Worker Prompt

`tools/agentic_issue_runner/project/worker_system.md` is the worker prompt template used for launched agents. Edit it in your fork to encode project-specific rules: repository boundaries, tools the agent may use, validation expectations, remote runtime instructions, handoff requirements, and stack-specific conventions.

The template uses `$worker_label` so a single file serves both Codex and Claude Code workers; the rest is CLI-agnostic. Keep private project details out of a public fork — secrets and machine-specific runtime facts belong in ignored local files (e.g. `.agent-library/runtime_specs/*.local.toml`), not in the tracked prompt.

## Dry Run

Run a scheduler preview before launching workers:

```bash
python -m tools.agentic_issue_runner.cli --dry-run --max-issues 3
```

## Run Workers

Launch fixed-permission workers:

```bash
# Codex (default)
python -m tools.agentic_issue_runner.cli --run --max-issues 1

# Claude Code
python -m tools.agentic_issue_runner.cli --run --max-issues 1 --worker-cli claude
```

The runner expects the main worktree to be clean and on the configured main branch before a live run. In `glab` mode it also fetches origin and fast-forwards; in `local` mode those steps are skipped.

Each worker CLI has a parallel rules-file preflight that must pass before launch:

- Codex: `.codex/rules/overnight-agent.rules` (validated with `codex execpolicy check`)
- Claude: `.claude/rules/overnight-agent.json` (presence-gated; Claude has no execpolicy binary)

## Remote Runtime Specs

Remote runtimes are optional. Put machine-specific runtime definitions in ignored local files under:

```text
.agent-library/runtime_specs/*.local.toml
```

The runner will load those specs for guarded `safe_cmd remote-run` and `safe_cmd remote-tmux` commands.

## Credits

The planning workflow and included skill structure are adapted from Matt Pocock's MIT-licensed `mattpocock/skills` project and AI Hero skill guides. Agentic Issue Runner focuses on the GitLab/glab execution layer, fixed-permission worker launch, dependency-aware scheduling, and transparent issue/MR handoffs.

## License

MIT. See `LICENSE`.

## Publication Safety

Do not commit local operator state, generated artifacts, or machine-specific runtime specs. The root `.gitignore` excludes the common sensitive surfaces:

- `.idea/`
- `.claude/settings.local.json`
- `.agent-library/runtime_specs/*.local.toml`
- `artifacts/` (runner prompts, claims, success/block notes, worker output)
- `.DS_Store`
