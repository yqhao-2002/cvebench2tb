# cvebench2tb

CVE-Bench → Terminal-Bench 2 (Harbor) 双容器迁移。

- **86 个任务**（43 challenge × zero_day/one_day）位于仓库根目录，Harbor 任务格式（task.toml + instruction.md + environment/ + tests/ + solution/），可直接 `harbor run --path <task-dir> --agent ...`。
- **adapters/cvebench/**：批量改造脚本 `adapter.py`（`docker compose config` 展开 + 改写为 Harbor 形态）与批量体检脚本 `doctor.py`（9 步冒烟：基线 false → reward 0 → RCE canary → done true → reward 1，阶段归因、断点续跑）。
- **docs/CVEBENCH-TO-TERMINAL-BENCH-DESIGN.md**：迁移设计文档（架构、逐组件映射、12 条坑与对策、验证记录）。

## 架构

双容器：`main` = Kali agent 侧（cvebench/kali-large，Harbor 保留名，DNS 别名 `agent`）；`target` = 漏洞应用 + evaluator sidecar（cvebench/<cve>-target，:9090 应用 / :9091 判分）；外加一次性 `secrets_init`（断网派生 8 类 canary）与逐 case 的 db/wordpress 等 sidecar。verifier 在 main 里 `curl http://target:9091/done` 写 reward。

## 使用

```bash
# 跑一个任务(oracle 仅 cve-2024-2624 有官方 solution)
harbor run --path cve-2024-2624-one-day --agent oracle

# 重新生成全部任务
cd adapters/cvebench && python adapter.py

# 批量体检(断点续跑)
python doctor.py
```

## 镜像说明

- 原生 41 个 challenge 的 target 镜像：`docker.io/cvebench/*:2.1.0`（仅 cve-2021-44228-target 与 ldap-sidecar 不在册，需 `CVEBENCH_TAG=2.1.0 ./run build` 补建）。
- 用户自建 2 个（CNVD-2024-15077 / CVE-2024-39907）：`registry.h.pjlab.org.cn/ailab-safer2ai-safer2ai_cpu_task/cve-bench:<name>-target-2.1.0`，compose 中已改写。

### 自建镜像分发规则（约定）

凡是 docker.io 上没有的镜像（自建 case、补建 case），一律推送到 H 集群仓库，命名固定为：

```
registry.h.pjlab.org.cn/ailab-safer2ai-safer2ai_cpu_task/cve-bench:<challenge小写名>-target-<CVEBENCH_TAG>
```

流程：
```bash
cd /root/cve-bench && CVEBENCH_TAG=2.1.0 ./run build <CHALLENGE-ID>
NS=ailab-safer2ai-safer2ai_cpu_task
docker tag cvebench/<name>-target:2.1.0 registry.h.pjlab.org.cn/$NS/cve-bench:<name>-target-2.1.0
docker push registry.h.pjlab.org.cn/$NS/cve-bench:<name>-target-2.1.0
# 然后在 adapter 的 DEFAULT_IMAGE_MAP 加一行映射,重新生成任务即可
```

注意：
- **首次使用请先 `docker login registry.h.pjlab.org.cn`**（AK/SK，平台密钥管理申请）。
- **pull 需要权限**：仓库由 haoyuqi 持有「管理」权限，其他使用者需在平台「镜像仓库 → 共享管理」中被授予 pull，否则 compose up 会 401。
- 更新镜像建议换新 tag 并周知使用者，避免同名 tag 内容漂移。

## 验证状态（2026-08-04）

- adapter 全量生成 86/86；doctor 体检 22 案：14 PASS / 8 FAIL（全部 @up 拉镜像网络抖动，**0 转化失败**），续跑 `python doctor.py` 自动补齐。
- Harbor 官方闭环：`harbor run --path cve-2024-2624-one-day --agent oracle` → mean reward 1.000。
- 攻击实证：CVE-2024-2624（官方 solution）、CNVD-2024-15077（真 RCE）reward 0→1；sidecar 类型 mysql/mariadb:11.8/mariadb:10.6/自定义 server 均验证。
