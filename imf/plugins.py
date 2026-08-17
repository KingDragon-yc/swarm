"""Plugin catalog and creator-mode selection.

DeepSeek Harness treats every capability as a plugin. IMF keeps that idea and
drops the Cordis runtime: a JSON catalog plus a ``## Plugins`` section in each
cell's AGENTS.md is the whole composition surface.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path


_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
_DEFAULTS = {
    "flash": ("feishu-board", "files", "search", "evidence", "report"),
    "pro": ("feishu-board", "files", "search", "http-observe", "evidence", "diff", "report"),
    "grok": ("feishu-board", "files", "search", "shell", "evidence", "report"),
    "luna": ("feishu-board", "files", "web", "evidence", "report"),
}


@dataclass(frozen=True, slots=True)
class Plugin:
    id: str
    title: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title, "summary": self.summary}


def load_catalog(path: Path) -> list[Plugin]:
    data = json.loads(path.read_text(encoding="utf-8"))
    plugins = []
    for item in data.get("plugins") or []:
        plugin_id = str(item.get("id", "")).strip()
        if not _PLUGIN_ID_RE.match(plugin_id):
            raise ValueError(f"invalid plugin id: {plugin_id}")
        plugins.append(
            Plugin(
                id=plugin_id,
                title=str(item.get("title", plugin_id)),
                summary=str(item.get("summary", "")),
            ),
        )
    if not plugins:
        raise ValueError(f"plugin catalog is empty: {path}")
    return plugins


def catalog_ids(catalog: list[Plugin]) -> set[str]:
    return {plugin.id for plugin in catalog}


def default_plugins(agent: str) -> tuple[str, ...]:
    return _DEFAULTS.get(agent, ("feishu-board", "files", "evidence"))


def parse_plugins_markdown(text: str) -> list[str]:
    """Read plugin ids from the ``## Plugins`` section of AGENTS.md."""
    lines = text.replace("\r\n", "\n").splitlines()
    collecting = False
    found: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower() == "## plugins":
            collecting = True
            continue
        if collecting and stripped.startswith("## "):
            break
        if not collecting or not stripped:
            continue
        if stripped.startswith("- "):
            item = stripped[2:].split()[0].strip("`").strip(",")
        else:
            item = stripped.split()[0].strip("`").strip(",")
        if item and item not in found and _PLUGIN_ID_RE.match(item):
            found.append(item)
    return found


def render_agents_md(
    *,
    agent: str,
    display: str,
    feature: str,
    role: str,
    plugins: list[str],
    notes: str = "",
) -> str:
    plugin_lines = "\n".join(f"- {item}" for item in plugins) or "- feishu-board"
    extra = notes.strip() or "This cell is owned by the operative. Enable plugins here or in the Web UI creator panel."
    return (
        f"# Agent: {display}\n\n"
        f"Operative id: `{agent}`\n\n"
        "## Feature\n\n"
        f"{feature}\n\n"
        f"{role}\n\n"
        "## Plugins\n\n"
        f"{plugin_lines}\n\n"
        "## Notes\n\n"
        f"{extra}\n"
    )


def apply_plugins(markdown: str, plugins: list[str]) -> str:
    """Replace the Plugins section while keeping Feature and Notes intact."""
    lines = markdown.replace("\r\n", "\n").splitlines()
    start = None
    end = len(lines)
    for index, line in enumerate(lines):
        if line.strip().lower() == "## plugins":
            start = index
            continue
        if start is not None and index > start and line.startswith("## "):
            end = index
            break
    block = ["## Plugins", ""] + [f"- {item}" for item in plugins] + [""]
    if start is None:
        return markdown.rstrip() + "\n\n" + "\n".join(block) + "\n"
    return "\n".join(lines[:start] + block + lines[end:]).rstrip() + "\n"


_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("shell", ("shell", "bash", "命令", "终端", "powershell")),
    ("web", ("web", "搜索", "文档", "fetch", "http://", "https://")),
    ("http-observe", ("http", "接口", "请求", "endpoint", "api", "observe")),
    ("search", ("grep", "搜索", "符号", "路由")),
    ("diff", ("diff", "对比", "差异")),
    ("report", ("报告", "src", "finding", "修复")),
    ("files", ("文件", "附件", "源码", "file")),
    ("evidence", ("证据", "offset", "evidence")),
)


def suggest_plugins(description: str, catalog: list[Plugin]) -> list[str]:
    """Keyword composer used by creator mode when no model is required."""
    known = catalog_ids(catalog)
    text = description.lower()
    selected = ["feishu-board"]
    for plugin_id, hints in _HINTS:
        if plugin_id in known and any(hint in text for hint in hints):
            if plugin_id not in selected:
                selected.append(plugin_id)
    if len(selected) == 1:
        for plugin_id in ("files", "evidence", "report"):
            if plugin_id in known:
                selected.append(plugin_id)
    return [item for item in selected if item in known]
