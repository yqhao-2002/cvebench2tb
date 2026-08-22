#!/bin/bash
set -euo pipefail

# 应用 entrypoint：前台启动 PHP 内置 web server（:8080，webroot /var/www/html）
exec php -S 0.0.0.0:8080 -t /var/www/html
