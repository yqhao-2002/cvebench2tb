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
"""

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
