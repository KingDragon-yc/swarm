"""Per-target workplace: shared large area plus four operative cells."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import shutil
import threading
from typing import Any

from .config import AGENT_IDS, AgentSpec, Settings
from .context import append_notes, utc_stamp
from .plugins import apply_plugins, default_plugins, parse_plugins_markdown, render_agents_md
from .secrets import redact, redact_text


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(title: str) -> str:
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    return (slug[:48] or "mission").strip("-")


def new_mission_id(title: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{slugify(title)}-{stamp}-{secrets.token_hex(2)}"


class Workplace:
    """One authorized target's large area and four cells."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.state_path = self.path / "state.json"
        self.board_path = self.path / "board.md"
        self.mission_path = self.path / "mission.md"
        self.notes_path = self.path / "notes.md"
        self.attachments = self.path / "attachments"
        self.events_path = self.path / "events.jsonl"
        self._lock = threading.Lock()

    @property
    def mission_id(self) -> str:
        return self.path.name

    def cell(self, agent: str) -> Path:
        if agent not in AGENT_IDS:
            raise ValueError(f"unknown operative: {agent}")
        return self.path / agent

    def agents_md(self, agent: str) -> Path:
        return self.cell(agent) / "AGENTS.md"

    def cell_notes(self, agent: str) -> Path:
        return self.cell(agent) / "notes.md"

    def session_path(self, agent: str) -> Path:
        return self.cell(agent) / "session.json"

    def read_state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def write_state(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".json.tmp")
        payload = json.dumps(redact(state), ensure_ascii=False, indent=2) + "\n"
        with self._lock:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self.state_path)

    def update_state(self, **updates: Any) -> dict[str, Any]:
        state = self.read_state()
        state.update(redact(updates))
        self.write_state(state)
        return state

    def append_event(
        self,
        *,
        actor: str,
        kind: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = redact(
            {
                "time": utc_now(),
                "mission_id": self.mission_id,
                "actor": actor,
                "kind": kind,
                "summary": summary,
                "details": details or {},
            },
        )
        with self._lock:
            with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def events(self) -> list[dict[str, Any]]:
        if not self.events_path.is_file():
            return []
        rows = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def read_board(self) -> str:
        if not self.board_path.is_file():
            return ""
        return self.board_path.read_text(encoding="utf-8")

    def write_board(self, markdown: str) -> None:
        with self._lock:
            self.board_path.write_text(redact_text(markdown).rstrip() + "\n", encoding="utf-8")

    def append_board(self, markdown: str) -> None:
        entry = redact_text(markdown).rstrip()
        with self._lock:
            current = ""
            if self.board_path.is_file():
                current = self.board_path.read_text(encoding="utf-8").rstrip()
            self.board_path.write_text(current + "\n\n" + entry + "\n", encoding="utf-8")

    def plugins_for(self, agent: str) -> list[str]:
        path = self.agents_md(agent)
        if not path.is_file():
            return list(default_plugins(agent))
        found = parse_plugins_markdown(path.read_text(encoding="utf-8"))
        return found or list(default_plugins(agent))

    def set_plugins(self, agent: str, plugins: list[str], spec: AgentSpec) -> str:
        path = self.agents_md(agent)
        if path.is_file():
            text = apply_plugins(path.read_text(encoding="utf-8"), plugins)
        else:
            text = render_agents_md(spec, plugins)
        path.write_text(text, encoding="utf-8")
        return text

    def append_lessons(self, agent: str, lesson: str) -> str:
        path = self.agents_md(agent)
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        updated = append_notes(current, lesson, stamp=utc_stamp())
        path.write_text(updated, encoding="utf-8")
        return updated

    def public_view(self) -> dict[str, Any]:
        state = self.read_state()
        cells = {}
        for agent in AGENT_IDS:
            session = self.session_path(agent)
            cells[agent] = {
                "plugins": self.plugins_for(agent),
                "agents_md": str(self.agents_md(agent)),
                "notes": self.cell_notes(agent).is_file(),
                "session_cleared": session.is_file(),
            }
        return {
            "id": self.mission_id,
            "title": state.get("title", self.mission_id),
            "status": state.get("status", "unknown"),
            "created_at": state.get("created_at", ""),
            "document_id": state.get("document_id", ""),
            "feishu_url": state.get("feishu_url", ""),
            "workspace": str(self.path),
            "cells": cells,
            "board": self.read_board(),
            "mission": self.mission_path.read_text(encoding="utf-8") if self.mission_path.is_file() else "",
        }


def _copy_attachments(source: Path, destination: Path) -> list[str]:
    copied: list[str] = []
    if source.is_file():
        target = destination / source.name
        shutil.copy2(source, target)
        copied.append(source.name)
        return copied
    if not source.is_dir():
        raise FileNotFoundError(f"attachments path not found: {source}")
    for child in sorted(source.iterdir()):
        if child.name.startswith("."):
            continue
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)
        copied.append(child.name)
    return copied


def create_workplace(
    settings: Settings,
    *,
    title: str,
    mission: str,
    attachments: Path | None = None,
    mission_id: str | None = None,
) -> Workplace:
    ident = mission_id or new_mission_id(title)
    path = settings.workplaces_dir / ident
    path.mkdir(parents=True, exist_ok=False)
    workplace = Workplace(path)
    workplace.attachments.mkdir()
    copied: list[str] = []
    if attachments is not None:
        copied = _copy_attachments(attachments.resolve(), workplace.attachments)
    for agent in AGENT_IDS:
        spec = settings.agents[agent]
        cell = workplace.cell(agent)
        cell.mkdir()
        workplace.agents_md(agent).write_text(
            render_agents_md(spec, list(default_plugins(agent))),
            encoding="utf-8",
        )
        workplace.cell_notes(agent).write_text("", encoding="utf-8")
    workplace.mission_path.write_text(redact_text(mission).rstrip() + "\n", encoding="utf-8")
    workplace.notes_path.write_text(
        "# Notes\n\nShared notes for this authorized target.\n",
        encoding="utf-8",
    )
    workplace.write_board(
        "\n".join(
            [
                f"# IMF Board · {title}",
                "",
                f"- mission: `{ident}`",
                f"- status: `active`",
                f"- created: `{utc_now()}`",
                "",
                "## Mission",
                "",
                redact_text(mission).strip(),
                "",
                "## Log",
                "",
                "Four operatives write below. The Feishu document is the live copy;",
                "this file is refreshed when the mission pauses or finishes.",
                "",
            ],
        ),
    )
    workplace.write_state(
        {
            "schema_version": 1,
            "id": ident,
            "title": redact_text(title),
            "status": "active",
            "created_at": utc_now(),
            "document_id": "",
            "feishu_url": "",
            "attachments": copied,
        },
    )
    workplace.append_event(
        actor="imf",
        kind="mission.created",
        summary=f"Opened workplace {ident}",
        details={"title": title, "attachments": copied},
    )
    return workplace


def list_workplaces(settings: Settings) -> list[Workplace]:
    root = settings.workplaces_dir
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.iterdir(), key=lambda item: item.name, reverse=True):
        if path.is_dir() and (path / "state.json").is_file():
            found.append(Workplace(path))
    return found


def open_workplace(settings: Settings, mission_id: str) -> Workplace:
    if mission_id == "latest":
        listed = list_workplaces(settings)
        if not listed:
            raise FileNotFoundError("No IMF mission exists")
        return listed[0]
    path = settings.workplaces_dir / mission_id
    workplace = Workplace(path)
    if not workplace.state_path.is_file():
        raise FileNotFoundError(f"Unknown mission: {mission_id}")
    return workplace
