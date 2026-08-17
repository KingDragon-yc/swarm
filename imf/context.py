"""Per-operative context budget, board excerpt, lessons, and session clear.

The Feishu board is durable. The chat is not. Compact only after the current
turn has been posted. After a task, write lessons into AGENTS.md and drop the
session (/clear or a new conversation — IMF never resumes).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .config import AgentSpec
from .secrets import redact


_CHARS_PER_TOKEN = 4
_BOARD_FRACTION = 0.05
_MIN_BOARD_CHARS = 8_000
_MAX_BOARD_CHARS = {
    "observe": 120_000,
    "orient": 120_000,
    "decide": 80_000,
    "act": 48_000,
}


def utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def board_budget_chars(spec: AgentSpec) -> int:
    """How much board text this operative may load into a turn."""
    raw = int(spec.context_tokens * _CHARS_PER_TOKEN * _BOARD_FRACTION)
    cap = _MAX_BOARD_CHARS.get(spec.ooda, 80_000)
    return max(_MIN_BOARD_CHARS, min(raw, cap))


def excerpt_board(board: str, spec: AgentSpec) -> tuple[str, bool]:
    """Slice the already-saved board to fit this operative's window.

    Compaction never drops unpublished work: callers must post to the board
    before the next excerpt.
    """
    budget = board_budget_chars(spec)
    text = board.replace("\r\n", "\n").strip()
    if len(text) <= budget:
        return text, False
    header = ""
    rest = text
    marker = "## Log"
    if marker in text:
        head, tail = text.split(marker, 1)
        header = head.rstrip() + "\n\n" + marker + "\n\n"
        rest = tail.lstrip()
    keep = budget - len(header) - 90
    if keep < 2_000:
        header = ""
        keep = budget - 90
    excerpt = (
        header
        + "…[compacted; full log is on the Feishu board / board.md]…\n\n"
        + rest[-keep:]
    )
    return excerpt, True


def extract_section(text: str, heading: str) -> str:
    needle = heading.strip().lower()
    lines = text.replace("\r\n", "\n").splitlines()
    collecting = False
    body: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower() == needle:
            collecting = True
            continue
        if collecting and stripped.startswith("## "):
            break
        if collecting:
            body.append(line)
    return "\n".join(body).strip()


def extract_lessons(text: str) -> str:
    for heading in ("## Lessons", "## Experience", "## 经验"):
        found = extract_section(text, heading)
        if found:
            return found[:1_200]
    return ""


def append_notes(markdown: str, lesson: str, *, stamp: str) -> str:
    """Append a dated lesson under ``## Notes`` without rewriting other sections."""
    cleaned = lesson.strip()
    if not cleaned:
        return markdown
    bullet = f"- `{stamp}` {cleaned}"
    text = markdown.replace("\r\n", "\n")
    if "## Notes" not in text:
        return text.rstrip() + "\n\n## Notes\n\n" + bullet + "\n"
    lines = text.splitlines()
    start = None
    end = len(lines)
    for index, line in enumerate(lines):
        if line.strip() == "## Notes":
            start = index
            continue
        if start is not None and index > start and line.startswith("## "):
            end = index
            break
    if start is None:
        return text.rstrip() + "\n\n## Notes\n\n" + bullet + "\n"
    notes = lines[start:end]
    while notes and notes[-1].strip() == "":
        notes.pop()
    notes.append(bullet)
    notes.append("")
    return "\n".join(lines[:start] + notes + lines[end:]).rstrip() + "\n"


def clear_session(cell: Path, *, metadata: dict[str, Any], task: str) -> dict[str, Any]:
    """Drop the chat. Next turn is a new conversation, never --resume."""
    record = redact(
        {
            "cleared_at": utc_stamp(),
            "method": "new_conversation",
            "last_session_id": str(metadata.get("session_id") or ""),
            "last_task": task[:240],
        },
    )
    path = cell / "session.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record
