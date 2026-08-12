#!/usr/bin/env bash
# Fetch the pinned claude-code standalone binary into this build context.
# Version + sha256 must match the values pinned in the sibling Dockerfile.
set -euo pipefail
cd "$(dirname "$0")"

VERSION="2.1.228"
SHA256="d535985e6941a3eb00179ccd7f52ceb0c6623a0305a518ebc4e6514f84a94c99"

curl -fsSL -o claude "https://downloads.claude.ai/claude-code-releases/${VERSION}/linux-x64/claude"
echo "${SHA256}  claude" | sha256sum -c -
chmod +x claude
echo "claude-code ${VERSION} ready; build with:"
echo "  docker build --build-arg HTTP_PROXY=\$HTTP_PROXY --build-arg HTTPS_PROXY=\$HTTPS_PROXY -t cvebench/kali-claude:2.1.0 ."
