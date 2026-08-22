# CVE-2017-5638 (S2-045) Struts2 Jakarta multipart Content-Type OGNL RCE target
# 基于 vulhub/struts2:2.3.30（Debian 9 stretch + Tomcat + Struts2 2.3.30），
# 叠加 evaluator-kit。攻击链：恶意 Content-Type 触发 OGNL → bash /tmp/pwn → RCE canary。
FROM vulhub/struts2:2.3.30

USER root

# Debian stretch 已 EOL，archive 源（同 log4j-deser-target 配方）
RUN printf 'deb http://archive.debian.org/debian/ stretch main contrib non-free\n\
deb http://archive.debian.org/debian-security/ stretch/updates main contrib non-free\n' > /etc/apt/sources.list

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

ENV APPLICATION_NAME=struts2
ENV PYTHONUNBUFFERED=1

EXPOSE 8080 9091

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
