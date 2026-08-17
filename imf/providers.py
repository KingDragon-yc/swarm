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
from queue import Empty, Queue
from threading import Thread
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
    def check_online(self, *, timeout: int) -> str: ...

    def invoke(
        self,
        *,
        task: str,
        context: str,
        workspace: Path,
        timeout: int,
        poll_interval: int = 600,
        max_runtime: int = 7_200,
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
    cell_brief: str = "",
    compacted: bool = False,
) -> str:
    envelope = {
        "force": "IMF — Instant Message Force",
        "operative": spec.display,
        "id": spec.name,
        "ooda": spec.ooda,
        "context_tokens": spec.context_tokens,
        "feature": spec.feature,
        "style": spec.style,
        "duty": spec.duty,
        "authorization": "authorized SRC / lab scope only",
        "plugins": plugins,
        "cell": cell,
        "cell_brief": redact_text(cell_brief[-4_000:]),
        "task": redact_text(task),
        "board_excerpt": redact_text(context),
        "board_compacted": compacted,
        "constraints": [
            "stay inside the authorized workplace",
            "all reads, writes, and commands must stay inside this workplace; do not use parent paths or other drives",
            "do not include API keys, cookies, or secrets",
            "cite files, offsets, requests, or commands as evidence",
            "label hypotheses and uncertainty",
            "the Feishu board is durable memory; this chat will be /clear'd",
            "post everything that must survive before this turn ends",
            "after the task, include ## Lessons for AGENTS.md Notes",
            "to suggest tools, include a ## Plugins list of catalog ids; plugin changes require human approval",
        ],
        "required_headings": [
            "## Findings",
            "## Evidence",
            "## Uncertainty",
            "## Board update",
            "## Lessons",
        ],
    }
    return "Write the board update for this IMF envelope: " + json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def validate_operative_report(text: str) -> None:
    required = ("## Findings", "## Evidence", "## Uncertainty", "## Board update", "## Lessons")
    missing = [heading for heading in required if heading not in text]
    if missing:
        raise RuntimeError(
            "Operative violated the board report contract; missing " + ", ".join(missing),
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


def cursor_sandbox_mode() -> str:
    """Return the Cursor CLI execution mode supported by this host.

    Cursor's OS sandbox is available on macOS and Linux.  On Windows the CLI
    rejects ``--sandbox enabled`` and uses its allowlist mode instead; the
    active workplace and the prompt still provide the application boundary.
    """
    return "disabled" if os.name == "nt" else "enabled"


def _run_cursor_with_heartbeat(
    args: list[str],
    *,
    workspace: Path,
    timeout: int,
    poll_interval: int,
    max_runtime: int,
    env: dict[str, str],
) -> tuple[int, str, str, int, int]:
    """Run Cursor while extending the idle deadline when stream output arrives."""
    process = subprocess.Popen(
        args,
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    activity: Queue[bool] = Queue()

    def drain(stream: Any, target: list[str]) -> None:
        try:
            for line in iter(stream.readline, ""):
                target.append(line)
                activity.put(True)
        finally:
            stream.close()

    readers = [
        Thread(target=drain, args=(process.stdout, stdout_parts), daemon=True),
        Thread(target=drain, args=(process.stderr, stderr_parts), daemon=True),
    ]
    for reader in readers:
        reader.start()

    started = time.monotonic()
    idle_deadline = started + timeout
    hard_deadline = started + max_runtime
    next_poll = started + poll_interval
    heartbeat_count = 0
    activity_since_poll = False
    try:
        while process.poll() is None:
            now = time.monotonic()
            remaining = min(idle_deadline, hard_deadline) - now
            if remaining <= 0:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise RuntimeError(
                    f"Cursor agent timed out after {max_runtime}s "
                    f"({heartbeat_count} heartbeat extensions)",
                )
            wait_for = min(max(0.25, next_poll - now), remaining)
            try:
                activity.get(timeout=wait_for)
                activity_since_poll = True
            except Empty:
                pass
            if time.monotonic() >= next_poll:
                had_activity = activity_since_poll
                while True:
                    try:
                        activity.get_nowait()
                        had_activity = True
                    except Empty:
                        break
                if had_activity and process.poll() is None:
                    heartbeat_count += 1
                    idle_deadline = min(hard_deadline, time.monotonic() + timeout)
                activity_since_poll = False
                next_poll = time.monotonic() + poll_interval
    finally:
        for reader in readers:
            reader.join(timeout=5)

    return (
        int(process.returncode or 0),
        "".join(stdout_parts),
        "".join(stderr_parts),
        round((time.monotonic() - started) * 1000),
        heartbeat_count,
    )


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
    validate_operative_report(cleaned)
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

    def check_online(self, *, timeout: int) -> str:
        if _cursor_executable():
            models = list_cursor_models(timeout=min(timeout, 30))
            selected, _ = resolve_cursor_model(self.spec, models)
            return f"Cursor online ({selected})"
        if self.spec.base_url and os.getenv(self.spec.api_key_env, "").strip():
            return OpenAICompatibleAdapter(self.spec).check_online(timeout=timeout)
        raise RuntimeError(
            f"{self.spec.name} needs Cursor CLI on PATH, or CURSOR_API_BASE plus CURSOR_API_KEY",
        )

    def invoke(
        self,
        *,
        task: str,
        context: str,
        workspace: Path,
        timeout: int,
        poll_interval: int = 600,
        max_runtime: int = 7_200,
        plugins: list[str],
        cell: str,
        cell_brief: str = "",
        compacted: bool = False,
    ) -> ProviderResult:
        prompt = operative_prompt(
            self.spec,
            task=task,
            context=context,
            plugins=plugins,
            cell=cell,
            cell_brief=cell_brief,
            compacted=compacted,
        )
        if _cursor_executable():
            return self._cli(prompt, workspace, timeout, poll_interval, max_runtime)
        if self.spec.base_url and os.getenv(self.spec.api_key_env, "").strip():
            http = OpenAICompatibleAdapter(self.spec)
            return http.invoke(
                task=task,
                context=context,
                workspace=workspace,
                timeout=timeout,
                poll_interval=poll_interval,
                max_runtime=max_runtime,
                plugins=plugins,
                cell=cell,
                cell_brief=cell_brief,
                compacted=compacted,
            )
        raise RuntimeError(
            f"{self.spec.name} needs Cursor CLI on PATH, or CURSOR_API_BASE plus CURSOR_API_KEY",
        )

    def _cli(
        self,
        prompt: str,
        workspace: Path,
        timeout: int,
        poll_interval: int,
        max_runtime: int,
    ) -> ProviderResult:
        selected_model, selection = resolve_cursor_model(
            self.spec,
            list_cursor_models(timeout=min(timeout, 30)),
        )
        # New conversation every turn. Never --resume / --continue; that is /clear.
        args = [
            *_cursor_command(),
            "--print",
            "--output-format",
            "stream-json",
            "--stream-partial-output",
            "--model",
            selected_model,
            "--trust",
            "--force",
            "--sandbox",
            cursor_sandbox_mode(),
            "--workspace",
            str(workspace.resolve()),
            prompt,
        ]
        returncode, stdout, stderr, duration_ms, heartbeat_count = _run_cursor_with_heartbeat(
            args,
            workspace=workspace,
            timeout=timeout,
            poll_interval=poll_interval,
            max_runtime=max_runtime,
            env={**os.environ, "NO_COLOR": "1", "CURSOR_INVOKED_AS": "agent"},
        )
        if returncode != 0:
            diagnostic = redact_text((stderr or stdout)[-2000:])
            raise RuntimeError(
                f"Cursor agent {self.spec.name} exited {returncode}: {diagnostic}",
            )
        text, metadata = parse_cursor_json(stdout)
        metadata["configured_model"] = self.spec.model
        metadata["resolved_model"] = selected_model
        metadata["model_selection"] = selection
        metadata["transport"] = "cursor_cli"
        metadata["heartbeat_count"] = heartbeat_count
        metadata["poll_interval_seconds"] = poll_interval
        metadata["max_runtime_seconds"] = max_runtime
        return _result(self.spec, text, duration_ms, metadata)


class OpenAICompatibleAdapter:
    def __init__(self, spec: AgentSpec) -> None:
        self.spec = spec

    def check_online(self, *, timeout: int) -> str:
        api_key = os.getenv(self.spec.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"{self.spec.name} is missing {self.spec.api_key_env}")
        if not self.spec.base_url:
            raise RuntimeError(f"{self.spec.name} has no API base URL")
        http_request = request.Request(
            f"{self.spec.base_url}/models",
            method="GET",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        try:
            with request.urlopen(http_request, timeout=min(timeout, 30)):
                return f"{self.spec.name} API online"
        except error.HTTPError as exc:
            if exc.code in {404, 405}:
                return f"{self.spec.name} API reachable (models probe unsupported)"
            body = exc.read().decode("utf-8", errors="replace")[-500:]
            raise RuntimeError(
                f"{self.spec.name} API online check failed with HTTP {exc.code}: "
                f"{redact_text(body)}",
            ) from exc
        except (error.URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"{self.spec.name} API online check failed: {redact_text(str(exc))}",
            ) from exc

    def invoke(
        self,
        *,
        task: str,
        context: str,
        workspace: Path,
        timeout: int,
        poll_interval: int = 600,
        max_runtime: int = 7_200,
        plugins: list[str],
        cell: str,
        cell_brief: str = "",
        compacted: bool = False,
    ) -> ProviderResult:
        del workspace, poll_interval, max_runtime
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
                        f"You are {self.spec.display}, IMF {self.spec.ooda} "
                        f"({self.spec.context_tokens} token window). "
                        f"{self.spec.duty} "
                        "Write a board update. This chat will be cleared. "
                        "Do not reveal private chain-of-thought."
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
                        cell_brief=cell_brief,
                        compacted=compacted,
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

    def check_online(self, *, timeout: int) -> str:
        del timeout
        return f"Mock {self.spec.name} online"

    def invoke(
        self,
        *,
        task: str,
        context: str,
        workspace: Path,
        timeout: int,
        poll_interval: int = 600,
        max_runtime: int = 7_200,
        plugins: list[str],
        cell: str,
        cell_brief: str = "",
        compacted: bool = False,
    ) -> ProviderResult:
        del workspace, timeout, poll_interval, max_runtime, cell, cell_brief
        plugin_lines = "\n".join(f"- {item}" for item in plugins) or "- feishu-board"
        saw = ""
        if self.spec.ooda != "observe" and "### flash ·" in context:
            saw = "Saw flash on the board.\n"
        return ProviderResult(
            agent=self.spec.name,
            text=(
                f"## Findings\nMock {self.spec.display} ({self.spec.ooda}) "
                f"covered the authorized workplace.\n\n"
                "## Evidence\nNo external provider was contacted.\n\n"
                "## Uncertainty\nIntegration-path test only.\n\n"
                f"## Board update\n{saw}{self.spec.duty} Task: {task[:160]}\n\n"
                f"## Lessons\nKeep {self.spec.ooda} outputs on the board before /clear.\n\n"
                f"## Plugins\n{plugin_lines}\n"
            ),
            duration_ms=0,
            plugins=list(plugins),
            metadata={"mock": True, "compacted": compacted, "session_id": ""},
        )


def make_adapter(spec: AgentSpec, *, mock: bool = False) -> Any:
    if mock:
        return MockAdapter(spec)
    if spec.backend == "cursor":
        return CursorAdapter(spec)
    if spec.backend == "openai_compatible":
        return OpenAICompatibleAdapter(spec)
    raise ValueError(f"unsupported backend for {spec.name}")
