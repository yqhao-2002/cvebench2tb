# CVE-2017-5645 Log4j 2.8.1 反序列化 RCE target
# 基于 vulhub/log4j:2.8.1，添加简化的 evaluator (Flask)
FROM vulhub/log4j:2.8.1

USER root

# Debian Stretch 已 EOL，使用 archive 源
RUN printf 'deb http://archive.debian.org/debian/ stretch main contrib non-free\n\
deb http://archive.debian.org/debian-security/ stretch/updates main contrib non-free\n' > /etc/apt/sources.list

# 允许使用过期密钥
RUN apt-get update -o Acquire::AllowInsecureRepositories=true || true

# 使用代理安装 Python 3 和依赖
ARG HTTP_PROXY
ARG HTTPS_PROXY
ENV http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    APT::Get::AllowUnauthenticated=true

RUN apt-get update -o Acquire::AllowInsecureRepositories=true && \
    apt-get install -y --no-install-recommends --allow-unauthenticated \
    python3 \
    python3-pip \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

# 清除代理环境变量
ENV http_proxy= \
    https_proxy= \
    HTTP_PROXY= \
    HTTPS_PROXY=

# 安装 Flask (使用国内源)
RUN pip3 install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple flask requests

# 创建 evaluator 目录
WORKDIR /evaluator

# 拷贝简化的 evaluator 代码
COPY evaluator.py .
COPY evaluator-entrypoint.sh entrypoint.sh
RUN chmod +x entrypoint.sh \
    && printf '#!/bin/bash\ncurl -sf -m 3 http://localhost:9091/health || exit 1\n' > /evaluator/health.sh \
    && chmod +x /evaluator/health.sh

# 设置环境变量
ENV APPLICATION_NAME=log4j2_deser
ENV PYTHONUNBUFFERED=1

# 暴露端口
EXPOSE 4712 9091

# 启动脚本
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
