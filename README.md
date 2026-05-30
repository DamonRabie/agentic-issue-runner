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

This repository is a reusable kit, not a hosted service. It currently targets GitLab projects through `glab`.

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

## Worker Prompt

`tools/agentic_issue_runner/prompts/worker_system.md` is the worker prompt template used for launched agents. It is where project-specific agent rules belong in a real deployment: repository boundaries, tools the agent may use, validation expectations, remote runtime instructions, and handoff requirements.

Keep private project details out of a public fork. Put machine-specific runtime facts in ignored local config files and keep public prompt defaults generic.

## Requirements

- Python 3.11+
- `git`
- `glab` authenticated for your GitLab host
- `codex` available on `PATH`

## Configuration

By default the code uses harmless placeholders:

- GitLab host: `gitlab.example.com`
- GitLab project: `group/project`
- main branch: `main`
- branch prefix: `agent`

Create a local config file from the example:

```bash
cp .agent-library/agentic_issue_runner.example.toml .agent-library/agentic_issue_runner.toml
```

Then edit `.agent-library/agentic_issue_runner.toml` for your project. The real config path is ignored by git.

You can also override the GitLab target with environment variables:

```bash
AIR_GITLAB_HOST=gitlab.example.com AIR_GITLAB_PROJECT=group/project python -m tools.agentic_issue_runner.cli --dry-run
```

## Dry Run

Run a scheduler preview before launching workers:

```bash
python -m tools.agentic_issue_runner.cli --dry-run --max-issues 3
```

## Run Workers

Launch fixed-permission workers:

```bash
python -m tools.agentic_issue_runner.cli --run --max-issues 1
```

The runner expects the main worktree to be clean and on the configured main branch before a live run.

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
- `.agent-library/agentic_issue_runner.toml`
- `artifacts/`
- `.DS_Store`
