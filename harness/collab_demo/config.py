"""Configuration and the fixed eight-agent roster.

Secrets are referenced by environment-variable name and are never copied into
configuration objects that are printed by the CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal


Backend = Literal["orchestrator", "cursor", "openai_compatible"]


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One model/provider entry in the collaboration roster."""

    name: str
    vendor: str
    backend: Backend
    role: str
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""

    @property
    def configured(self) -> bool:
        """Return whether this entry has enough local configuration to run."""
        if self.backend == "orchestrator":
            return True
        if self.backend == "cursor":
            return bool(self.model)
        return bool(
            self.model
            and self.base_url
            and self.api_key_env
            and os.getenv(self.api_key_env, "").strip()
        )


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings derived from environment variables."""

    root: Path
    state_dir: Path
    env_file: Path
    agents: dict[str, AgentSpec]
    feishu_app_id_env: str = "FEISHU_APP_ID"
    feishu_app_secret_env: str = "FEISHU_APP_SECRET"
    feishu_document_id_env: str = "FEISHU_DOCUMENT_ID"
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


def _api_agent(
    *,
    name: str,
    vendor: str,
    role: str,
    model_env: str,
    model_default: str,
    base_url_env: str,
    base_url_default: str,
    key_env: str,
) -> AgentSpec:
    provider = _env(f"{name.upper()}_PROVIDER", "direct").lower()
    if provider not in {"direct", "siliconflow"}:
        raise ValueError(
            f"{name.upper()}_PROVIDER must be direct or siliconflow",
        )
    if provider == "siliconflow":
        selected_base_url = _env(
            "SILICONFLOW_BASE_URL",
            "https://api.siliconflow.cn/v1",
        )
        selected_key_env = _env(
            f"{name.upper()}_API_KEY_ENV",
            "SILICONFLOW_API_KEY",
        )
    else:
        selected_base_url = _env(base_url_env, base_url_default)
        selected_key_env = _env(f"{name.upper()}_API_KEY_ENV", key_env)
    return AgentSpec(
        name=name,
        vendor=vendor,
        backend="openai_compatible",
        role=role,
        model=_env(model_env, model_default),
        base_url=selected_base_url.rstrip("/"),
        api_key_env=selected_key_env,
    )


def load_settings(root: Path | None = None) -> Settings:
    """Load settings without reading or persisting any secret values.

    The four API workers can use their vendor endpoints or SiliconFlow. Set
    the corresponding ``*_BASE_URL`` and ``*_MODEL`` together when switching
    an agent to a compatible aggregator endpoint.
    """
    harness_root = (root or Path(__file__).resolve().parent.parent).resolve()
    env_file = harness_root / ".env"
    load_local_env(env_file)
    state_dir = Path(
        _env("COLLAB_STATE_DIR", str(harness_root / ".collab")),
    ).resolve()

    agents = {
        "gpt": AgentSpec(
            name="gpt",
            vendor="OpenAI",
            backend="orchestrator",
            role=(
                "唯一编排器；先独立完成任务，仅在证据缺口、专长缺口或高风险"
                "复核确有价值时委派"
            ),
        ),
        "claude": AgentSpec(
            name="claude",
            vendor="Anthropic via Cursor",
            backend="cursor",
            model=_env("CURSOR_CLAUDE_MODEL", "auto"),
            role="代码审计、长链推理、实现复核",
        ),
        "gemini": AgentSpec(
            name="gemini",
            vendor="Google via Cursor",
            backend="cursor",
            model=_env("CURSOR_GEMINI_MODEL", "auto"),
            role="大上下文材料梳理、跨文件模式识别",
        ),
        "grok": AgentSpec(
            name="grok",
            vendor="xAI via Cursor",
            backend="cursor",
            model=_env("CURSOR_GROK_MODEL", "auto"),
            role="对抗性假设、另类攻击路径与反例检查",
        ),
        "deepseek": _api_agent(
            name="deepseek",
            vendor="DeepSeek",
            role="CTF 推理、漏洞链假设和低成本初次复核",
            model_env="DEEPSEEK_MODEL",
            model_default="deepseek-chat",
            base_url_env="DEEPSEEK_BASE_URL",
            base_url_default="https://api.deepseek.com",
            key_env="DEEPSEEK_API_KEY",
        ),
        "kimi": _api_agent(
            name="kimi",
            vendor="Moonshot or SiliconFlow",
            role="中文长上下文、报告与资料归纳",
            model_env="KIMI_MODEL",
            model_default="",
            base_url_env="KIMI_BASE_URL",
            base_url_default="https://api.moonshot.cn/v1",
            key_env="KIMI_API_KEY",
        ),
        "glm": _api_agent(
            name="glm",
            vendor="Zhipu or SiliconFlow",
            role="中文推理、通用复核与工具方案",
            model_env="GLM_MODEL",
            model_default="",
            base_url_env="GLM_BASE_URL",
            base_url_default="https://open.bigmodel.cn/api/paas/v4",
            key_env="GLM_API_KEY",
        ),
        "qwen": _api_agent(
            name="qwen",
            vendor="Alibaba or SiliconFlow",
            role="代码生成、脚本分析与国内技术栈",
            model_env="QWEN_MODEL",
            model_default="",
            base_url_env="QWEN_BASE_URL",
            base_url_default="https://dashscope.aliyuncs.com/compatible-mode/v1",
            key_env="QWEN_API_KEY",
        ),
    }
    return Settings(
        root=harness_root,
        state_dir=state_dir,
        env_file=env_file,
        agents=agents,
    )
