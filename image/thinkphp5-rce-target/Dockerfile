# ThinkPHP 5.0.20 核心路由 RCE target（2018 年 12 月经典 RCE，无 CVE 编号）
# 基于 vulhub/thinkphp:5.0.20（Debian 10 buster + OpenJDK 8），
# 叠加 evaluator-kit。攻击链：User-Agent 携带 zerodiumsystem(...) 单请求 RCE。
FROM vulhub/thinkphp:5.0.20

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
RUN chmod +x entrypoint.sh
COPY health.sh /evaluator/health.sh
RUN chmod +x /evaluator/health.sh

ENV APPLICATION_NAME=thinkphp
ENV PYTHONUNBUFFERED=1

EXPOSE 8080 9091

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
