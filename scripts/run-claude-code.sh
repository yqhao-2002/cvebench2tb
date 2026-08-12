#!/usr/bin/env bash
# Run harbor's claude-code agent against a cvebench2tb task via the internal
# OpenAI/Anthropic-compatible gateway (token.pjlab.org.cn).
#
# The gateway accepts both x-api-key and Bearer auth and serves the Anthropic
# Messages API at /v1/messages, so the stock claude-code CLI works as-is:
#   ANTHROPIC_BASE_URL=https://token.pjlab.org.cn  (CLI appends /v1/messages)
#   ANTHROPIC_API_KEY=<gateway token>              (sent as x-api-key)
#
# Usage:
#   scripts/run-claude-code.sh <task-dir> [model] [extra harbor args...]
# Examples:
#   scripts/run-claude-code.sh cve-2024-2624-one-day kimi-k3 -n 1
#   scripts/run-claude-code.sh cve-2024-2624-one-day glm-5.2 -n 1 -k 3 --ak max_turns=100
#
# Notes:
#   - Model name must be BARE (kimi-k3, not anthropic/kimi-k3): harbor keeps the
#     full name when ANTHROPIC_BASE_URL is custom and points sonnet/opus/haiku/
#     subagent aliases at it.
#   - The gateway enforces a cluster RPM limit; keep -n 1 for smoke tests.
#   - The API key ends up visible inside the task container (agent can read env).
#     Use a dedicated internal token, not a personal one.
#   - glm-5.2 reasoning can eat max_output_tokens; if output looks truncated set
#     CLAUDE_CODE_MAX_OUTPUT_TOKENS explicitly.
set -euo pipefail

TASK=${1:?"usage: run-claude-code.sh <task-dir> [model] [extra harbor args...]"}
MODEL=${2:-kimi-k3}
shift 2 2>/dev/null || shift

# Credentials: reuse the gateway token from the cve-bench .env.
# NOTE: do NOT inherit ambient ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY — shells run
# under Claude Code carry that session's own endpoint, and its URL + our token
# produces "401 Invalid token". Override via CC_BASE_URL/CC_API_KEY if needed.
set -a; source /root/cve-bench/.env; set +a
export ANTHROPIC_BASE_URL="${CC_BASE_URL:-https://token.pjlab.org.cn}"
export ANTHROPIC_API_KEY="${CC_API_KEY:-${OPENAI_API_KEY:?"missing gateway token (set CC_API_KEY or OPENAI_API_KEY in /root/cve-bench/.env)"}}"
unset ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN
unset ANTHROPIC_MODEL ANTHROPIC_SMALL_FAST_MODEL \
      ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL

exec /root/harbor/.venv/bin/harbor run --path "$TASK" --agent claude-code --model "$MODEL" "$@"
