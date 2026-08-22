#!/bin/bash
set -euo pipefail

# 应用 entrypoint：前台启动 Apache + PHP（ThinkPHP 5.0.20 demo，:8080）
exec docker-php-entrypoint apache2-foreground
