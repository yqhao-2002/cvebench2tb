#!/bin/bash
set -euo pipefail

# 应用 entrypoint：前台启动 Flink JobManager（REST :8081）。
# 上游 docker-compose 传 command: jobmanager，此处等价固定。
exec /docker-entrypoint.sh jobmanager
