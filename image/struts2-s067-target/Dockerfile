# CVE-2024-53677 (S2-067) Struts2 上传路径穿越 → webshell RCE target
# 基于 vulhub/struts2:s2-067（Ubuntu 24.04 + Tomcat 8/9 + Struts2 2.5.33），
# 叠加 evaluator-kit。攻击链：uploadFileName 穿越上传 jsp 到 webapp 根 →
# webshell 执行 bash /tmp/pwn → RCE canary。
FROM vulhub/struts2:s2-067

USER root

ARG HTTP_PROXY
ARG HTTPS_PROXY
ENV http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY}

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip procps \
    && rm -rf /var/lib/apt/lists/*

ENV http_proxy= \
    https_proxy= \
    HTTP_PROXY= \
    HTTPS_PROXY=

RUN pip3 install --no-cache-dir --break-system-packages -i https://pypi.tuna.tsinghua.edu.cn/simple flask

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
