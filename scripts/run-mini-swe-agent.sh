#!/usr/bin/env bash
# Run Harbor's offline mini-swe-agent against a cvebench2tb task via the local
# Harbor conda environment and the repo-local OpenAI-compatible gateway config.
#
# Usage:
#   scripts/run-mini-swe-agent.sh <task-dir> [model] [extra harbor args...]
# Examples:
#   scripts/run-mini-swe-agent.sh cve-2024-5315-one-day qwen3.6 -n 1
#   scripts/run-mini-swe-agent.sh cve-2024-5315-one-day qwen3.6 -n 1 --ak reasoning_effort=high
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=./lib-harbor-agent-env.sh
source "${SCRIPT_DIR}/lib-harbor-agent-env.sh"

TASK=${1:?"usage: run-mini-swe-agent.sh <task-dir> [model] [extra harbor args...]"}
MODEL_INPUT=${2:-deepseek-v4-pro-0813}
shift 2 2>/dev/null || shift

cb2tb_require_harbor_bin
cb2tb_load_openai_env
cb2tb_route_openai_through_model_proxy

MINI_STEP_LIMIT=${CB2TB_MINI_STEP_LIMIT:-150}

case "${MODEL_INPUT}" in
  openai/*)
    MODEL_SPEC="${MODEL_INPUT}"
    ;;
  */*)
    printf 'run-mini-swe-agent.sh expects a bare model id or openai/<model>; got: %s\n' "${MODEL_INPUT}" >&2
    exit 1
    ;;
  *)
    MODEL_SPEC="openai/${MODEL_INPUT}"
    ;;
esac

PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
exec "$(cb2tb_harbor_bin)" run \
  --path "$TASK" \
  --agent agents.tracing_agents:TracingOfflineMiniSweAgent \
  --model "$MODEL_SPEC" \
  --ak "config={\"agent\":{\"step_limit\":${MINI_STEP_LIMIT}}}" \
  --ae 'OPENAI_API_KEY=${OPENAI_API_KEY}' \
  --ae 'OPENAI_BASE_URL=${OPENAI_BASE_URL}' \
  "$@"
