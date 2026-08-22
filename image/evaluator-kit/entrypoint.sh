#!/bin/bash
# evaluator entrypoint：启动 Flask 判分服务（9091）
cd /evaluator
exec python3 evaluator.py
