"""Constants shared by the agentic issue runner helpers."""

from __future__ import annotations

from tools.agentic_issue_runner.config import load_config

CONFIG = load_config()

PROJECT_HOST = CONFIG.gitlab.host
PROJECT_PATH = CONFIG.gitlab.project_path
PROJECT_REMOTE = f"{PROJECT_HOST}/{PROJECT_PATH}"
MAIN_BRANCH = CONFIG.runner.main_branch
AGENT_BRANCH_PREFIX = CONFIG.runner.branch_prefix
REMOTE_PYTHON_MODULE_PREFIXES = CONFIG.runner.remote_python_module_prefixes

READY_LABEL = CONFIG.labels.ready
AGENT_IN_PROGRESS_LABEL = CONFIG.labels.in_progress
AGENT_MR_OPENED_LABEL = CONFIG.labels.mr_opened
AGENT_NEEDS_REVIEW_LABEL = CONFIG.labels.needs_review
AGENT_BLOCKED_LABEL = CONFIG.labels.blocked
COMPLETED_LABEL = CONFIG.labels.completed
AGENT_MANAGED_LABELS = CONFIG.labels.managed

LOCAL_ONLY_LABEL = CONFIG.labels.local_only
EXPENSIVE_COMPUTE_LABEL = CONFIG.labels.expensive_compute
LONG_RUNNING_LABEL = CONFIG.labels.long_running
NEEDS_EXTERNAL_SERVICE_LABEL = CONFIG.labels.external_service
RESOURCE_LABELS = CONFIG.labels.resource
HEAVY_RESOURCE_LABELS = CONFIG.labels.heavy_resource

HIGH_PRIORITY_LABELS = frozenset(CONFIG.labels.high_priority)

FORBIDDEN_CLOSING_KEYWORDS = ("closes", "fixes", "resolves")

RUNNER_MODE = CONFIG.runner.mode
LOCAL_ISSUES_DIR = CONFIG.local.issues_dir
