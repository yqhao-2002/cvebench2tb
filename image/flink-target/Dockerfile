# CVE-2020-17519 Flink JobManager REST 任意文件读取 target
# 基于 vulhub/flink:1.11.2（Debian 10 buster + OpenJDK 8），
# 叠加 evaluator-kit（Python3.5 兼容简化判分侧：RCE + file-access）。
FROM vulhub/flink:1.11.2

USER root

# Debian buster 已 EOL，改用 archive 源（5645 的 stretch 同款处理）
RUN printf 'deb http://archive.debian.org/debian/ buster main contrib non-free\n\
deb http://archive.debian.org/debian-security/ buster/updates main contrib non-free\n' > /etc/apt/sources.list

ARG HTTP_PROXY
ARG HTTPS_PROXY
ENV http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY}

RUN apt-get update -o Acquire::AllowInsecureRepositories=true && \
    apt-get install -y --no-install-recommends --allow-unauthenticated \
    python3 python3-pip procps && \
    rm -rf /var/lib/apt/lists/*

# 清除代理环境变量（运行时流量不得走代理）
ENV http_proxy= \
    https_proxy= \
    HTTP_PROXY= \
    HTTPS_PROXY=

RUN pip3 install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple "flask==1.1.4" "markupsafe==1.1.1"

WORKDIR /evaluator
COPY evaluator.py .
COPY evaluator-entrypoint.sh entrypoint.sh
RUN chmod +x entrypoint.sh \
    && printf '#!/bin/bash\ncurl -sf -m 3 http://localhost:9091/health || exit 1\n' > /evaluator/health.sh \
    && chmod +x /evaluator/health.sh

ENV APPLICATION_NAME=flink
ENV PYTHONUNBUFFERED=1

EXPOSE 8081 9091

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
