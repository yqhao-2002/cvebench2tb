#!/bin/bash
# preload-images.sh — 从共享缓存把 cve-bench 所需镜像装入本地 docker。
#
# 适用: 无外网 / docker.io 不可达的机器(如集群 worker、隔离开发机)。
# 原理: docker 解析镜像"先本地后远端"——只要同名 tag 在本地,Dockerfile FROM
#       和 compose image: 都会直接命中,任务文件零修改。
#
# 用法:
#   bash scripts/preload-images.sh [缓存根目录]
#   # 默认 /mnt/shared-storage-gpfs2/trustcyberdata/public/forEval/forExploit/CVEBench
set -uo pipefail

CACHE=${1:-/mnt/shared-storage-gpfs2/trustcyberdata/public/forEval/forExploit/CVEBench}
TAG=2.1.0
fail=0

echo "缓存目录: $CACHE"
[ -d "$CACHE" ] || { echo "ERROR: 缓存目录不存在(未挂载 gpfs2?)"; exit 1; }

# --- 1. kali-large(agent 侧基底,rootfs 格式 → docker import) ---
if docker image inspect "cvebench/kali-large:$TAG" >/dev/null 2>&1; then
    echo "SKIP  cvebench/kali-large:$TAG (本地已有)"
elif [ -s "$CACHE/dockers/kali-large.tar" ]; then
    echo "IMPORT cvebench/kali-large:$TAG (7.2GB,需要几分钟) ..."
    docker import "$CACHE/dockers/kali-large.tar" "cvebench/kali-large:$TAG" || fail=1
else
    echo "MISS  dockers/kali-large.tar 不存在于缓存"
    fail=1
fi

# --- 2. target/sidecar 镜像合集(docker save 格式 → docker load) ---
if [ -s "$CACHE/code/cvebench-images.tar" ]; then
    echo "LOAD  cvebench-images.tar ..."
    docker load -i "$CACHE/code/cvebench-images.tar" || fail=1
else
    echo "MISS  code/cvebench-images.tar 为空或不存在"
    echo "      需先在拥有全量镜像的机器上执行:"
    echo "      docker save \$(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^cvebench/') -o cvebench-images.tar"
    echo "      再放到 $CACHE/code/ 下"
    fail=1
fi

# --- 3. 核对: 仓库任务引用的镜像是否都已就位 ---
echo
echo "== 核对任务镜像可得性 =="
repo_root=$(cd "$(dirname "$0")/.." && pwd)
images=$( { grep -h '^    image:' "$repo_root"/*/environment/docker-compose.yaml 2>/dev/null | awk '{print $2}';
            grep -h '^FROM ' "$repo_root"/*/environment/Dockerfile 2>/dev/null | awk '{print $2}'; } | sort -u )
missing=()
public=()
while read -r img; do
    [ -z "$img" ] && continue
    case "$img" in
        cvebench/*|registry.h.pjlab.org.cn/*)
            docker image inspect "$img" >/dev/null 2>&1 || missing+=("$img");;
        *)
            public+=("$img");;   # ubuntu/mysql/mariadb/mailhog 等公开镜像
    esac
done <<< "$images"

if [ ${#missing[@]} -eq 0 ]; then
    echo "OK    cvebench/registry 镜像全部在本地"
else
    echo "STILL MISSING(需 pull 或补齐缓存):"
    printf '  %s\n' "${missing[@]}"
    fail=1
fi
[ ${#public[@]} -gt 0 ] && echo "公开镜像(首次 up 时按需自动拉取): $(printf '%s ' "${public[@]}" | sort -u | tr '\n' ' ')"

echo
[ $fail -eq 0 ] && echo "✅ 预载完成,可离线运行任务" || echo "⚠️  有缺失项,见上方 MISS/STILL MISSING"
exit $fail
