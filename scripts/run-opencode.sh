#!/usr/bin/env bash
# Run Harbor's offline OpenCode agent against a cvebench2tb task via the local
# Harbor conda environment and the repo-local OpenAI-compatible gateway config.
#
# Usage:
#   scripts/run-opencode.sh <task-dir> [model] [extra harbor args...]
# Examples:
#   scripts/run-opencode.sh cve-2024-34716-one-day deepseek-v4-pro-0813 -n 1
#   scripts/run-opencode.sh cve-2024-34716-one-day deepseek-v4-pro-0813 -n 1 --ak variant=fast
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=./lib-harbor-agent-env.sh
source "${SCRIPT_DIR}/lib-harbor-agent-env.sh"

TASK=${1:?"usage: run-opencode.sh <task-dir> [model] [extra harbor args...]"}
MODEL_INPUT=${2:-deepseek-v4-pro-0813}
shift 2 2>/dev/null || shift

cb2tb_require_harbor_bin
cb2tb_load_openai_env
cb2tb_route_openai_through_model_proxy

OPENCODE_MAX_STEPS=${CB2TB_OPENCODE_MAX_STEPS:-150}

case "${MODEL_INPUT}" in
  */*)
    MODEL_SPEC="${MODEL_INPUT}"
    ;;
  *)
    MODEL_SPEC="openai/${MODEL_INPUT}"
    ;;
esac

PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
exec "$(cb2tb_harbor_bin)" run \
  --path "$TASK" \
  --agent agents.tracing_agents:TracingOfflineOpenCode \
  --model "$MODEL_SPEC" \
  --ak "opencode_config={\"agent\":{\"build\":{\"steps\":${OPENCODE_MAX_STEPS}},\"plan\":{\"steps\":${OPENCODE_MAX_STEPS}},\"general\":{\"steps\":${OPENCODE_MAX_STEPS}},\"explore\":{\"steps\":${OPENCODE_MAX_STEPS}}}}" \
  --ae 'OPENAI_API_KEY=${OPENAI_API_KEY}' \
  --ae 'OPENAI_BASE_URL=${OPENAI_BASE_URL}' \
  "$@"
