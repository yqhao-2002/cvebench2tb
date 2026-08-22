#!/bin/bash
set -euo pipefail

# 应用 entrypoint：前台启动 Tomcat（Struts2 demo app :8080）
exec /usr/local/tomcat/bin/catalina.sh run
