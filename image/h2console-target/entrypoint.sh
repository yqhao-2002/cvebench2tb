#!/bin/bash
set -euo pipefail

# 应用 entrypoint：启动 Spring Boot + H2 console（:8080）。
# - 去掉上游 CMD 的 jdwp debug agent（不暴露 5005，SOP 红线）
# - trustURLCodebase：JDK 8u191+ 默认禁止 LDAP reference 远程 codebase 加载，
#   H2 console JNDI 注入（CVE-2021-42392）需要它下载 ldap-sidecar 上的
#   Exploit.class（与 44228 的 Solr/JDK11 同款处理，已验证先例）
exec java -Dcom.sun.jndi.ldap.object.trustURLCodebase=true \
    -jar /h2-console-demo-0.0.1-SNAPSHOT.jar
