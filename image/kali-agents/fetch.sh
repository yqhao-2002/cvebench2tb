#!/usr/bin/env bash
# Fetch the pinned standalone binaries into this build context.
# Versions + sha256 must match the values pinned in the sibling Dockerfile.
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "error: kali-agents currently pins Linux x86_64 CLI artifacts" >&2
  exit 1
fi
for tool in curl tar sha256sum; do
  command -v "${tool}" >/dev/null 2>&1 || {
    echo "error: required host command not found: ${tool}" >&2
    exit 1
  }
done

CLAUDE_VERSION="2.1.228"
CLAUDE_SHA256="d535985e6941a3eb00179ccd7f52ceb0c6623a0305a518ebc4e6514f84a94c99"

CODEX_VERSION="rust-v0.147.0"
CODEX_BIN_SHA256="cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40"

OPENCODE_VERSION="v1.18.18"
OPENCODE_BIN_SHA256="bb71f45b564f9234a97f54d6252a4a41d2f4388ae4b078918f691824cc3b3e54"

# claude-code: direct binary download.
curl -fsSL -o claude \
  "https://downloads.claude.ai/claude-code-releases/${CLAUDE_VERSION}/linux-x64/claude"
echo "${CLAUDE_SHA256}  claude" | sha256sum -c -

# codex: archive contains codex-x86_64-unknown-linux-musl (static-pie).
curl -fsSL -o codex.tar.gz \
  "https://github.com/openai/codex/releases/download/${CODEX_VERSION}/codex-x86_64-unknown-linux-musl.tar.gz"
tar xzf codex.tar.gz
mv codex-x86_64-unknown-linux-musl codex
rm codex.tar.gz
echo "${CODEX_BIN_SHA256}  codex" | sha256sum -c -

# opencode: archive contains a single `opencode` binary.
curl -fsSL -o opencode.tar.gz \
  "https://github.com/anomalyco/opencode/releases/download/${OPENCODE_VERSION}/opencode-linux-x64.tar.gz"
tar xzf opencode.tar.gz
rm opencode.tar.gz
echo "${OPENCODE_BIN_SHA256}  opencode" | sha256sum -c -

chmod +x claude codex opencode
echo "claude-code ${CLAUDE_VERSION}, codex ${CODEX_VERSION}, opencode ${OPENCODE_VERSION} ready."
echo "Build with:"
echo "  docker build --build-arg HTTP_PROXY=\$HTTP_PROXY --build-arg HTTPS_PROXY=\$HTTPS_PROXY -t cvebench/kali-agents:2.1.0 ."
echo "On the H cluster, route pip through the internal mirror:"
echo '  docker build --build-arg HTTP_PROXY=$HTTP_PROXY --build-arg HTTPS_PROXY=$HTTPS_PROXY \'
echo '    --build-arg PIP_INDEX_URL=http://mirrors.i.h.pjlab.org.cn/repository/pypi-proxy/simple/ \'
echo "    --build-arg PIP_TRUSTED_HOST=mirrors.i.h.pjlab.org.cn -t cvebench/kali-agents:2.1.0 ."
