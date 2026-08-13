"""Routing policy and append-only collaboration ledger."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any, Iterable


_EXPLICIT_AGENT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(claude|gemini|grok|deepseek|kimi|glm|qwen)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_COMPLEX_TERMS = {
    "多阶段",
    "攻击链",
    "利用链",
    "交叉复核",
    "独立复核",
    "并行",
    "竞态",
    "反序列化",
    "沙箱逃逸",
    "内核",
    "二进制",
    "pwn",
    "crypto",
    "复杂",
    "全项目审计",
    "source review",
    "exploit chain",
    "race condition",
}
_REVIEW_TERMS = {"复核", "审计", "反例", "验证", "review", "verify"}
_ROUTE_SPECIALTIES: tuple[tuple[set[str], str], ...] = (
    ({"代码", "源码", "审计", "漏洞", "review", "source"}, "claude"),
    ({"大量", "长上下文", "跨文件", "日志", "pcap", "材料"}, "gemini"),
    ({"反例", "绕过", "另类", "对抗", "攻击面"}, "grok"),
    ({"ctf", "利用", "漏洞链", "web", "pwn", "crypto"}, "deepseek"),
    ({"中文", "报告", "长文", "归纳"}, "kimi"),
    ({"脚本", "编码", "实现", "python", "java"}, "qwen"),
)
_SECRET_ENV_NAMES = (
    "FEISHU_APP_SECRET",
    "DEEPSEEK_API_KEY",
    "SILICONFLOW_API_KEY",
    "KIMI_API_KEY",
    "GLM_API_KEY",
    "QWEN_API_KEY",
    "CURSOR_API_KEY",
)
_TOKEN_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|app[_-]?secret|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
)
_EVENT_LABELS = {
    "run.created": "任务建立",
    "routing.recommended": "路由判断",
    "orchestrator.launch_config": "启动配置",
    "orchestrator.note": "进展记录",
    "delegation.started": "委派开始",
    "delegation.completed": "专家回报",
    "delegation.failed": "委派失败",
    "feishu.sync_failed": "飞书同步失败",
    "feishu.doc_rebuilt": "飞书文档重建",
    "run.completed": "任务完成",
    "run.failed": "任务失败",
}
_HIGHLIGHT_KINDS = {
    "orchestrator.note",
    "delegation.completed",
    "run.completed",
    "run.failed",
}


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """A conservative recommendation; Codex remains the final decision maker."""

    mode: str
    suggested_agents: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["suggested_agents"] = list(self.suggested_agents)
        data["reasons"] = list(self.reasons)
        return data


def _first_specialist(task_lower: str) -> str:
    for terms, name in _ROUTE_SPECIALTIES:
        if any(term in task_lower for term in terms):
            return name
    return "deepseek"


def route_task(task: str) -> RouteDecision:
    """Recommend solo execution or a narrowly scoped delegation.

    This deliberately prefers solo work. A long prompt alone is not evidence
    that seven extra models will improve the result.
    """
    normalized = " ".join(task.split())
    lower = normalized.lower()
    explicit = []
    for match in _EXPLICIT_AGENT_RE.finditer(lower):
        name = match.group(1).lower()
        if name not in explicit:
            explicit.append(name)

    if explicit:
        return RouteDecision(
            mode="delegate",
            suggested_agents=tuple(explicit[:2]),
            reasons=("任务显式点名了专家模型",),
        )

    complex_hits = sorted(term for term in _COMPLEX_TERMS if term in lower)
    review_hits = sorted(term for term in _REVIEW_TERMS if term in lower)
    if not complex_hits:
        return RouteDecision(
            mode="solo",
            suggested_agents=(),
            reasons=("未发现必须引入外部专家的证据缺口或专长缺口",),
        )

    primary = _first_specialist(lower)
    suggestions = [primary]
    reasons = ["检测到复杂或高风险特征：" + "、".join(complex_hits[:3])]
    if review_hits and primary != "grok":
        suggestions.append("grok")
        reasons.append("任务同时要求独立反例或复核")
    return RouteDecision(
        mode="delegate",
        suggested_agents=tuple(suggestions[:2]),
        reasons=tuple(reasons),
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact_text(value: str) -> str:
    """Mask configured credentials and common inline token forms."""
    redacted = value
    for env_name in _SECRET_ENV_NAMES:
        secret = os.getenv(env_name, "")
        if secret and len(secret) >= 6:
            redacted = redacted.replace(secret, f"<redacted:{env_name}>")
    for pattern in _TOKEN_PATTERNS:
        redacted = pattern.sub("<redacted:credential>", redacted)
    return redacted


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _event_label(kind: str) -> str:
    return _EVENT_LABELS.get(kind, kind)


def _format_scalar(value: Any) -> str:
    if value is None:
        return "(empty)"
    if isinstance(value, bool):
        return "true" if value else "false"
    return redact_text(str(value))


def _append_markdown_detail(lines: list[str], key: str, value: Any, level: int = 0) -> None:
    indent = "  " * level
    if isinstance(value, dict):
        lines.append(f"{indent}- {key}:")
        if not value:
            lines.append(f"{indent}  - (empty)")
            return
        for child_key, child_value in value.items():
            _append_markdown_detail(lines, str(child_key), child_value, level + 1)
        return
    if isinstance(value, (list, tuple)):
        lines.append(f"{indent}- {key}:")
        if not value:
            lines.append(f"{indent}  - (empty)")
            return
        for item in value:
            if isinstance(item, (dict, list, tuple)):
                _append_markdown_detail(lines, "item", item, level + 1)
            else:
                lines.append(f"{indent}  - {_format_scalar(item)}")
        return
    text = _format_scalar(value)
    if "\n" in text or len(text) > 160:
        lines.append(f"{indent}- {key}:")
        lines.append("")
        lines.append("```text")
        lines.extend(text.splitlines() or ["(empty)"])
        lines.append("```")
        return
    lines.append(f"{indent}- {key}: {text}")


def _append_text_detail(lines: list[str], key: str, value: Any, level: int = 0) -> None:
    indent = "  " * level
    if isinstance(value, dict):
        lines.append(f"{indent}- {key}:")
        if not value:
            lines.append(f"{indent}  (empty)")
            return
        for child_key, child_value in value.items():
            _append_text_detail(lines, str(child_key), child_value, level + 1)
        return
    if isinstance(value, (list, tuple)):
        lines.append(f"{indent}- {key}:")
        if not value:
            lines.append(f"{indent}  (empty)")
            return
        for item in value:
            if isinstance(item, (dict, list, tuple)):
                _append_text_detail(lines, "item", item, level + 1)
            else:
                lines.append(f"{indent}  - {_format_scalar(item)}")
        return
    text = _format_scalar(value)
    if "\n" in text or len(text) > 160:
        lines.append(f"{indent}- {key}:")
        for part in text.splitlines() or ["(empty)"]:
            lines.append(f"{indent}    {part}")
        return
    lines.append(f"{indent}- {key}: {text}")


def _collect_highlights(events: list[dict[str, Any]], limit: int = 5) -> list[str]:
    seen: set[str] = set()
    selected: list[str] = []
    for event in reversed(events):
        if event.get("kind") not in _HIGHLIGHT_KINDS:
            continue
        summary = str(event.get("summary", "")).strip()
        if not summary or summary in seen:
            continue
        seen.add(summary)
        selected.append(summary)
        if len(selected) >= limit:
            break
    selected.reverse()
    return selected


class EventLedger:
    """Append-only local source of truth with a human-readable projection."""

    def __init__(self, state_dir: Path, run_id: str) -> None:
        self.state_dir = state_dir.resolve()
        self.run_id = run_id
        self.run_dir = self.state_dir / "runs" / run_id
        self.state_path = self.run_dir / "state.json"
        self.events_path = self.run_dir / "events.jsonl"
        self.brief_path = self.run_dir / "brief.md"

    @classmethod
    def create(
        cls,
        state_dir: Path,
        task: str,
        workspace: Path,
    ) -> "EventLedger":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{stamp}-{secrets.token_hex(3)}"
        ledger = cls(state_dir, run_id)
        ledger.run_dir.mkdir(parents=True, exist_ok=False)
        state = {
            "schema_version": 1,
            "run_id": run_id,
            "task": redact_text(task),
            "workspace": str(workspace.resolve()),
            "status": "active",
            "created_at": utc_now(),
            "document_id": "",
        }
        ledger._write_state(state)
        ledger.append(
            actor="gpt",
            kind="run.created",
            summary="Codex orchestrator created the run",
            details={"task": task, "workspace": str(workspace.resolve())},
        )
        return ledger

    @classmethod
    def open(cls, state_dir: Path, run_id: str) -> "EventLedger":
        ledger = cls(state_dir, run_id)
        if not ledger.state_path.is_file():
            raise FileNotFoundError(f"Unknown run_id: {run_id}")
        return ledger

    def read_state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _write_state(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(_redact(state), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    def update_state(self, **updates: Any) -> dict[str, Any]:
        state = self.read_state()
        state.update(_redact(updates))
        self._write_state(state)
        return state

    def append(
        self,
        *,
        actor: str,
        kind: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = _redact(
            {
                "schema_version": 1,
                "time": utc_now(),
                "run_id": self.run_id,
                "actor": actor,
                "kind": kind,
                "summary": summary,
                "details": details or {},
            },
        )
        with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._render_brief()
        return event

    def events(self) -> list[dict[str, Any]]:
        if not self.events_path.is_file():
            return []
        rows = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def render_event_markdown(
        self,
        event: dict[str, Any],
        *,
        index: int | None = None,
    ) -> str:
        number = f"{index}. " if index is not None else ""
        lines = [f"### {number}{_event_label(event['kind'])}", ""]
        lines.append(f"- 时间: `{event['time']}`")
        lines.append(f"- 角色: `{event['actor']}`")
        lines.append(f"- 事件: `{event['kind']}`")
        lines.extend(["", event["summary"], ""])
        details = event.get("details") or {}
        if details:
            lines.extend(["**细节**", ""])
            for key, value in details.items():
                _append_markdown_detail(lines, str(key), value)
            lines.append("")
        return "\n".join(lines).rstrip()

    def render_event_text(
        self,
        event: dict[str, Any],
        *,
        index: int | None = None,
    ) -> str:
        label = _event_label(event["kind"])
        header = f"步骤 {index} · {label}" if index is not None else label
        lines = [header, f"时间: {event['time']}", f"角色: {event['actor']}", f"事件: {event['kind']}", "", f"摘要: {event['summary']}"]
        details = event.get("details") or {}
        if details:
            lines.extend(["", "细节:"])
            for key, value in details.items():
                _append_text_detail(lines, str(key), value)
        lines.extend(["", "---", ""])
        return "\n".join(lines).rstrip()

    def render_wp_markdown(self) -> str:
        state = self.read_state()
        events = self.events()
        lines = [
            f"# 协作 WP · {self.run_id}",
            "",
            "## 任务范围",
            "",
            f"- 状态: `{state['status']}`",
            f"- 工作区: `{state['workspace']}`",
            f"- 建立时间: `{state.get('created_at', '(unknown)')}`",
            f"- 飞书文档: `{state.get('document_id') or '(未设置)'}`",
            f"- 本地账本: `{self.events_path}`",
            f"- 本地投影: `{self.brief_path}`",
            "",
            "### 目标",
            "",
            state["task"],
            "",
        ]
        highlights = _collect_highlights(events)
        if highlights:
            lines.extend(["## 当前判断", ""])
            for item in highlights:
                lines.append(f"- {item}")
            lines.append("")
        lines.extend(["## 逐次进展", ""])
        if not events:
            lines.append("暂无事件。")
        for index, event in enumerate(events, start=1):
            lines.append(self.render_event_markdown(event, index=index))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def render_wp_text(self) -> str:
        state = self.read_state()
        events = self.events()
        lines = [
            f"协作 WP / {self.run_id}",
            "",
            "[任务范围]",
            f"状态: {state['status']}",
            f"工作区: {state['workspace']}",
            f"建立时间: {state.get('created_at', '(unknown)')}",
            f"飞书文档: {state.get('document_id') or '(未设置)'}",
            f"本地账本: {self.events_path}",
            f"本地投影: {self.brief_path}",
            "",
            "[目标]",
            state["task"],
            "",
        ]
        highlights = _collect_highlights(events)
        if highlights:
            lines.append("[当前判断]")
            for offset, item in enumerate(highlights, start=1):
                lines.append(f"{offset}. {item}")
            lines.append("")
        lines.append("[逐次进展]")
        if not events:
            lines.append("暂无事件。")
        else:
            for index, event in enumerate(events, start=1):
                lines.append(self.render_event_text(event, index=index))
        return "\n".join(lines).rstrip() + "\n"

    def _render_brief(self) -> None:
        self.brief_path.write_text(self.render_wp_markdown(), encoding="utf-8")

    def context(self, *, max_chars: int = 30_000) -> str:
        """Return a bounded collaboration history for a specialist prompt."""
        text = self.brief_path.read_text(encoding="utf-8")
        if len(text) <= max_chars:
            return text
        return text[-max_chars:]


def latest_run_id(state_dir: Path) -> str | None:
    runs_dir = state_dir.resolve() / "runs"
    if not runs_dir.is_dir():
        return None
    candidates: Iterable[Path] = (
        path for path in runs_dir.iterdir() if path.is_dir()
    )
    latest = max(candidates, key=lambda path: path.name, default=None)
    return latest.name if latest else None
