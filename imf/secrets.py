"""Redact credentials so they never land in boards, events, or prompts."""

from __future__ import annotations

import os
import re
from typing import Any

SECRET_ENV_NAMES = (
    "FEISHU_APP_SECRET",
    "DEEPSEEK_API_KEY",
    "CURSOR_API_KEY",
    "SILICONFLOW_API_KEY",
)

_TOKEN_PATTERNS = (
    re.compile(
        r"(?i)(api[_-]?key|app[_-]?secret|authorization|password|passwd|pwd|"
        r"cookie|session(?:[_-]?id)?|access[_-]?token|refresh[_-]?token|secret)"
        r"\s*[:=]\s*[^\s,;]+",
    ),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)


def redact_text(value: str) -> str:
    redacted = value
    for env_name in SECRET_ENV_NAMES:
        secret = os.getenv(env_name, "")
        if secret and len(secret) >= 6:
            redacted = redacted.replace(secret, f"<redacted:{env_name}>")
    for pattern in _TOKEN_PATTERNS:
        redacted = pattern.sub("<redacted:credential>", redacted)
    return redacted


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value
