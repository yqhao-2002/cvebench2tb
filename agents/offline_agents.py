"""Offline variants of Harbor's opencode / mini-swe-agent agents.

Harbor's stock implementations run ``npm i -g opencode-ai`` / ``uv tool install
mini-swe-agent`` on *every* setup, which needs container network access.  Our
kali-agents image already ships these CLIs, so these subclasses add the same
"already installed -> skip" check that Harbor's claude-code and codex agents
have natively (``command -v <binary>``).  Everything else (run loop, trajectory
parsing, model/config wiring) is inherited unchanged.

Usage (PYTHONPATH must point at the cvebench2tb repo root):

    PYTHONPATH=/root/cvebench2tb harbor run --path <task> \\
        --agent agents.offline_agents:OfflineOpenCode ...

codex does NOT need an offline variant: Harbor's ``codex`` agent already skips
install when ``command -v codex`` succeeds, exactly like claude-code.

OpenCode wire protocol
----------------------
opencode's built-in ``openai`` provider is hard-wired to the OpenAI Responses
API and, on replay, DROPS prior-turn reasoning items unless the server returns
``encrypted_content`` (verified against opencode 1.18.18: the replayed
assistant message only carries ``output_text``).  Most OpenAI-compatible
gateways return plain reasoning, so multi-turn runs silently lose the model's
reasoning chain.

``OfflineOpenCode._build_register_config_command`` therefore re-registers the
``openai`` provider through ``@ai-sdk/openai-compatible`` (bundled inside the
opencode binary — no npm access needed).  That adapter speaks Chat
Completions and replays assistant reasoning as ``reasoning_content``
(mock-gateway verified: turn-2 request carries
``reasoning_content='...'`` on the assistant message).

Select the old behaviour with ``CB2TB_OPENCODE_PROTOCOL=responses``.
"""

import copy
import json
import os
import shlex

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.agents.installed.mini_swe_agent import MiniSweAgent
from harbor.agents.installed.opencode import OpenCode
from harbor.environments.base import BaseEnvironment


async def _preinstalled_binary_satisfies_version(
    agent: BaseInstalledAgent,
    environment: BaseEnvironment,
    binary: str,
) -> bool:
    result = await environment.exec(command=f"command -v {binary} >/dev/null 2>&1")
    if result.return_code != 0:
        return False
    if agent.version() is None:
        return True

    version_command = agent.get_version_command()
    if version_command is None:
        return False
    version_result = await environment.exec(command=version_command)
    if version_result.return_code != 0:
        return False
    return agent.parse_version(version_result.stdout or "") == agent.version()


def opencode_wire_protocol() -> str:
    """Chat completions by default; ``responses`` opts back into the old wire."""
    return os.environ.get("CB2TB_OPENCODE_PROTOCOL", "chat").strip().lower()


def opencode_offline_env() -> dict[str, str]:
    """Env vars that keep the standalone opencode binary usable without any
    external network (offline evaluation tasks).

    - ``OPENCODE_DISABLE_MODELS_FETCH=1``: skip the models.dev registry sync
      (verified: without it the CLI can block ~15 s or hang on offline runs).
    - ``OPENCODE_PURE=1``: skip external plugin discovery entirely.
    - ``OPENCODE_DISABLE_AUTOUPDATE=1`` / ``DO_NOT_TRACK=1``: the update
      check blocked fresh-HOME first runs offline for >45 s (mock-gateway
      verified); harmless online.

    Disable with ``CB2TB_OPENCODE_OFFLINE=0`` (online environments that want
    plugin/model-registry features).
    """
    if os.environ.get("CB2TB_OPENCODE_OFFLINE", "1") == "0":
        return {}
    return {
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCODE_PURE": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "DO_NOT_TRACK": "1",
    }


# The built-in provider ids of opencode must not be reused for the npm
# override: re-registering provider id "openai" with a custom npm package
# makes opencode resolve built-in openai metadata and block offline
# (mock-gateway verified).  A dedicated id routes through the bundled
# @ai-sdk/openai-compatible adapter cleanly.
OPENCODE_CHAT_PROVIDER_ID = "openai-chat"


def opencode_wire_model_ref(model_name: str | None) -> str | None:
    """Map ``openai/<model>`` to the chat-completions provider ref.

    Only active when the wire protocol is "chat" and OPENAI_BASE_URL is set
    (i.e. an OpenAI-compatible gateway).  Returns None when no translation
    applies, meaning the caller should use the model name as-is.
    """
    if not model_name or "/" not in model_name:
        return None
    provider, model_id = model_name.split("/", 1)
    if provider != "openai":
        return None
    if opencode_wire_protocol() != "chat":
        return None
    if not os.environ.get("OPENAI_BASE_URL"):
        return None
    return f"{OPENCODE_CHAT_PROVIDER_ID}/{model_id}"


class OfflineOpenCode(OpenCode):
    """OpenCode that skips install when the CLI is already in the image."""

    def get_version_command(self) -> str:
        # The standalone binary does not need the NVM initialization used by
        # Harbor's npm-installed OpenCode.
        return "opencode --version"

    async def install(self, environment: BaseEnvironment) -> None:
        if await _preinstalled_binary_satisfies_version(
            self, environment, "opencode"
        ):
            self.logger.debug(
                "OpenCode is already available at the requested version; "
                "skipping install"
            )
            return
        await super().install(environment)

    async def run(self, instruction, environment, context) -> None:
        """Run with model-ref translation + offline env injection.

        Harbor's stock run() builds ``--model={self.model_name}``; when the
        chat-completions provider is active the CLI must see the translated
        ``openai-chat/<model>`` ref instead.  We wrap exec_as_agent to rewrite
        the flag and merge ``opencode_offline_env()`` into every command env.
        """
        wire_ref = opencode_wire_model_ref(self.model_name)
        original_exec = self.exec_as_agent
        model_flag = f"--model={self.model_name}"

        async def _patched_exec(environment_, command, env=None, **kwargs):
            if wire_ref and model_flag in command:
                command = command.replace(model_flag, f"--model={wire_ref}")
            # Shield opencode from host-global OOM (74G virtual memory makes
            # it the killer's top pick despite a small RSS); harmless no-op
            # where /proc/self is not writable.
            if command.startswith(". ~/.nvm/nvm.sh; opencode "):
                command = command.replace(
                    ". ~/.nvm/nvm.sh; ",
                    ". ~/.nvm/nvm.sh; echo -1000 > /proc/self/oom_score_adj 2>/dev/null; ",
                    1,
                )
            if env is None:
                env = {}
            env = {**env, **opencode_offline_env()}
            return await original_exec(
                environment=environment_, command=command, env=env, **kwargs
            )

        self.exec_as_agent = _patched_exec
        try:
            await super().run(instruction, environment, context)
        finally:
            self.exec_as_agent = original_exec

    def _build_register_config_command(self) -> str | None:
        """Generate opencode.json; route the ``openai`` provider over
        Chat Completions with reasoning replay (see module docstring)."""
        config: dict = {}

        if self.mcp_servers:
            mcp: dict = {}
            for server in self.mcp_servers:
                if server.transport == "stdio":
                    cmd_list = [server.command] + server.args if server.command else []
                    mcp[server.name] = {"type": "local", "command": cmd_list}
                else:
                    mcp[server.name] = {"type": "remote", "url": server.url}
            config["mcp"] = mcp

        if self.model_name and "/" in self.model_name:
            provider, model_id = self.model_name.split("/", 1)
            provider_config: dict = {"models": {model_id: {}}}
            base_url = os.environ.get("OPENAI_BASE_URL")
            if provider == "openai" and base_url and opencode_wire_protocol() == "chat":
                # Register the chat-completions provider under a dedicated id
                # (built-in ids must not be reused — see OPENCODE_CHAT_PROVIDER_ID).
                # --model openai/<id> keeps working: run() translates the CLI
                # flag to openai-chat/<id>.
                provider_config["npm"] = "@ai-sdk/openai-compatible"
                provider_config["name"] = "OpenAI-compatible (chat completions)"
                provider_config["options"] = {
                    "baseURL": base_url,
                    "apiKey": os.environ.get("OPENAI_API_KEY", "dummy-key"),
                }
                provider = OPENCODE_CHAT_PROVIDER_ID
            elif base_url and provider in ("openai", "anthropic"):
                # Stock behaviour: only set baseURL in provider options.
                provider_config.setdefault("options", {})["baseURL"] = base_url
            config["provider"] = {provider: provider_config}

        config = OpenCode._deep_merge(
            copy.deepcopy(OpenCode._DEFAULT_CONFIG), config
        )
        config = OpenCode._deep_merge(config, self._opencode_config)
        if not config:
            return None

        config_json = json.dumps(config, indent=2)
        escaped = shlex.quote(config_json)
        return (
            "mkdir -p ~/.config/opencode && "
            f"echo {escaped} > ~/.config/opencode/opencode.json"
        )


class OfflineMiniSweAgent(MiniSweAgent):
    """mini-swe-agent that skips install when the CLI is already in the image."""

    def get_version_command(self) -> str:
        # Resolve the CLI symlink and query the adjacent venv Python. This also
        # works if the fallback Harbor installer placed the tool in a uv venv.
        return (
            'MINI_BIN="$(readlink -f "$(command -v mini-swe-agent)")"; '
            'MINI_PY="$(dirname "$MINI_BIN")/python"; '
            '"$MINI_PY" -c \'import importlib.metadata as m; '
            'print(m.version("mini-swe-agent"))\''
        )

    async def install(self, environment: BaseEnvironment) -> None:
        if await _preinstalled_binary_satisfies_version(
            self, environment, "mini-swe-agent"
        ):
            self.logger.debug(
                "mini-swe-agent is already available at the requested version; "
                "skipping install"
            )
            return
        await super().install(environment)
