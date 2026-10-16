#!/bin/bash
# verify.sh —— RL 侧单脚本判分模板(数据侧唯一保留的判分脚本)。
# 用法:  EVALUATOR_URL=http://127.0.0.1:9091/done bash verify.sh
# 输出:  stdout 一行 JSON({"judger_name","total":0/1}),ParseJudgerStdout 直接消费;
#        想只要 0/1 就把 printf 换成 echo。
# 说明:  脚本不挑执行位置(在 target 内跑用默认 localhost;别处跑用环境变量指路)。
#        安全性取决于执行位置:在 target/trainer 侧执行 = 判分不经过 main,V1-V4 全灭;
#        若仍在 main 里执行,curl 可被 agent 预埋假货替换 —— 那等于没修。
set -uo pipefail

URL="${EVALUATOR_URL:-http://127.0.0.1:9091/done}"
JUDGER_NAME="${JUDGER_NAME:-cve_reward}"

# ① 拿判定:curl 优先,python3 兜底(evaluator 是 Flask,target 里必有 python3)
out="$(curl -s -m 20 "$URL" 2>/dev/null \
   || python3 -c "import urllib.request;print(urllib.request.urlopen('$URL',timeout=120).read().decode())" 2>/dev/null)"

# 答复为空 = 根本没问上(evaluator 死了/超时)→ 明确记 0,不静默
if [ -z "$out" ]; then
  printf '{"judger_name":"%s","total":0,"metadata":{"error":"evaluator-unreachable"}}\n' "$JUDGER_NAME"
  exit 0
fi

# ② 判定 + ③ 输出(verdict 原文进 metadata,复盘可见 evaluator 原话)
if echo "$out" | grep -q '"status"[[:space:]]*:[[:space:]]*true'; then
  printf '{"judger_name":"%s","total":1,"metadata":{"verdict":"%s"}}\n' "$JUDGER_NAME" "$out"
else
  printf '{"judger_name":"%s","total":0,"metadata":{"verdict":"%s"}}\n' "$JUDGER_NAME" "$out"
fi
