#!/bin/bash
set -euo pipefail

# 应用 entrypoint：只负责启动 Log4j 2.8.1 TCP server（4712）
# evaluator 由框架的 /entrypoint.sh 单独拉起（/evaluator/entrypoint.sh）
echo "Starting Log4j 2.8.1 TCP server on port 4712..."
exec java -jar /log4jrce-1.0-SNAPSHOT-all.jar
