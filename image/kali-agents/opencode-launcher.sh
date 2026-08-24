#!/bin/sh
# OpenCode launcher: routes the built-in `openai` provider over Chat Completions
# with reasoning replay, for any invocation (Harbor native agent, manual runs,
# repo wrappers). See docs/KALI-AGENTS-MIGRATION.md §12.
#
# Why: opencode's built-in openai provider is hard-wired to the Responses API
# and drops prior-turn reasoning on replay unless the server returns
# encrypted_content. OpenAI-compatible gateways return plain reasoning, so it
# is silently lost. Registering the provider through the bundled
# @ai-sdk/openai-compatible adapter switches the wire to /chat/completions and
# replays assistant reasoning as `reasoning_content`.
#
# Activation conditions (all must hold):
#   - OPENAI_BASE_URL is set (custom gateway)
#   - CB2TB_OPENCODE_PROTOCOL != "responses" (opt-out switch)
#   - --model=openai/<id> is requested (other providers untouched)
# Idempotent: rewrites the provider block only; merges into any existing
# ~/.config/opencode/opencode.json (repo wrappers may have written one with
# the same content — result is identical).
set -eu

REAL=/usr/local/bin/opencode.real

# --- opt-outs -----------------------------------------------------------------
if [ "${CB2TB_OPENCODE_PROTOCOL:-chat}" = "responses" ]; then
    exec "$REAL" "$@"
fi
: "${OPENAI_BASE_URL:=}"
[ -n "$OPENAI_BASE_URL" ] || exec "$REAL" "$@"
[ "${CB2TB_OPENCODE_OFFLINE:-1}" != "0" ] && {
    export OPENCODE_DISABLE_MODELS_FETCH=1 OPENCODE_PURE=1 \
        OPENCODE_DISABLE_AUTOUPDATE=1 DO_NOT_TRACK=1
}

# --- extract --model=openai/<id> ----------------------------------------------
MODEL_REF=""
for arg in "$@"; do
    case "$arg" in
        --model=openai/*) MODEL_REF=$arg ;;
    esac
done
[ -n "$MODEL_REF" ] || exec "$REAL" "$@"
MODEL_ID=${MODEL_REF#--model=openai/}

# Guard against host-global OOM assassinating opencode (74G virtual memory
# makes it the killer's top pick despite a small RSS).
echo -1000 > /proc/self/oom_score_adj 2>/dev/null || true

# --- merge provider block into ~/.config/opencode/opencode.json ---------------
CFG_DIR=${OPENCODE_CONFIG_DIR:-${HOME}/.config/opencode}
CFG="$CFG_DIR/opencode.json"
mkdir -p "$CFG_DIR"

API_KEY=${OPENAI_API_KEY:-dummy-key}
if [ -f "$CFG" ] && command -v python3 >/dev/null 2>&1; then
    python3 - "$CFG" "$OPENAI_BASE_URL" "$API_KEY" "$MODEL_ID" <<'PYEOF'
import json, sys

cfg_path, base_url, api_key, model_id = sys.argv[1:5]
try:
    cfg = json.load(open(cfg_path))
    if not isinstance(cfg, dict):
        cfg = {}
except Exception:
    cfg = {}

cfg.setdefault("provider", {})["openai-chat"] = {
    "npm": "@ai-sdk/openai-compatible",
    "name": "OpenAI-compatible (chat completions)",
    "options": {"baseURL": base_url, "apiKey": api_key},
    "models": {model_id: {}},
}
json.dump(cfg, open(cfg_path, "w"), indent=2)
PYEOF
else
    printf '{"provider":{"openai-chat":{"npm":"@ai-sdk/openai-compatible","name":"OpenAI-compatible (chat completions)","options":{"baseURL":"%s","apiKey":"%s"},"models":{"%s":{}}}}}' \
        "$OPENAI_BASE_URL" "$API_KEY" "$MODEL_ID" > "$CFG"
fi

# --- translate the model reference and exec ------------------------------------
set -- "$@"
NEW_ARGS=""
for arg in "$@"; do
    if [ "$arg" = "$MODEL_REF" ]; then
        NEW_ARGS="$NEW_ARGS --model=openai-chat/$MODEL_ID"
    else
        NEW_ARGS="$NEW_ARGS $(printf '%s' "$arg" | sed "s/'/'\\\\''/g;1s/^/'/;\$s/\$/'/")"
    fi
done
eval "set -- $NEW_ARGS"

exec "$REAL" "$@"
