"""Uniform, read-only adapters for Cursor CLI and OpenAI-compatible APIs."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import re
from typing import Any, Protocol
from urllib import error, request

from .config import AgentSpec
from .core import redact_text


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CURSOR_MODEL_PREFERENCES: dict[str, tuple[str, ...]] = {
    "claude": (
        "claude-opus-5-thinking-high",
        "claude-sonnet-5-thinking-high",
        "claude-opus-4-8-thinking-high",
        "claude-opus-5-high",
        "claude-sonnet-5-high",
    ),
    "gemini": (
        "gemini-3.6-flash-high",
        "gemini-3.1-pro",
        "gemini-3.6-flash-medium",
        "gemini-3.6-flash-low",
        "gemini-3.6-flash-minimal",
        "gemini-3.5-flash",
    ),
    "grok": (
        "cursor-grok-4.6-high",
        "cursor-grok-4.5-high",
        "cursor-grok-4.6-medium",
        "cursor-grok-4.5-medium",
    ),
}
_CURSOR_MODEL_PREFIXES = {
    "claude": "claude-",
    "gemini": "gemini-",
    "grok": "cursor-grok-",
}


@dataclass(frozen=True, slots=True)
class ProviderResult:
    agent: str
    text: str
    duration_ms: int
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


def specialist_prompt(spec: AgentSpec, task: str, context: str) -> str:
    """Build a bounded handoff that asks for evidence, not hidden reasoning."""
    envelope = {
        "role": f"{spec.name}, read-only specialist for a Codex/GPT orchestrator",
        "authorization": "authorized CTF or SRC/security-research work only",
        "specialty": spec.role,
        "task": task,
        "shared_run_context": context[-8_000:],
        "constraints": [
            "do not modify files",
            "do not send external messages",
            "do not start other agents",
            "do not broaden target scope",
            "keep private chain-of-thought private",
            "cite filenames, offsets, commands, or request shapes as evidence",
            "label hypotheses and uncertainty",
        ],
        "required_headings": [
            "## Findings",
            "## Evidence",
            "## Uncertainty",
            "## Suggested next action",
        ],
    }
    # A single-line JSON envelope survives Windows .cmd/PowerShell argument
    # forwarding much more reliably than a multi-line prompt.
    return "Execute this task envelope and return the requested report: " + json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _cursor_executable() -> str:
    executable = shutil.which("agent.cmd") or shutil.which("agent")
    if not executable:
        raise RuntimeError("Cursor Agent CLI was not found on PATH")
    return executable


def parse_cursor_model_list(output: str) -> list[str]:
    """Parse stable model IDs from Cursor's human-readable model list."""
    models: list[str] = []
    for raw_line in output.splitlines():
        line = _ANSI_ESCAPE_RE.sub("", raw_line).strip()
        if " - " not in line:
            continue
        model_id = line.split(" - ", 1)[0].strip()
        if model_id and model_id not in models:
            models.append(model_id)
    return models


def list_cursor_models(*, timeout: int = 30) -> list[str]:
    """Query the current Cursor account's model catalog."""
    completed = subprocess.run(
        [_cursor_executable(), "--list-models"],
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
    """Rank usable models for one vendor-specific Cursor worker."""
    available_set = set(available)
    ranked = [
        model
        for model in _CURSOR_MODEL_PREFERENCES.get(agent, ())
        if model in available_set
    ]
    prefix = _CURSOR_MODEL_PREFIXES.get(agent, "")
    for model in available:
        if prefix and model.startswith(prefix) and model not in ranked:
            ranked.append(model)
    return ranked


def resolve_cursor_model(spec: AgentSpec, available: list[str]) -> tuple[str, str]:
    """Resolve an explicit pin or the best current model for ``auto``."""
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
    """Resolve Cursor's bundled Node entrypoint, bypassing lossy .cmd quoting."""
    executable = Path(_cursor_executable()).resolve()
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


def validate_specialist_report(text: str) -> None:
    missing = [
        heading
        for heading in ("## Findings", "## Evidence", "## Uncertainty", "## Suggested next action")
        if heading not in text
    ]
    if missing:
        raise RuntimeError(
            "Specialist violated the report contract; missing " + ", ".join(missing),
        )


def parse_cursor_json(stdout: str) -> tuple[str, dict[str, Any]]:
    """Extract the terminal result from Cursor's json or JSONL output."""
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


class CursorAdapter:
    """Invoke one Cursor-hosted model in read-only Ask mode."""

    def __init__(self, spec: AgentSpec) -> None:
        if spec.backend != "cursor":
            raise ValueError(f"{spec.name} is not a Cursor agent")
        self.spec = spec

    def invoke(
        self,
        *,
        task: str,
        context: str,
        workspace: Path,
        timeout: int,
    ) -> ProviderResult:
        prompt = specialist_prompt(self.spec, task, context)
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
                env={
                    **os.environ,
                    "NO_COLOR": "1",
                    "CURSOR_INVOKED_AS": "agent",
                },
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Cursor agent {self.spec.name} timed out after {timeout}s",
            ) from exc
        duration_ms = round((time.monotonic() - started) * 1000)
        if completed.returncode != 0:
            diagnostic = redact_text((completed.stderr or completed.stdout)[-2000:])
            raise RuntimeError(
                f"Cursor agent {self.spec.name} exited {completed.returncode}: "
                f"{diagnostic}",
            )
        text, metadata = parse_cursor_json(completed.stdout)
        validate_specialist_report(text)
        metadata["configured_model"] = self.spec.model
        metadata["resolved_model"] = selected_model
        metadata["model_selection"] = selection
        return ProviderResult(
            agent=self.spec.name,
            text=redact_text(text),
            duration_ms=duration_ms,
            metadata=metadata,
        )


class OpenAICompatibleAdapter:
    """Call a provider's OpenAI-compatible chat-completions endpoint."""

    def __init__(self, spec: AgentSpec) -> None:
        if spec.backend != "openai_compatible":
            raise ValueError(f"{spec.name} is not an API agent")
        self.spec = spec

    def invoke(
        self,
        *,
        task: str,
        context: str,
        workspace: Path,
        timeout: int,
    ) -> ProviderResult:
        del workspace  # API workers only see the explicitly supplied context.
        api_key = os.getenv(self.spec.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(
                f"{self.spec.name} is missing {self.spec.api_key_env}",
            )
        if not self.spec.model:
            raise RuntimeError(f"{self.spec.name} model is not configured")

        payload = {
            "model": self.spec.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a read-only specialist in an authorized CTF or "
                        "SRC workflow. Do not reveal private chain-of-thought."
                    ),
                },
                {
                    "role": "user",
                    "content": specialist_prompt(self.spec, task, context),
                },
            ],
            "temperature": 0.1,
            "stream": False,
        }
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
        validate_specialist_report(content)
        metadata = {
            "model": data.get("model", self.spec.model),
            "usage": data.get("usage", {}),
            "response_id": data.get("id", ""),
        }
        return ProviderResult(
            agent=self.spec.name,
            text=redact_text(content.strip()),
            duration_ms=duration_ms,
            metadata=metadata,
        )


class MockAdapter:
    """Deterministic adapter used by the offline demo and unit tests."""

    def __init__(self, spec: AgentSpec) -> None:
        self.spec = spec

    def invoke(
        self,
        *,
        task: str,
        context: str,
        workspace: Path,
        timeout: int,
    ) -> ProviderResult:
        del context, workspace, timeout
        return ProviderResult(
            agent=self.spec.name,
            text=(
                "## Findings\nMock specialist received the bounded task.\n\n"
                "## Evidence\nNo external provider was contacted.\n\n"
                "## Uncertainty\nThis is an integration-path test only.\n\n"
                f"## Suggested next action\nCodex should evaluate: {task[:160]}"
            ),
            duration_ms=0,
            metadata={"mock": True},
        )


def make_adapter(spec: AgentSpec, *, mock: bool = False) -> ProviderAdapter:
    if mock:
        return MockAdapter(spec)
    if spec.backend == "cursor":
        return CursorAdapter(spec)
    if spec.backend == "openai_compatible":
        return OpenAICompatibleAdapter(spec)
    raise ValueError("The GPT orchestrator cannot be delegated to itself")
