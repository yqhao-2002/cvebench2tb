#!/bin/bash

set -e

# 简化版框架 entrypoint（原版含 mongo/mysql 密码导出，本任务无 db sidecar）。
# 职责与上游一致：拉起应用 command + /evaluator/entrypoint.sh，双进程 wait。

if [[ $ATTACKER_ROLE == "administrator" ]]; then
    export ADMIN_PASSWORD=$DEFAULT_PASSWORD
else
    export ADMIN_PASSWORD=${ADMIN_PASSWORD:-$(cat /run/secrets/admin/admin_password)}
fi

exec "$@" &
APPLICATION_PID=$!

pushd /evaluator
./entrypoint.sh &
EVALUATOR_PID=$!
popd

wait $EVALUATOR_PID $APPLICATION_PID
