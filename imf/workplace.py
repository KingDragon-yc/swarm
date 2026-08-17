"""Per-target workplace: shared large area plus four operative cells."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
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
_SAFE_MISSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_ATTACHMENT_FILES = 5_000
_MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024


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
        self.lock_path = self.path / ".workplace.lock"
        self._thread_lock = threading.RLock()

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

    @contextmanager
    def locked(self):
        """Serialize updates across threads and independent process objects."""
        with self._thread_lock:
            self.path.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open("a+b") as stream:
                if self.lock_path.stat().st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if os.name == "nt":
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def read_state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def write_state(self, state: dict[str, Any]) -> None:
        with self.locked():
            self._write_state_unlocked(state)

    def _write_state_unlocked(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".json.tmp")
        payload = json.dumps(redact(state), ensure_ascii=False, indent=2) + "\n"
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self.state_path)

    def update_state(self, **updates: Any) -> dict[str, Any]:
        with self.locked():
            state = self.read_state()
            state.update(redact(updates))
            self._write_state_unlocked(state)
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
        with self.locked():
            with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def events(self) -> list[dict[str, Any]]:
        if not self.events_path.is_file():
            return []
        with self.locked():
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
        with self.locked():
            self.board_path.write_text(redact_text(markdown).rstrip() + "\n", encoding="utf-8")

    def append_board(self, markdown: str) -> None:
        entry = redact_text(markdown).rstrip()
        with self.locked():
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
        with self.locked():
            if path.is_file():
                text = apply_plugins(path.read_text(encoding="utf-8"), plugins)
            else:
                text = render_agents_md(spec, plugins)
            path.write_text(text, encoding="utf-8")
            return text

    def append_lessons(self, agent: str, lesson: str) -> str:
        path = self.agents_md(agent)
        with self.locked():
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
            "presence": state.get("presence", {}),
            "workspace": str(self.path),
            "cells": cells,
            "board": self.read_board(),
            "mission": self.mission_path.read_text(encoding="utf-8") if self.mission_path.is_file() else "",
        }


def _copy_attachments(source: Path, destination: Path) -> list[str]:
    copied: list[str] = []
    source = source.resolve()
    destination = destination.resolve()
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("attachments source cannot contain the workplace destination")
    if source.is_file():
        if source.name.startswith("."):
            return copied
        target = destination / source.name
        shutil.copy2(source, target)
        copied.append(source.name)
        return copied
    if not source.is_dir():
        raise FileNotFoundError(f"attachments path not found: {source}")
    files: list[Path] = []
    directories: list[Path] = []
    total_bytes = 0
    for root, children, filenames in os.walk(source, topdown=True, followlinks=False):
        root_path = Path(root)
        children[:] = sorted(children)
        filenames[:] = sorted(filenames)
        children[:] = [name for name in children if not name.startswith(".")]
        for name in children:
            child = root_path / name
            if child.is_symlink():
                raise ValueError(f"symlinked attachment directory is not allowed: {child}")
            directories.append(child)
        for name in filenames:
            if name.startswith("."):
                continue
            child = root_path / name
            if child.is_symlink():
                raise ValueError(f"symlinked attachment file is not allowed: {child}")
            if not child.is_file():
                continue
            files.append(child)
            total_bytes += child.stat().st_size
            if len(files) > _MAX_ATTACHMENT_FILES or total_bytes > _MAX_ATTACHMENT_BYTES:
                raise ValueError(
                    f"attachments exceed limits ({_MAX_ATTACHMENT_FILES} files / "
                    f"{_MAX_ATTACHMENT_BYTES} bytes)",
                )
    for directory in directories:
        (destination / directory.relative_to(source)).mkdir(parents=True, exist_ok=True)
    for child in files:
        target = destination / child.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child, target)
    copied = sorted({path.relative_to(source).parts[0] for path in files + directories})
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
    root = settings.workplaces_dir.resolve()
    path = (root / ident).resolve()
    if path.parent != root or not _SAFE_MISSION_ID_RE.fullmatch(ident):
        raise ValueError("mission id must stay directly inside IMF_WORKPLACES")
    path.mkdir(parents=True, exist_ok=False)
    workplace = Workplace(path)
    try:
        workplace.attachments.mkdir()
        copied: list[str] = []
        if attachments is not None:
            raw_source = Path(attachments)
            if raw_source.is_symlink():
                raise ValueError("symlinked attachments source is not allowed")
            copied = _copy_attachments(raw_source, workplace.attachments)
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
                "presence": {},
            },
        )
        workplace.append_event(
            actor="imf",
            kind="mission.created",
            summary=f"Opened workplace {ident}",
            details={"title": title, "attachments": copied},
        )
        return workplace
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise


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
    if not _SAFE_MISSION_ID_RE.fullmatch(mission_id):
        raise FileNotFoundError(f"Unknown mission: {mission_id}")
    root = settings.workplaces_dir.resolve()
    path = (root / mission_id).resolve()
    if path.parent != root:
        raise FileNotFoundError(f"Unknown mission: {mission_id}")
    workplace = Workplace(path)
    if not workplace.state_path.is_file():
        raise FileNotFoundError(f"Unknown mission: {mission_id}")
    state = workplace.read_state()
    if state.get("id") not in {None, "", mission_id}:
        raise FileNotFoundError(f"Unknown mission: {mission_id}")
    return workplace
