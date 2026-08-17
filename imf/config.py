"""Four-operative roster and local settings.

Secrets stay in the process environment. Configuration objects only store
environment-variable *names*.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal


Backend = Literal["openai_compatible", "cursor"]
OodaPhase = Literal["observe", "orient", "decide", "act"]

# OODA loop order. Default dispatch follows this sequence.
AGENT_IDS = ("flash", "pro", "luna", "grok")

# Long-running SRC turns need room for model/tool work. A heartbeat observed
# during the polling window refreshes the idle deadline, up to the hard cap.
DEFAULT_DISPATCH_TIMEOUT = 1_800
DEFAULT_POLL_INTERVAL = 600
DEFAULT_MAX_RUNTIME = 7_200


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One operative in the Instant Message Force."""

    name: str
    display: str
    vendor: str
    backend: Backend
    model: str
    reasoning: str
    ooda: OodaPhase
    context_tokens: int
    feature: str
    style: str
    duty: str
    base_url: str = ""
    api_key_env: str = ""

    @property
    def role(self) -> str:
        return self.style

    @property
    def configured(self) -> bool:
        return bool(
            self.model
            and self.base_url
            and self.api_key_env
            and os.getenv(self.api_key_env, "").strip()
        )


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    workplaces_dir: Path
    env_file: Path
    web_dir: Path
    catalog_path: Path
    agents: dict[str, AgentSpec]
    feishu_app_id_env: str = "FEISHU_APP_ID"
    feishu_app_secret_env: str = "FEISHU_APP_SECRET"
    feishu_folder_token_env: str = "FEISHU_FOLDER_TOKEN"
    feishu_chat_id_env: str = "FEISHU_CHAT_ID"

    @property
    def feishu_configured(self) -> bool:
        return bool(
            os.getenv(self.feishu_app_id_env, "").strip()
            and os.getenv(self.feishu_app_secret_env, "").strip()
        )


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def load_local_env(path: Path) -> None:
    """Load an ignored local env file without overriding process variables."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _deepseek(
    *,
    name: str,
    display: str,
    model: str,
    reasoning: str,
    ooda: OodaPhase,
    context_tokens: int,
    feature: str,
    style: str,
    duty: str,
) -> AgentSpec:
    provider = _env("DEEPSEEK_PROVIDER", "direct").lower()
    if provider not in {"direct", "siliconflow"}:
        raise ValueError("DEEPSEEK_PROVIDER must be direct or siliconflow")
    if provider == "siliconflow":
        base_url = _env("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
        key_env = "SILICONFLOW_API_KEY"
        model_name = _env(f"{name.upper()}_MODEL", model)
    else:
        base_url = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        key_env = "DEEPSEEK_API_KEY"
        model_name = _env(f"{name.upper()}_MODEL", model)
    return AgentSpec(
        name=name,
        display=display,
        vendor="DeepSeek",
        backend="openai_compatible",
        model=model_name,
        reasoning=_env(f"{name.upper()}_REASONING", reasoning),
        ooda=ooda,
        context_tokens=context_tokens,
        feature=feature,
        style=style,
        duty=duty,
        base_url=base_url.rstrip("/"),
        api_key_env=key_env,
    )


def load_settings(root: Path | None = None) -> Settings:
    repo = (root or Path(__file__).resolve().parent.parent).resolve()
    env_file = repo / ".env"
    load_local_env(env_file)
    workplaces = Path(_env("IMF_WORKPLACES", str(repo / "workplaces"))).resolve()
    agents = {
        "flash": _deepseek(
            name="flash",
            display="Flash",
            model="deepseek-v4-flash",
            reasoning="high",
            ooda="observe",
            context_tokens=1_000_000,
            feature="Observe — 1M window, restless scout.",
            style=(
                "Divergent and unable to sit still. Burn the 1M window on authorized "
                "surface coverage: paths, configs, hypotheses, odd edges. Dump it to "
                "the board fast. Do not close the case. Do not issue orders."
            ),
            duty="Observe widely. Post raw coverage. Leave Orient and Decide to the others.",
        ),
        "pro": _deepseek(
            name="pro",
            display="Pro",
            model="deepseek-v4-pro",
            reasoning="max",
            ooda="orient",
            context_tokens=1_000_000,
            feature="Orient — 1M window, god/ghost oscillation.",
            style=(
                "神鬼二象性: one beat up at architecture, the next beat down in a "
                "single offset. Read Flash on the board, shift the frame, drop noise, "
                "name contradictions. Adjust the picture. Do not act."
            ),
            duty="Reframe Flash's log. Oscillate high/low. Hand Luna a clean picture, not a raid.",
        ),
        "luna": AgentSpec(
            name="luna",
            display="Luna",
            vendor="OpenAI via Cursor",
            backend="cursor",
            model=_env("CURSOR_LUNA_MODEL", "gpt-5.6-luna-high"),
            reasoning="max",
            ooda="decide",
            context_tokens=500_000,
            feature="Decide — 500k window, generalist closer.",
            style=(
                "All-rounder with a 500k window. Read Observe and Orient, pick one "
                "path, and write a bounded order for Grok: target, allowed steps, "
                "stop condition. Do not wander into the dirty work."
            ),
            duty="Decide. Write one concrete order for Grok on the board.",
            base_url=_env("CURSOR_API_BASE"),
            api_key_env="CURSOR_API_KEY",
        ),
        "grok": AgentSpec(
            name="grok",
            display="Grok",
            vendor="xAI via Cursor",
            backend="cursor",
            model=_env("CURSOR_GROK_MODEL", "cursor-grok-4.6-high"),
            reasoning="high",
            ooda="act",
            context_tokens=256_000,
            feature="Act — 256k window, sharp and lazy.",
            style=(
                "Smart enough, short window, prefers not to think twice. Point and "
                "shoot: execute Luna's latest order, cite evidence, stop. No replans, "
                "no extra reconnaissance, no essays."
            ),
            duty="Act on Luna's last order only. Do the named steps. Stop.",
            base_url=_env("CURSOR_API_BASE"),
            api_key_env="CURSOR_API_KEY",
        ),
    }
    return Settings(
        root=repo,
        workplaces_dir=workplaces,
        env_file=env_file,
        web_dir=repo / "web",
        catalog_path=repo / "plugins" / "catalog.json",
        agents=agents,
    )
