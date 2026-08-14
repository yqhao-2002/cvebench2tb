# kali-agents：四 agent 合一镜像迁移与交接

> 状态：**已完成并发布**。镜像已推送 registry（digest `sha256:c8723851…`）；4 agent 烟测通过；86 任务已重生成；代码已 pushall。

## 0. 接手人先看这里

交接时间：2026-08-14（Asia/Hong_Kong）。仓库：`/root/cvebench2tb`，分支：`master`，改造开始时 HEAD 为 `033cc99`。

### 当前结论

- 本地最终镜像已经存在并推送：`cvebench/kali-agents:2.1.0` → `registry.h.pjlab.org.cn/ailab-safer2ai-safer2ai_cpu_task/cvebench2tb:kali-agents-2.1.0`（digest `sha256:c8723851…`）；
- 四个 CLI、完整 mini-swe Python 环境、Harbor 预装复用均已断网验证；
- 86 个任务已全部改为 registry tag `kali-agents-2.1.0`，该 tag 已可被授权机器 pull；
- 4 agent（claude-code/codex/opencode/mini-swe-agent）在 `cve-2024-2624-one-day` + kimi-k3 上烟测全部「起来」（install 复用 + 进入 run + 调模型），详见 README「四 agent 烟测」节；
- 没有修改 `/root/harbor`；

### 工作区预期状态

- 91 个 tracked 文件被修改：README、adapter/template、设计文档，以及 86 个生成任务的 `environment/Dockerfile`；
- 6 个应提交的新源码/文档文件：
  - `agents/__init__.py`
  - `agents/offline_agents.py`
  - `docs/KALI-AGENTS-MIGRATION.md`
  - `image/kali-agents/Dockerfile`
  - `image/kali-agents/fetch.sh`
  - `image/kali-agents/requirements-mini-swe.lock`
- 3 个被 `.gitignore` 排除的大二进制：`image/kali-agents/{claude,codex,opencode}`；不要 `git add -f`。

接手后先运行：

```bash
cd /root/cvebench2tb
git status --short
git diff --check
docker image inspect cvebench/kali-agents:2.1.0 \
  --format 'id={{.Id}} size={{.Size}} created={{.Created}}'
```

### 发布（已完成 2026-08-14）

镜像已推送并验证：

```bash
NS=registry.h.pjlab.org.cn/ailab-safer2ai-safer2ai_cpu_task/cvebench2tb
docker push "$NS:kali-agents-2.1.0"          # digest sha256:c8723851…
docker manifest inspect "$NS:kali-agents-2.1.0" >/dev/null   # 已存在
```

## 1. 目标与约束

把 **claude-code / codex / opencode / mini-swe-agent** 四个 CLI 打包进同一个 Kali 镜像，继续服务于 cvebench2tb 的 Harbor 双容器任务。

约束：

- 不修改 Harbor 源码；
- 四个 CLI 必须实际存在于同一个任务镜像；
- Harbor setup 必须复用预装 CLI，不能在每次 trial 中重新 npm/uv 安装；
- 换一台 Linux x86_64 机器后，可从仓库脚本和 lock 文件完整重建；
- mini-swe-agent 不得覆盖 Kali 中由 Debian 管理的系统 Python 包。

## 2. Harbor 原生支持与“预装复用”的区别

当前 `/root/harbor`（0.20.0）原生注册了四个 agent，但 install 行为不同：

| agent | Harbor 原生可运行 | 原生 install 会复用预装 CLI | 本项目接入 |
|---|---:|---:|---|
| claude-code | 是 | 是，先检查 `command -v claude` | 原生 `claude-code` |
| codex | 是 | 是，先检查 `command -v codex` | 原生 `codex` |
| opencode | 是 | 否，每次执行 npm 安装 | `OfflineOpenCode` 薄子类 |
| mini-swe-agent | 是 | 否，每次执行 uv tool 安装 | `OfflineMiniSweAgent` 薄子类 |

`agents/offline_agents.py` 没有重新实现 agent，只覆盖两件事：

1. CLI 存在且版本匹配时跳过 install；
2. 用适合 standalone binary / 独立 venv 的命令探测版本。

run loop、模型配置、轨迹解析和 ATIF 输出全部继承 Harbor 原类。指定 `--agent-version` 时，薄子类会严格比较预装版本；不匹配才回退到 Harbor 原 install。

## 3. 仓库结构

```text
agents/
  offline_agents.py                 # opencode / mini-swe 预装复用薄子类
image/kali-agents/
  Dockerfile                        # all-in-one 镜像
  fetch.sh                          # 下载并校验三个 standalone CLI
  requirements-mini-swe.lock        # mini-swe 完整 Python 依赖闭包
```

三个下载产物 `image/kali-agents/{claude,codex,opencode}` 共约 750 MB，已加入 `.gitignore`，不能提交。其他机器先运行 `fetch.sh` 即可恢复构建上下文。

## 4. 固定版本

| CLI | 版本 | 校验 |
|---|---|---|
| claude | 2.1.228 | `d535985e6941a3eb00179ccd7f52ceb0c6623a0305a518ebc4e6514f84a94c99` |
| codex | rust-v0.147.0 / codex-cli 0.147.0 | `cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40` |
| opencode | v1.18.18 | `bb71f45b564f9234a97f54d6252a4a41d2f4388ae4b078918f691824cc3b3e54` |
| mini-swe-agent | 2.4.6 | Python 完整依赖版本锁见 `requirements-mini-swe.lock` |

`fetch.sh` 下载后逐一执行 sha256 校验；Dockerfile COPY 后逐一执行版本命令，任何失败都会终止构建。

## 5. mini-swe-agent Python 隔离

Kali 基础镜像的 Pygments、aiohttp、h11 等 Python 包由 Debian 管理。直接执行系统级 pip 安装时，新版 LiteLLM 依赖会尝试升级这些包，并因 Debian 包没有 pip RECORD 而失败。

最终方案：

- venv 固定在 `/opt/mini-swe-agent`；
- 完整安装 mini-swe-agent 声明的依赖，包括 datasets / pyarrow / numpy / pandas；
- `requirements-mini-swe.lock` 锁定已验证的完整依赖闭包；
- Docker 使用 `pip install --no-deps -r ...`，不会在未来构建时静默选择新版；
- 构建阶段强制执行 `pip check` 与 `mini-swe-agent --help`；
- `/usr/local/bin/mini-swe-agent` 链接到 venv 内 CLI。

选择完整依赖而非之前的最小化方案，是为了让 benchmark 子命令也能在其他机器完整运行，并使 `pip check` 真正通过。没有使用 Harbor runtime installer 中的 `litellm[proxy]` extra。

## 6. 跨机器构建

前提：Linux x86_64、Docker、curl、tar、sha256sum。基础镜像为 `cvebench/kali-large:2.1.0`。

普通公网环境：

```bash
cd image/kali-agents
./fetch.sh
docker build \
  --build-arg HTTP_PROXY="$HTTP_PROXY" \
  --build-arg HTTPS_PROXY="$HTTPS_PROXY" \
  -t cvebench/kali-agents:2.1.0 .
```

H 集群环境不要让 pip 经 `httpproxy-headless` 慢速访问公网 PyPI。根据 `/root/bench/003--vpn、vscode、代理仓库.pdf`，使用集群内部 PyPI proxy：

```bash
docker build \
  --build-arg HTTP_PROXY="$HTTP_PROXY" \
  --build-arg HTTPS_PROXY="$HTTPS_PROXY" \
  --build-arg PIP_INDEX_URL=http://mirrors.i.h.pjlab.org.cn/repository/pypi-proxy/simple/ \
  --build-arg PIP_TRUSTED_HOST=mirrors.i.h.pjlab.org.cn \
  -t cvebench/kali-agents:2.1.0 .
```

这些 PIP 参数在 Dockerfile 中是可选 ARG；不传时仍使用构建机器的正常 pip 配置，因此没有把 H 集群地址硬编码成公网构建的必需条件。

实测对比：公网代理下载 24.3 MB LiteLLM wheel 数分钟无进展；内部 mirror 响应约 5 ms，wheel 下载达到数百 MB/s。

## 7. 已完成验证

最终本地镜像：`cvebench/kali-agents:2.1.0`，image ID
`sha256:3de773f88704bea4cffa6f4ddc1671cd24d01a8721a7ee53b7124b3b247fec9d`，
本地 uncompressed size 约 8.40 GB。

在 `docker run --network none` 完全断网条件下已验证：

- `/usr/local/bin/{claude,codex,opencode,mini-swe-agent}` 全部存在；
- claude 2.1.228；
- codex-cli 0.147.0；
- opencode 1.18.18；
- mini-swe-agent 2.4.6；
- mini-swe-agent `pip check` 输出 `No broken requirements found`；
- lock 与镜像内 74 个非 pip Python 包逐项完全一致；
- datasets、LiteLLM、numpy、pandas、pyarrow 全部可 import；
- `mini-swe-agent --help` 可启动。

Harbor install guard 验证：

- ClaudeCode：只执行一次预装存在性检测；
- Codex：只执行一次预装存在性检测；
- OfflineOpenCode：指定版本时只执行存在性和版本检测；
- OfflineMiniSweAgent：指定版本时只执行存在性和版本检测；
- 四者均未触发 `npm install`、`uv tool install` 或安装用 curl；
- 自定义版本探测在断网镜像中分别得到 opencode 1.18.18、mini-swe-agent 2.4.6。

## 8. Harbor 运行方式

```bash
# Harbor 已原生实现预装复用
harbor run --path <task> --agent claude-code ...
harbor run --path <task> --agent codex ...

# Harbor 原类缺少预装复用，使用仓库薄子类
cd /path/to/cvebench2tb
PYTHONPATH="$PWD" harbor run --path <task> \
  --agent agents.offline_agents:OfflineOpenCode ...
PYTHONPATH="$PWD" harbor run --path <task> \
  --agent agents.offline_agents:OfflineMiniSweAgent ...
```

模型凭证和 base URL 仍按 Harbor 标准方式通过 `--ae NAME=value` 注入；这不属于镜像构建逻辑。

## 9. Adapter 与任务状态

- `adapters/cvebench/adapter.py` 的 `KALI_BASE_IMAGE` 已改为 `cvebench/kali-agents:2.1.0`；
- template Dockerfile 已切换到同一镜像；
- 86/86 个任务已用 registry 模式重生成；
- 86/86 个生成过程中的 Docker Compose 解析检查均为 ok；
- 生成后的任务引用：`registry.h.pjlab.org.cn/ailab-safer2ai-safer2ai_cpu_task/cvebench2tb:kali-agents-2.1.0`。

重生成命令：

```bash
cd adapters/cvebench
python adapter.py \
  --registry registry.h.pjlab.org.cn/ailab-safer2ai-safer2ai_cpu_task/cvebench2tb
```

## 10. 尚未执行的发布动作

本轮没有擅自修改远端状态。发布时需要：

```bash
NS=registry.h.pjlab.org.cn/ailab-safer2ai-safer2ai_cpu_task/cvebench2tb
docker tag cvebench/kali-agents:2.1.0 "$NS:kali-agents-2.1.0"
docker push "$NS:kali-agents-2.1.0"
```

随后再提交并推送本仓库。推送前应确认 registry 登录状态和目标仓库权限。

## 11. 常见故障快速定位

| 现象 | 原因 | 处理 |
|---|---|---|
| LiteLLM 24.3 MB wheel 数分钟无进展 | pip 正经 `httpproxy-headless` 访问公网 | H 集群构建时传 §6 的内部 `PIP_INDEX_URL` |
| `Cannot uninstall Pygments` / `no RECORD file` | 把 mini-swe 依赖装进了 Kali 系统 Python | 保持 `/opt/mini-swe-agent` venv，不改回系统 pip |
| OpenCode setup 执行 `npm i -g` | 使用了 Harbor 原生 `opencode` 短名 | 使用 `agents.offline_agents:OfflineOpenCode` |
| mini-swe setup 执行 `uv tool install` | 使用了 Harbor 原生 `mini-swe-agent` 短名 | 使用 `agents.offline_agents:OfflineMiniSweAgent` |
| `ModuleNotFoundError: agents` | Harbor 进程找不到本仓库 | 在仓库根执行并设置 `PYTHONPATH="$PWD"` |
| 任务启动时报 `manifest unknown` | registry 中新 tag 尚未发布 | 先完成 §0/§10 的镜像 push |
| mini-swe `--version` 报无此选项 | 2.4.6 CLI 本身没有该选项 | 用薄子类的 metadata 版本探测或 `/opt/mini-swe-agent/bin/python -c ...` |
