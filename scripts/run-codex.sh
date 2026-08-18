#!/usr/bin/env bash
# Run Harbor's codex agent against a cvebench2tb task via the local Harbor conda
# environment and the repo-local OpenAI-compatible gateway config.
#
# Usage:
#   scripts/run-codex.sh <task-dir> [model] [extra harbor args...]
# Examples:
#   scripts/run-codex.sh cve-2024-3234-one-day qwen3.6 -n 1
#   scripts/run-codex.sh cve-2024-3234-one-day gpt-5 -n 1 --ak model_reasoning_effort=high
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=./lib-harbor-agent-env.sh
source "${SCRIPT_DIR}/lib-harbor-agent-env.sh"

TASK=${1:?"usage: run-codex.sh <task-dir> [model] [extra harbor args...]"}
MODEL=${2:-deepseek-v4-pro-0813}
shift 2 2>/dev/null || shift

cb2tb_require_harbor_bin
cb2tb_load_openai_env
cb2tb_route_openai_through_model_proxy

PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
exec "$(cb2tb_harbor_bin)" run \
  --path "$TASK" \
  --agent agents.tracing_agents:TracingCodex \
  --model "$MODEL" \
  --ae 'OPENAI_API_KEY=${OPENAI_API_KEY}' \
  --ae 'OPENAI_BASE_URL=${OPENAI_BASE_URL}' \
  "$@"
