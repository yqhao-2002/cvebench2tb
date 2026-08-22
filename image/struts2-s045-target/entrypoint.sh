#!/bin/bash
set -euo pipefail

# 应用 entrypoint：前台启动 Maven Jetty（Struts2 2.3.30 showcase，:8080）。
# ~/.m2 仓库已预置，运行期无网络依赖。
cd /usr/src
exec mvn jetty:run
