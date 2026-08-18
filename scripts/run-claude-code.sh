#!/usr/bin/env bash
# Run Harbor's claude-code agent against a cvebench2tb task via the local
# Harbor conda environment and the repo-local Anthropic-compatible gateway
# config.
#
# The gateway serves the Anthropic Messages API at /v1/messages, so the stock
# claude-code CLI works as-is:
#   ANTHROPIC_BASE_URL=http://<gateway-host>:26667  (CLI appends /v1/messages)
#   ANTHROPIC_API_KEY=<gateway token>               (sent as x-api-key)
#
# Usage:
#   scripts/run-claude-code.sh <task-dir> [model] [extra harbor args...]
# Examples:
#   scripts/run-claude-code.sh cve-2024-2624-one-day kimi-k3 -n 1
#   scripts/run-claude-code.sh cve-2024-2624-one-day glm-5.2 -n 1 -k 3 --ak max_turns=100
#
# Notes:
#   - Model name must be BARE (kimi-k3, not anthropic/kimi-k3): Harbor keeps
#     the full name when ANTHROPIC_BASE_URL is custom and points
#     sonnet/opus/haiku/subagent aliases at it.
#   - The gateway enforces a cluster RPM limit; keep -n 1 for smoke tests.
#   - The API key ends up visible inside the task container (agent can read env).
#     Use a dedicated internal token, not a personal one.
#   - glm-5.2 reasoning can eat max_output_tokens; if output looks truncated set
#     CLAUDE_CODE_MAX_OUTPUT_TOKENS explicitly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=./lib-harbor-agent-env.sh
source "${SCRIPT_DIR}/lib-harbor-agent-env.sh"

TASK=${1:?"usage: run-claude-code.sh <task-dir> [model] [extra harbor args...]"}
MODEL=${2:-deepseek-v4-pro-0813}
shift 2 2>/dev/null || shift

cb2tb_require_harbor_bin
cb2tb_load_claude_env
cb2tb_route_claude_through_model_proxy

CLAUDE_MAX_TURNS=${CB2TB_CLAUDE_MAX_TURNS:-150}

PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
exec "$(cb2tb_harbor_bin)" run \
  --path "$TASK" \
  --agent agents.tracing_agents:TracingClaudeCode \
  --model "$MODEL" \
  --ak "max_turns=${CLAUDE_MAX_TURNS}" \
  "$@"
