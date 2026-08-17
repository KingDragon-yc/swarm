"""Adapters: DeepSeek HTTP, Cursor CLI, optional Cursor-compatible HTTP."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Protocol
from urllib import error, request

from .config import AgentSpec
from .plugins import parse_plugins_markdown
from .secrets import redact_text


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CURSOR_PREFERENCES = {
    "grok": (
        "cursor-grok-4.6-high",
        "cursor-grok-4.5-high",
        "cursor-grok-4.6-medium",
        "cursor-grok-4.5-medium",
    ),
    "luna": (
        "gpt-5.6-luna-high",
        "gpt-5.6-luna",
        "gpt-5.5",
    ),
}


@dataclass(frozen=True, slots=True)
class ProviderResult:
    agent: str
    text: str
    duration_ms: int
    plugins: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class ProviderAdapter(Protocol):
    def invoke(
        self,
        *,
        task: str,
        context: str,
        workspace: Path,
        timeout: int,
    ) -> ProviderResult: ...


def extract_plugin_update(text: str) -> list[str]:
    return parse_plugins_markdown(text)


def operative_prompt(
    spec: AgentSpec,
    *,
    task: str,
    context: str,
    plugins: list[str],
    cell: str,
) -> str:
    envelope = {
        "force": "IMF — Instant Message Force",
        "operative": spec.display,
        "id": spec.name,
        "feature": spec.feature,
        "role": spec.role,
        "authorization": "authorized SRC / lab scope only",
        "plugins": plugins,
        "cell": cell,
        "task": task,
        "board_excerpt": context[-12_000:],
        "constraints": [
            "stay inside the authorized workplace",
            "do not include API keys, cookies, or secrets",
            "cite files, offsets, requests, or commands as evidence",
            "label hypotheses and uncertainty",
            "your reply is posted to the shared Feishu board",
            "to change tools, include a ## Plugins list of catalog ids",
        ],
        "required_headings": [
            "## Findings",
            "## Evidence",
            "## Uncertainty",
            "## Board update",
        ],
    }
    return "Write the board update for this IMF envelope: " + json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def parse_cursor_model_list(output: str) -> list[str]:
    models: list[str] = []
    for raw_line in output.splitlines():
        line = _ANSI_ESCAPE_RE.sub("", raw_line).strip()
        if " - " not in line:
            continue
        model_id = line.split(" - ", 1)[0].strip()
        if model_id and model_id not in models:
            models.append(model_id)
    return models


def _cursor_executable() -> str | None:
    return shutil.which("agent.cmd") or shutil.which("agent")


def list_cursor_models(*, timeout: int = 30) -> list[str]:
    executable = _cursor_executable()
    if not executable:
        raise RuntimeError("Cursor Agent CLI was not found on PATH")
    completed = subprocess.run(
        [executable, "--list-models"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env={**os.environ, "NO_COLOR": "1"},
    )
    if completed.returncode != 0:
        diagnostic = redact_text((completed.stderr or completed.stdout)[-1000:])
        raise RuntimeError(
            f"Cursor model probe exited {completed.returncode}: {diagnostic}",
        )
    models = parse_cursor_model_list(completed.stdout)
    if not models:
        raise RuntimeError("Cursor model probe returned no parseable model IDs")
    return models


def cursor_model_candidates(agent: str, available: list[str]) -> list[str]:
    available_set = set(available)
    ranked = [
        model
        for model in _CURSOR_PREFERENCES.get(agent, ())
        if model in available_set
    ]
    if agent == "grok":
        extras = [model for model in available if "grok" in model and model not in ranked]
    elif agent == "luna":
        extras = [
            model
            for model in available
            if "luna" in model and model not in ranked
        ]
    else:
        extras = []
    return ranked + extras


def resolve_cursor_model(spec: AgentSpec, available: list[str]) -> tuple[str, str]:
    configured = spec.model.strip()
    candidates = cursor_model_candidates(spec.name, available)
    if configured and configured.lower() != "auto":
        if configured in available:
            return configured, "configured"
        suggestion = ", ".join(candidates[:5]) or "no vendor model found"
        raise RuntimeError(
            f"configured model '{configured}' is unavailable; candidates: {suggestion}",
        )
    if not candidates:
        raise RuntimeError(
            f"Cursor listed no model for {spec.name}; refresh/login may be required",
        )
    return candidates[0], "auto"


def _cursor_command() -> list[str]:
    executable = Path(_cursor_executable() or "").resolve()
    if not executable.is_file():
        raise RuntimeError("Cursor Agent CLI was not found on PATH")
    install_dir = executable.parent
    versions_dir = install_dir / "versions"
    if versions_dir.is_dir():
        candidates = sorted(
            (
                path
                for path in versions_dir.iterdir()
                if path.is_dir()
                and (path / "node.exe").is_file()
                and (path / "index.js").is_file()
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            selected = candidates[0]
            return [str(selected / "node.exe"), str(selected / "index.js")]
    return [str(executable)]


def parse_cursor_json(stdout: str) -> tuple[str, dict[str, Any]]:
    candidates = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    if not candidates:
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Cursor CLI returned non-JSON output") from exc
        if isinstance(value, dict):
            candidates.append(value)
    terminal = next(
        (
            value
            for value in reversed(candidates)
            if value.get("type") == "result" or "result" in value
        ),
        None,
    )
    if terminal is None:
        raise RuntimeError("Cursor CLI JSON contained no terminal result")
    if terminal.get("is_error") or terminal.get("subtype") == "error":
        raise RuntimeError(
            "Cursor CLI failed: " + redact_text(str(terminal.get("result", "unknown"))),
        )
    text = terminal.get("result")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Cursor CLI returned an empty result")
    metadata = {
        key: terminal[key]
        for key in ("duration_ms", "duration_api_ms", "session_id", "usage")
        if key in terminal
    }
    return text.strip(), metadata


def _result(spec: AgentSpec, text: str, duration_ms: int, metadata: dict[str, Any]) -> ProviderResult:
    cleaned = redact_text(text.strip())
    return ProviderResult(
        agent=spec.name,
        text=cleaned,
        duration_ms=duration_ms,
        plugins=extract_plugin_update(cleaned),
        metadata=metadata,
    )


class CursorAdapter:
    """Cursor CLI first; OpenAI-compatible HTTP if CURSOR_API_BASE is set."""

    def __init__(self, spec: AgentSpec) -> None:
        if spec.backend != "cursor":
            raise ValueError(f"{spec.name} is not a Cursor operative")
        self.spec = spec

    def invoke(
        self,
        *,
        task: str,
        context: str,
        workspace: Path,
        timeout: int,
        plugins: list[str],
        cell: str,
    ) -> ProviderResult:
        prompt = operative_prompt(
            self.spec,
            task=task,
            context=context,
            plugins=plugins,
            cell=cell,
        )
        if _cursor_executable():
            return self._cli(prompt, workspace, timeout)
        if self.spec.base_url and os.getenv(self.spec.api_key_env, "").strip():
            http = OpenAICompatibleAdapter(self.spec)
            return http.invoke(
                task=task,
                context=context,
                workspace=workspace,
                timeout=timeout,
                plugins=plugins,
                cell=cell,
            )
        raise RuntimeError(
            f"{self.spec.name} needs Cursor CLI on PATH, or CURSOR_API_BASE plus CURSOR_API_KEY",
        )

    def _cli(self, prompt: str, workspace: Path, timeout: int) -> ProviderResult:
        selected_model, selection = resolve_cursor_model(
            self.spec,
            list_cursor_models(timeout=min(timeout, 30)),
        )
        args = [
            *_cursor_command(),
            "--print",
            "--mode",
            "ask",
            "--output-format",
            "json",
            "--model",
            selected_model,
            "--trust",
            "--workspace",
            str(workspace.resolve()),
            prompt,
        ]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                args,
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env={**os.environ, "NO_COLOR": "1", "CURSOR_INVOKED_AS": "agent"},
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Cursor agent {self.spec.name} timed out after {timeout}s",
            ) from exc
        duration_ms = round((time.monotonic() - started) * 1000)
        if completed.returncode != 0:
            diagnostic = redact_text((completed.stderr or completed.stdout)[-2000:])
            raise RuntimeError(
                f"Cursor agent {self.spec.name} exited {completed.returncode}: {diagnostic}",
            )
        text, metadata = parse_cursor_json(completed.stdout)
        metadata["configured_model"] = self.spec.model
        metadata["resolved_model"] = selected_model
        metadata["model_selection"] = selection
        metadata["transport"] = "cursor_cli"
        return _result(self.spec, text, duration_ms, metadata)


class OpenAICompatibleAdapter:
    def __init__(self, spec: AgentSpec) -> None:
        self.spec = spec

    def invoke(
        self,
        *,
        task: str,
        context: str,
        workspace: Path,
        timeout: int,
        plugins: list[str],
        cell: str,
    ) -> ProviderResult:
        del workspace
        api_key = os.getenv(self.spec.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"{self.spec.name} is missing {self.spec.api_key_env}")
        if not self.spec.model:
            raise RuntimeError(f"{self.spec.name} model is not configured")
        if not self.spec.base_url:
            raise RuntimeError(f"{self.spec.name} has no API base URL")
        payload: dict[str, Any] = {
            "model": self.spec.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an IMF operative on an authorized SRC/lab mission. "
                        "Write a board update. Do not reveal private chain-of-thought."
                    ),
                },
                {
                    "role": "user",
                    "content": operative_prompt(
                        self.spec,
                        task=task,
                        context=context,
                        plugins=plugins,
                        cell=cell,
                    ),
                },
            ],
            "stream": False,
        }
        if self.spec.vendor == "DeepSeek":
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = self.spec.reasoning
        http_request = request.Request(
            f"{self.spec.base_url}/chat/completions",
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        started = time.monotonic()
        try:
            with request.urlopen(http_request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(
                f"{self.spec.name} API HTTP {exc.code}: {redact_text(body)}",
            ) from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"{self.spec.name} API request failed: {redact_text(str(exc))}",
            ) from exc
        duration_ms = round((time.monotonic() - started) * 1000)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"{self.spec.name} API response had no message content",
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"{self.spec.name} API returned empty content")
        metadata = {
            "model": data.get("model", self.spec.model),
            "usage": data.get("usage", {}),
            "response_id": data.get("id", ""),
            "transport": "openai_compatible",
            "reasoning": self.spec.reasoning,
        }
        return _result(self.spec, content, duration_ms, metadata)


class MockAdapter:
    def __init__(self, spec: AgentSpec) -> None:
        self.spec = spec

    def invoke(
        self,
        *,
        task: str,
        context: str,
        workspace: Path,
        timeout: int,
        plugins: list[str],
        cell: str,
    ) -> ProviderResult:
        del context, workspace, timeout, cell
        plugin_lines = "\n".join(f"- {item}" for item in plugins) or "- feishu-board"
        return ProviderResult(
            agent=self.spec.name,
            text=(
                f"## Findings\nMock {self.spec.display} covered the authorized workplace.\n\n"
                "## Evidence\nNo external provider was contacted.\n\n"
                "## Uncertainty\nIntegration-path test only.\n\n"
                f"## Board update\n{self.spec.display} is on station for: {task[:160]}\n\n"
                f"## Plugins\n{plugin_lines}\n"
            ),
            duration_ms=0,
            plugins=list(plugins),
            metadata={"mock": True},
        )


def make_adapter(spec: AgentSpec, *, mock: bool = False) -> Any:
    if mock:
        return MockAdapter(spec)
    if spec.backend == "cursor":
        return CursorAdapter(spec)
    if spec.backend == "openai_compatible":
        return OpenAICompatibleAdapter(spec)
    raise ValueError(f"unsupported backend for {spec.name}")
