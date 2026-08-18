from __future__ import annotations

import base64
import copy
import json
import os
import shlex
import uuid
from pathlib import Path
from typing import Any

from harbor.agents.installed.base import NonZeroAgentExitCodeError, with_prompt_template
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.agents.installed.codex import Codex
from harbor.agents.installed.mini_swe_agent import MiniSweAgent
from harbor.agents.installed.opencode import OpenCode
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths
from harbor.utils.env import parse_bool_env_value

from agents.offline_agents import OfflineMiniSweAgent, OfflineOpenCode


_CAPTURE_DIRNAME = "http_capture"
_PROXY_TRAJECTORY_FILENAME = "proxy_trajectory.json"


def _record_body_json(record: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("forwarded_body_json", "body_json"):
        body = record.get(key)
        if isinstance(body, dict):
            return body
    return None


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_safe_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "content", "value", "input_text", "output_text", "arguments"):
            if key in value:
                text = _safe_text(value.get(key))
                if text:
                    return text
        value_type = str(value.get("type") or "").lower()
        if value_type in {"input_text", "output_text", "text"} and "text" in value:
            return _safe_text(value.get("text"))
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _message_like_system_entries(body: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def add_entry(role: Any, content: Any, source: str, index: int) -> None:
        normalized_role = str(role or "").strip().lower()
        if normalized_role not in {"system", "developer"}:
            return
        entries.append(
            {
                "role": normalized_role,
                "content": content,
                "text": _safe_text(content),
                "source": source,
                "index": index,
            }
        )

    messages = body.get("messages")
    if isinstance(messages, list):
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            add_entry(message.get("role"), message.get("content"), "messages", index)

    model_input = body.get("input")
    if isinstance(model_input, list):
        for index, item in enumerate(model_input):
            if not isinstance(item, dict):
                continue
            add_entry(item.get("role"), item.get("content"), "input", index)

    return entries


def _extract_system_prompt(record: dict[str, Any]) -> dict[str, Any]:
    body = _record_body_json(record)
    if not isinstance(body, dict):
        return {
            "schema_version": 1,
            "found": False,
            "source": None,
            "sequence": record.get("sequence"),
            "path": record.get("path"),
            "text": None,
            "raw": None,
        }

    if "system" in body:
        raw = body.get("system")
        text = _safe_text(raw)
        return {
            "schema_version": 1,
            "found": bool(text or raw is not None),
            "source": "system",
            "sequence": record.get("sequence"),
            "path": record.get("path"),
            "text": text or None,
            "raw": raw,
        }

    if "instructions" in body:
        raw = body.get("instructions")
        text = _safe_text(raw)
        return {
            "schema_version": 1,
            "found": bool(text or raw is not None),
            "source": "instructions",
            "sequence": record.get("sequence"),
            "path": record.get("path"),
            "text": text or None,
            "raw": raw,
        }

    entries = _message_like_system_entries(body)
    text = "\n\n".join(entry["text"] for entry in entries if entry.get("text"))
    return {
        "schema_version": 1,
        "found": bool(entries),
        "source": "messages" if entries else None,
        "sequence": record.get("sequence"),
        "path": record.get("path"),
        "text": text or None,
        "raw": entries or None,
    }


def _extract_tool_definitions(record: dict[str, Any]) -> dict[str, Any]:
    body = _record_body_json(record)
    tools: list[Any] = []
    source: str | None = None

    if isinstance(body, dict):
        if isinstance(body.get("tools"), list):
            tools = body.get("tools") or []
            source = "tools"
        elif isinstance(body.get("functions"), list):
            tools = body.get("functions") or []
            source = "functions"

    return {
        "schema_version": 1,
        "found": bool(tools),
        "source": source,
        "sequence": record.get("sequence"),
        "path": record.get("path"),
        "tools": tools,
    }


def _captured_model_name_matches(record: dict[str, Any], model_name: str | None) -> bool:
    if not model_name:
        return False
    body = _record_body_json(record)
    if not isinstance(body, dict):
        return False
    request_model = body.get("model")
    if not isinstance(request_model, str) or not request_model.strip():
        return False
    normalized = model_name.split("/", 1)[-1]
    return request_model == model_name or request_model == normalized


def _select_primary_request(
    requests: list[dict[str, Any]],
    *,
    model_name: str | None,
) -> dict[str, Any]:
    if not requests:
        return {}

    def score(record: dict[str, Any]) -> tuple[int, int]:
        tool_info = _extract_tool_definitions(record)
        system_info = _extract_system_prompt(record)
        value = 0
        if _captured_model_name_matches(record, model_name):
            value += 100
        if tool_info.get("found"):
            value += 50
        if system_info.get("found"):
            value += 25
        return value, -int(record.get("sequence") or 0)

    return max(requests, key=score)


class HttpCaptureMixin:
    def __init__(self, *args, capture_http: bool | str = False, **kwargs):
        self._capture_http = parse_bool_env_value(
            capture_http,
            name="capture_http",
            default=False,
        )
        super().__init__(*args, **kwargs)

    @property
    def _capture_script_path(self) -> Path:
        return Path(__file__).with_name("http_capture_proxy.py")

    def _capture_artifact_dir(self) -> Path:
        return self.logs_dir / _CAPTURE_DIRNAME

    async def _start_capture_proxy(
        self,
        environment: BaseEnvironment,
        *,
        upstream_base_url: str,
    ) -> dict[str, Any]:
        remote_root = f"/tmp/cvebench2tb-http-capture-{uuid.uuid4().hex}"
        remote_script = f"{remote_root}/http_capture_proxy.py"
        remote_ready = f"{remote_root}/ready.json"
        remote_stdout = f"{remote_root}/proxy.stdout.log"
        remote_stderr = f"{remote_root}/proxy.stderr.log"

        await self.exec_as_agent(
            environment,
            command=f"mkdir -p {shlex.quote(remote_root)}",
        )
        await environment.upload_file(self._capture_script_path, remote_script)

        start_command = (
            "python3 "
            f"{shlex.quote(remote_script)} "
            "--host 127.0.0.1 "
            "--port 0 "
            f"--upstream {shlex.quote(upstream_base_url)} "
            f"--log-dir {shlex.quote(remote_root)} "
            f"--ready-file {shlex.quote(remote_ready)} "
            f"> {shlex.quote(remote_stdout)} "
            f"2> {shlex.quote(remote_stderr)} "
            "& echo $!"
        )
        start_result = await self.exec_as_agent(
            environment,
            command=f"sh -lc {shlex.quote(start_command)}",
        )
        pid = (start_result.stdout or "").strip().splitlines()
        fallback_pid = pid[-1] if pid else ""

        ready_info: dict[str, Any] | None = None
        for _ in range(50):
            ready_result = await self.exec_as_agent(
                environment,
                command=(
                    "sh -lc "
                    + shlex.quote(
                        f"test -s {shlex.quote(remote_ready)} && cat {shlex.quote(remote_ready)} || true"
                    )
                ),
                timeout_sec=5,
            )
            text = (ready_result.stdout or "").strip()
            if text:
                ready_info = json.loads(text)
                break

        if ready_info is None:
            stderr_result = await self.exec_as_agent(
                environment,
                command=(
                    "sh -lc "
                    + shlex.quote(f"cat {shlex.quote(remote_stderr)} 2>/dev/null || true")
                ),
                timeout_sec=5,
            )
            error = (stderr_result.stdout or stderr_result.stderr or "").strip()
            raise RuntimeError(error or "HTTP capture proxy did not become ready")

        port = ready_info.get("port")
        if not isinstance(port, int):
            raise RuntimeError(f"invalid proxy ready payload: {ready_info!r}")

        return {
            "pid": str(ready_info.get("pid") or fallback_pid or ""),
            "remote_root": remote_root,
            "local_base_url": f"http://127.0.0.1:{port}",
            "upstream_base_url": upstream_base_url,
        }

    async def _stop_capture_proxy(
        self,
        environment: BaseEnvironment,
        proxy_info: dict[str, Any],
    ) -> None:
        pid = str(proxy_info.get("pid") or "").strip()
        if not pid:
            return
        await self.exec_as_agent(
            environment,
            command=f"sh -lc {shlex.quote(f'kill {shlex.quote(pid)} 2>/dev/null || true')}",
            timeout_sec=10,
        )

    async def _copy_remote_file(
        self,
        environment: BaseEnvironment,
        source_path: str,
        destination: Path,
    ) -> None:
        result = await self.exec_as_agent(
            environment,
            command=(
                "python3 -c "
                + shlex.quote(
                    "import base64, pathlib, sys; "
                    "sys.stdout.write(base64.b64encode(pathlib.Path(sys.argv[1]).read_bytes()).decode())"
                )
                + " "
                + shlex.quote(source_path)
            ),
            timeout_sec=60,
        )
        encoded = (result.stdout or "").strip()
        if not encoded:
            destination.write_text("", encoding="utf-8")
            return
        data = base64.b64decode(encoded)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.write_text(data.decode("utf-8"), encoding="utf-8")
        except UnicodeDecodeError:
            destination.write_bytes(data)

    async def _copy_capture_artifacts(
        self,
        environment: BaseEnvironment,
        proxy_info: dict[str, Any],
    ) -> list[str]:
        remote_root = str(proxy_info.get("remote_root") or "")
        if not remote_root:
            return []

        find_result = await self.exec_as_agent(
            environment,
            command=(
                "sh -lc "
                + shlex.quote(f"find {shlex.quote(remote_root)} -type f -print 2>/dev/null || true")
            ),
            timeout_sec=30,
        )
        copied: list[str] = []
        for source_path in [
            line.strip()
            for line in (find_result.stdout or "").splitlines()
            if line.strip()
        ]:
            relative = source_path.removeprefix(remote_root).lstrip("/")
            if not relative:
                continue
            destination = self._capture_artifact_dir() / relative
            await self._copy_remote_file(environment, source_path, destination)
            copied.append(str(destination.relative_to(self.logs_dir)))

        await self.exec_as_agent(
            environment,
            command=f"rm -rf {shlex.quote(remote_root)}",
            timeout_sec=30,
        )
        return copied

    def _write_capture_summary(
        self,
        *,
        instruction: str,
        proxy_info: dict[str, Any],
        copied_artifacts: list[str],
    ) -> None:
        capture_dir = self._capture_artifact_dir()
        requests_path = capture_dir / "api_requests.jsonl"
        responses_path = capture_dir / "api_responses.jsonl"
        requests = self._read_jsonl(requests_path)
        responses = self._read_jsonl(responses_path)
        primary_request = _select_primary_request(requests, model_name=self.model_name)
        system_prompt = _extract_system_prompt(primary_request)
        tool_definitions = _extract_tool_definitions(primary_request)

        response_by_sequence = {
            response.get("sequence"): response
            for response in responses
            if isinstance(response, dict) and response.get("sequence") is not None
        }
        sequences = []
        seen: set[Any] = set()
        for request in requests:
            sequence = request.get("sequence")
            if sequence not in seen:
                sequences.append(sequence)
                seen.add(sequence)
        for response in responses:
            sequence = response.get("sequence")
            if sequence not in seen:
                sequences.append(sequence)
                seen.add(sequence)

        trajectory = {
            "schema_version": 1,
            "capture_mode": "reverse_proxy",
            "agent_class": f"{self.__class__.__module__}:{self.__class__.__name__}",
            "agent_name": self.name(),
            "model_name": self.model_name,
            "instruction": instruction,
            "proxy": {
                "upstream_base_url": proxy_info.get("upstream_base_url"),
                "local_base_url": proxy_info.get("local_base_url"),
            },
            "artifacts": {
                "capture_dir": _CAPTURE_DIRNAME,
                "copied_files": copied_artifacts,
                "request_log_path": (
                    str(requests_path.relative_to(self.logs_dir))
                    if requests_path.exists()
                    else None
                ),
                "response_log_path": (
                    str(responses_path.relative_to(self.logs_dir))
                    if responses_path.exists()
                    else None
                ),
                "harbor_trajectory_path": (
                    "trajectory.json"
                    if (self.logs_dir / "trajectory.json").exists()
                    else None
                ),
            },
            "summary": {
                "request_count": len(requests),
                "response_count": len(responses),
                "system_prompt_found": system_prompt.get("found", False),
                "tool_count": len(tool_definitions.get("tools") or []),
                "primary_request_sequence": primary_request.get("sequence"),
            },
            "system_prompt": system_prompt,
            "tool_definitions": tool_definitions,
            "exchanges": [
                {
                    "sequence": sequence,
                    "request": next(
                        (
                            request
                            for request in requests
                            if isinstance(request, dict)
                            and request.get("sequence") == sequence
                        ),
                        None,
                    ),
                    "response": response_by_sequence.get(sequence),
                }
                for sequence in sequences
            ],
        }
        self._write_json(self.logs_dir / _PROXY_TRAJECTORY_FILENAME, trajectory)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
        return records

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

    async def _run_with_capture(
        self,
        environment: BaseEnvironment,
        *,
        instruction: str,
        upstream_base_url: str,
        env_updates: dict[str, str] | None = None,
        runner: Any,
    ) -> None:
        if not self._capture_http:
            await runner()
            return

        proxy_info = await self._start_capture_proxy(
            environment,
            upstream_base_url=upstream_base_url,
        )
        copied_artifacts: list[str] = []
        original_extra_env = dict(self._extra_env)
        if env_updates:
            self._extra_env.update(env_updates)
        try:
            await runner()
        finally:
            try:
                await self._stop_capture_proxy(environment, proxy_info)
            finally:
                try:
                    copied_artifacts = await self._copy_capture_artifacts(
                        environment, proxy_info
                    )
                finally:
                    self._extra_env = original_extra_env
            self._write_capture_summary(
                instruction=instruction,
                proxy_info=proxy_info,
                copied_artifacts=copied_artifacts,
            )


class TracingClaudeCode(HttpCaptureMixin, ClaudeCode):
    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if self._capture_http and self._is_bedrock_mode():
            raise RuntimeError("capture_http is not supported with Claude Bedrock mode")

        async def _run_impl() -> None:
            use_bedrock = self._is_bedrock_mode()
            force_oauth = self._should_force_oauth()
            oauth_token = (self._get_env("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()

            if force_oauth and not oauth_token:
                raise RuntimeError(
                    "CLAUDE_FORCE_OAUTH is set but CLAUDE_CODE_OAUTH_TOKEN is not. "
                    "Run `claude setup-token` to get one, or unset CLAUDE_FORCE_OAUTH."
                )

            if force_oauth:
                api_key = ""
            else:
                api_key = (
                    self._get_env("ANTHROPIC_API_KEY")
                    or self._get_env("ANTHROPIC_AUTH_TOKEN")
                    or ""
                )
                if api_key and oauth_token:
                    self.logger.debug(
                        "API key and OAuth token both set; using the API key "
                        "(set CLAUDE_FORCE_OAUTH=1 to use the subscription)."
                    )

            env = {
                "ANTHROPIC_API_KEY": api_key,
                "ANTHROPIC_BASE_URL": self._get_env("ANTHROPIC_BASE_URL"),
                "CLAUDE_CODE_OAUTH_TOKEN": oauth_token,
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": self._get_env(
                    "CLAUDE_CODE_MAX_OUTPUT_TOKENS"
                ),
                "FORCE_AUTO_BACKGROUND_TASKS": "1",
                "ENABLE_BACKGROUND_TASKS": "1",
            }

            if use_bedrock:
                env["CLAUDE_CODE_USE_BEDROCK"] = "1"
                bedrock_token = self._get_env("AWS_BEARER_TOKEN_BEDROCK") or ""
                if bedrock_token:
                    env["AWS_BEARER_TOKEN_BEDROCK"] = bedrock_token

                for aws_var in (
                    "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY",
                    "AWS_SESSION_TOKEN",
                    "AWS_PROFILE",
                ):
                    value = self._get_env(aws_var) or ""
                    if value:
                        env[aws_var] = value

                env["AWS_REGION"] = self._get_env("AWS_REGION") or "us-east-1"
                small_model_region = self._get_env(
                    "ANTHROPIC_SMALL_FAST_MODEL_AWS_REGION"
                ) or ""
                if small_model_region:
                    env["ANTHROPIC_SMALL_FAST_MODEL_AWS_REGION"] = small_model_region
                if (self._get_env("DISABLE_PROMPT_CACHING") or "").strip() == "1":
                    env["DISABLE_PROMPT_CACHING"] = "1"

            env = {key: value for key, value in env.items() if value}

            if self.model_name:
                if use_bedrock:
                    if "/" in self.model_name:
                        env["ANTHROPIC_MODEL"] = self.model_name.split("/", 1)[-1]
                    else:
                        env["ANTHROPIC_MODEL"] = self.model_name
                elif "ANTHROPIC_BASE_URL" in env:
                    env["ANTHROPIC_MODEL"] = self.model_name
                else:
                    env["ANTHROPIC_MODEL"] = self.model_name.split("/")[-1]
            elif self._get_env("ANTHROPIC_MODEL"):
                env["ANTHROPIC_MODEL"] = self._get_env("ANTHROPIC_MODEL") or ""

            if "ANTHROPIC_BASE_URL" in env and "ANTHROPIC_MODEL" in env:
                env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = env["ANTHROPIC_MODEL"]
                env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = env["ANTHROPIC_MODEL"]
                env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = env["ANTHROPIC_MODEL"]
                env["CLAUDE_CODE_SUBAGENT_MODEL"] = env["ANTHROPIC_MODEL"]

            if (
                self._get_env("CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING") or ""
            ).strip() == "1":
                env["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"] = "1"

            env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
            env["IS_SANDBOX"] = "1"
            env.update(self._resolved_env_vars)
            env["CLAUDE_CONFIG_DIR"] = (
                EnvironmentPaths.agent_dir / "sessions"
            ).as_posix()

            setup_command = (
                "mkdir -p $CLAUDE_CONFIG_DIR/debug $CLAUDE_CONFIG_DIR/projects/-app "
                "$CLAUDE_CONFIG_DIR/shell-snapshots $CLAUDE_CONFIG_DIR/statsig "
                "$CLAUDE_CONFIG_DIR/todos $CLAUDE_CONFIG_DIR/skills && "
                "if [ -d ~/.claude/skills ]; then "
                "cp -r ~/.claude/skills/. $CLAUDE_CONFIG_DIR/skills/ 2>/dev/null || true; "
                "fi"
            )

            skills_command = self._build_register_skills_command()
            if skills_command:
                setup_command += f" && {skills_command}"

            memory_command = self._build_register_memory_command()
            if memory_command:
                setup_command += f" && {memory_command}"

            mcp_command = self._build_register_mcp_servers_command()
            if mcp_command:
                setup_command += f" && {mcp_command}"

            cli_flags = self.build_cli_flags()
            extra_flags = (cli_flags + " ") if cli_flags else ""
            resume_flag = "--continue " if self._resume else ""

            await self.exec_as_agent(
                environment,
                command=setup_command,
                env=env,
            )

            instruction_shell_var = f"harbor_claude_code_instruction_{uuid.uuid4().hex}"
            instruction_env_var = instruction_shell_var.upper()
            run_env = {**env, instruction_env_var: instruction}

            await self.exec_as_agent(
                environment,
                command=(
                    'export PATH="$HOME/.local/bin:$PATH"; '
                    f'{instruction_shell_var}="${instruction_env_var}"; '
                    f"unset {instruction_env_var}; "
                    f'printf "%s" "${instruction_shell_var}" | '
                    f"claude --verbose --output-format=stream-json "
                    f"{extra_flags}"
                    f"{resume_flag}"
                    f"--print 2>&1 | tee "
                    f"/logs/agent/claude-code.txt"
                ),
                env=run_env,
            )

        upstream_base_url = self._get_env("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"
        await self._run_with_capture(
            environment,
            instruction=instruction,
            upstream_base_url=upstream_base_url,
            env_updates={"ANTHROPIC_BASE_URL": "unused"},
            runner=_run_impl,
        )

    async def _run_with_capture(
        self,
        environment: BaseEnvironment,
        *,
        instruction: str,
        upstream_base_url: str,
        env_updates: dict[str, str] | None = None,
        runner: Any,
    ) -> None:
        if not self._capture_http:
            await runner()
            return

        proxy_info = await self._start_capture_proxy(
            environment,
            upstream_base_url=upstream_base_url,
        )
        copied_artifacts: list[str] = []
        original_extra_env = dict(self._extra_env)
        scoped_env = {
            "ANTHROPIC_BASE_URL": str(proxy_info["local_base_url"]),
        }
        try:
            self._extra_env.update(scoped_env)
            with environment.scoped_exec_env(scoped_env):
                await runner()
        finally:
            try:
                await self._stop_capture_proxy(environment, proxy_info)
            finally:
                copied_artifacts = await self._copy_capture_artifacts(
                    environment, proxy_info
                )
            self._extra_env = original_extra_env
            self._write_capture_summary(
                instruction=instruction,
                proxy_info=proxy_info,
                copied_artifacts=copied_artifacts,
            )


class TracingCodex(HttpCaptureMixin, Codex):
    _COMPAT_PROVIDER_ID = "cvebench_gateway"

    def _build_register_mcp_servers_command(self) -> str | None:
        """Configure non-OpenAI gateways for HTTP Responses transport.

        Recent Codex releases default the built-in OpenAI provider to the
        Responses WebSocket transport. The capture proxy and the configured
        OpenAI-compatible gateway use HTTP/SSE, so register a custom provider
        that explicitly disables WebSockets while retaining the Responses API.

        Harbor calls this hook after creating config.toml, making it the one
        extension point where provider and MCP configuration can be appended
        without duplicating Codex.run().
        """
        commands: list[str] = []
        base_url = self._get_env("OPENAI_BASE_URL")
        if base_url:
            provider_config = "\n".join(
                [
                    f'model_provider = "{self._COMPAT_PROVIDER_ID}"',
                    "",
                    f"[model_providers.{self._COMPAT_PROVIDER_ID}]",
                    'name = "CVEBench OpenAI-compatible gateway"',
                    f"base_url = {json.dumps(base_url)}",
                    'env_key = "OPENAI_API_KEY"',
                    'wire_api = "responses"',
                    "supports_websockets = false",
                    "",
                ]
            )
            commands.append(
                f'printf \'%s\\n\' {shlex.quote(provider_config)} '
                '>> "$CODEX_HOME/config.toml"'
            )

        mcp_command = super()._build_register_mcp_servers_command()
        if mcp_command:
            commands.append(mcp_command)

        return "\n".join(commands) or None

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        upstream_base_url = self._get_env("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        await self._run_with_capture(
            environment,
            instruction=instruction,
            upstream_base_url=upstream_base_url,
            env_updates={
                "OPENAI_BASE_URL": "unused",
                "OPENAI_API_BASE": "unused",
            },
            runner=lambda: super(TracingCodex, self).run(
                instruction, environment, context
            ),
        )

    async def _run_with_capture(
        self,
        environment: BaseEnvironment,
        *,
        instruction: str,
        upstream_base_url: str,
        env_updates: dict[str, str] | None = None,
        runner: Any,
    ) -> None:
        if not self._capture_http:
            await runner()
            return

        proxy_info = await self._start_capture_proxy(
            environment,
            upstream_base_url=upstream_base_url,
        )
        copied_artifacts: list[str] = []
        original_extra_env = dict(self._extra_env)
        scoped_env = {
            "OPENAI_BASE_URL": str(proxy_info["local_base_url"]),
            "OPENAI_API_BASE": str(proxy_info["local_base_url"]),
        }
        try:
            self._extra_env.update(scoped_env)
            with environment.scoped_exec_env(scoped_env):
                await runner()
        finally:
            try:
                await self._stop_capture_proxy(environment, proxy_info)
            finally:
                copied_artifacts = await self._copy_capture_artifacts(
                    environment, proxy_info
                )
            self._extra_env = original_extra_env
            self._write_capture_summary(
                instruction=instruction,
                proxy_info=proxy_info,
                copied_artifacts=copied_artifacts,
            )


class _TracingOpenCodeBase(HttpCaptureMixin, OpenCode):
    def _build_register_config_command(self) -> str | None:
        config: dict[str, Any] = {}

        if self.mcp_servers:
            mcp: dict[str, dict[str, Any]] = {}
            for server in self.mcp_servers:
                if server.transport == "stdio":
                    cmd_list = [server.command] + server.args if server.command else []
                    mcp[server.name] = {"type": "local", "command": cmd_list}
                else:
                    mcp[server.name] = {"type": "remote", "url": server.url}
            config["mcp"] = mcp

        if self.model_name and "/" in self.model_name:
            provider, model_id = self.model_name.split("/", 1)
            provider_config: dict[str, Any] = {"models": {model_id: {}}}
            base_url = self._get_env("OPENAI_BASE_URL")
            if base_url and provider == "openai":
                provider_config.setdefault("options", {})["baseURL"] = base_url
            config["provider"] = {provider: provider_config}

        config = self._deep_merge(copy.deepcopy(self._DEFAULT_CONFIG), config)
        config = self._deep_merge(config, self._opencode_config)
        if not config:
            return None

        config_json = json.dumps(config, indent=2)
        escaped = shlex.quote(config_json)
        return (
            "mkdir -p ~/.config/opencode && "
            f"echo {escaped} > ~/.config/opencode/opencode.json"
        )

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        self._instruction = instruction
        escaped_instruction = shlex.quote(instruction)

        if not self.model_name or "/" not in self.model_name:
            raise ValueError("Model name must be in the format provider/model_name")

        provider, _ = self.model_name.split("/", 1)
        env: dict[str, str] = {}
        keys: list[str] = []

        if provider == "amazon-bedrock":
            keys.extend(["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"])
        elif provider == "anthropic":
            keys.append("ANTHROPIC_API_KEY")
        elif provider == "azure":
            keys.extend(["AZURE_RESOURCE_NAME", "AZURE_API_KEY"])
        elif provider == "deepseek":
            keys.append("DEEPSEEK_API_KEY")
        elif provider == "github-copilot":
            keys.append("GITHUB_TOKEN")
        elif provider == "google":
            keys.extend(
                [
                    "GEMINI_API_KEY",
                    "GOOGLE_GENERATIVE_AI_API_KEY",
                    "GOOGLE_APPLICATION_CREDENTIALS",
                    "GOOGLE_CLOUD_PROJECT",
                    "GOOGLE_CLOUD_LOCATION",
                    "GOOGLE_GENAI_USE_VERTEXAI",
                    "GOOGLE_API_KEY",
                ]
            )
        elif provider == "groq":
            keys.append("GROQ_API_KEY")
        elif provider == "huggingface":
            keys.append("HF_TOKEN")
        elif provider == "llama":
            keys.append("LLAMA_API_KEY")
        elif provider == "mistral":
            keys.append("MISTRAL_API_KEY")
        elif provider == "openai":
            keys.extend(["OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE"])
        elif provider == "opencode":
            keys.append("OPENCODE_API_KEY")
        elif provider == "xai":
            keys.append("XAI_API_KEY")
        elif provider == "openrouter":
            keys.append("OPENROUTER_API_KEY")

        for key in keys:
            value = self._get_env(key)
            if value is not None:
                env[key] = value

        env["OPENCODE_FAKE_VCS"] = "git"
        env["XDG_DATA_HOME"] = "/tmp/opencode/xdg-data"
        env["XDG_STATE_HOME"] = "/tmp/opencode/xdg-state"

        cli_flags = self.build_cli_flags()
        cli_flags_arg = (cli_flags + " ") if cli_flags else ""
        resume_flag = "--continue " if self._resume else ""

        async def _run_impl() -> None:
            skills_command = self._build_register_skills_command()
            if skills_command:
                await self.exec_as_agent(environment, command=skills_command, env=env)

            mcp_command = self._build_register_config_command()
            if mcp_command:
                await self.exec_as_agent(environment, command=mcp_command, env=env)

            await self.exec_as_agent(
                environment,
                command=(
                    ". ~/.nvm/nvm.sh; "
                    f"opencode --model={self.model_name} run --format=json "
                    f"{resume_flag}{cli_flags_arg}--thinking "
                    f"--dangerously-skip-permissions -- {escaped_instruction} "
                    f"2>&1 </dev/null | stdbuf -oL tee /logs/agent/opencode.txt"
                ),
                env=env,
            )

            if messages := self._error_messages():
                raise NonZeroAgentExitCodeError(
                    "OpenCode emitted error event(s): " + "; ".join(messages[:3])
                )

        upstream_base_url = (
            self._get_env("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        )
        await self._run_with_capture(
            environment,
            instruction=instruction,
            upstream_base_url=upstream_base_url,
            runner=_run_impl,
            env=env,
        )

    async def _run_with_capture(
        self,
        environment: BaseEnvironment,
        *,
        instruction: str,
        upstream_base_url: str,
        runner: Any,
        env: dict[str, str],
    ) -> None:
        if not self._capture_http:
            await runner()
            return

        proxy_info = await self._start_capture_proxy(
            environment,
            upstream_base_url=upstream_base_url,
        )
        copied_artifacts: list[str] = []
        original_extra_env = dict(self._extra_env)
        scoped_env = {
            "OPENAI_BASE_URL": str(proxy_info["local_base_url"]),
            "OPENAI_API_BASE": str(proxy_info["local_base_url"]),
        }
        try:
            self._extra_env.update(scoped_env)
            env.update(scoped_env)
            with environment.scoped_exec_env(scoped_env):
                await runner()
        finally:
            try:
                await self._stop_capture_proxy(environment, proxy_info)
            finally:
                copied_artifacts = await self._copy_capture_artifacts(
                    environment, proxy_info
                )
            self._extra_env = original_extra_env
            self._write_capture_summary(
                instruction=instruction,
                proxy_info=proxy_info,
                copied_artifacts=copied_artifacts,
            )


class TracingOfflineOpenCode(_TracingOpenCodeBase, OfflineOpenCode):
    pass


class TracingOfflineMiniSweAgent(HttpCaptureMixin, OfflineMiniSweAgent):
    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        upstream_base_url = self._get_env("OPENAI_BASE_URL") or self._get_env(
            "OPENAI_API_BASE"
        ) or "https://api.openai.com/v1"
        await self._run_with_capture(
            environment,
            instruction=instruction,
            upstream_base_url=upstream_base_url,
            runner=lambda: super(TracingOfflineMiniSweAgent, self).run(
                instruction, environment, context
            ),
        )

    async def _run_with_capture(
        self,
        environment: BaseEnvironment,
        *,
        instruction: str,
        upstream_base_url: str,
        env_updates: dict[str, str] | None = None,
        runner: Any,
    ) -> None:
        if not self._capture_http:
            await runner()
            return

        proxy_info = await self._start_capture_proxy(
            environment,
            upstream_base_url=upstream_base_url,
        )
        copied_artifacts: list[str] = []
        original_extra_env = dict(self._extra_env)
        scoped_env = {
            "OPENAI_BASE_URL": str(proxy_info["local_base_url"]),
            "OPENAI_API_BASE": str(proxy_info["local_base_url"]),
        }
        try:
            self._extra_env.update(scoped_env)
            with environment.scoped_exec_env(scoped_env):
                await runner()
        finally:
            try:
                await self._stop_capture_proxy(environment, proxy_info)
            finally:
                copied_artifacts = await self._copy_capture_artifacts(
                    environment, proxy_info
                )
            self._extra_env = original_extra_env
            self._write_capture_summary(
                instruction=instruction,
                proxy_info=proxy_info,
                copied_artifacts=copied_artifacts,
            )
