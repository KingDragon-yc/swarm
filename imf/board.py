"""Live Feishu board plus the local board.md snapshot."""

from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timezone

from .config import Settings
from .feishu import FeishuClient, FeishuError, document_url
from .secrets import redact_text
from .workplace import Workplace


def format_post(agent: str, text: str, *, post_id: str | None = None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    body = redact_text(text).strip() or "(empty)"
    marker = post_id or secrets.token_hex(6)
    return f"### {agent} · {stamp} · post:{marker}\n\n{body}\n"


def _post_blocks(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?m)(?=^### )", text) if part.strip().startswith("### ")]


def _merge_board(local: str, remote: str) -> str:
    """Preserve local unsynced posts while incorporating Feishu edits."""
    local = local.replace("\r\n", "\n").strip()
    remote = remote.replace("\r\n", "\n").strip()
    if not local:
        return remote + ("\n" if remote else "")
    if not remote or local == remote:
        return local + "\n"
    if local.startswith(remote):
        return local + "\n"
    if remote.startswith(local):
        return remote + "\n"
    local_blocks = _post_blocks(local)
    remote_blocks = _post_blocks(remote)
    if not local_blocks or not remote_blocks:
        return local + "\n\n## Feishu recovery\n\n" + remote + "\n"
    header = local.split("## Log", 1)[0].rstrip()
    blocks = list(remote_blocks)
    for block in local_blocks:
        if block not in blocks:
            blocks.append(block)
    return header + "\n\n## Log\n\n" + "\n\n".join(blocks) + "\n"


class Board:
    """Feishu is the live IM channel; board.md is the workplace snapshot."""

    def __init__(self, settings: Settings, workplace: Workplace) -> None:
        self.settings = settings
        self.workplace = workplace
        self._client: FeishuClient | None = None

    @property
    def client(self) -> FeishuClient | None:
        if not self.settings.feishu_configured:
            return None
        if self._client is None:
            self._client = FeishuClient.from_settings(self.settings)
        return self._client

    def ensure_document(self) -> str:
        client = self.client
        if client is None:
            return ""
        with self.workplace.locked():
            state = self.workplace.read_state()
            document_id = str(state.get("document_id", "")).strip()
            if document_id:
                return document_id
            folder_token = os.getenv(self.settings.feishu_folder_token_env, "").strip()
            title = f"IMF Board · {state.get('title', self.workplace.mission_id)}"
            document_id = client.create_document(title, folder_token)
            state.update(
                document_id=document_id,
                feishu_url=document_url(document_id),
            )
            self.workplace._write_state_unlocked(state)
            # Persist the document id before the first append so a partial
            # network failure can retry the same document instead of creating
            # an orphan document on every attempt.
            client.append_text(document_id, self.workplace.read_board())
        self.workplace.append_event(
            actor="imf",
            kind="board.opened",
            summary="Opened Feishu board document",
            details={"document_id": document_id},
        )
        return document_id

    def post(self, agent: str, text: str, *, sync: bool = True) -> str:
        entry = format_post(agent, text)
        self.workplace.append_board(entry)
        warning = ""
        if sync:
            try:
                document_id = self.ensure_document()
                client = self.client
                if document_id and client is not None:
                    # Check the marker before retrying an uncertain Feishu
                    # response so a timeout does not duplicate the same post.
                    remote = client.raw_content(document_id)
                    marker = entry.split("post:", 1)[1].splitlines()[0]
                    if f"post:{marker}" not in remote:
                        client.append_text(document_id, entry)
            except FeishuError as exc:
                warning = str(exc)
                self.workplace.append_event(
                    actor="imf",
                    kind="feishu.sync_failed",
                    summary=warning,
                )
        self.workplace.append_event(
            actor=agent,
            kind="board.post",
            summary=f"{agent} posted to the board",
            details={"chars": len(entry)},
        )
        return warning

    def pull(self) -> str:
        state = self.workplace.read_state()
        document_id = str(state.get("document_id", "")).strip()
        client = self.client
        if not document_id or client is None:
            return self.workplace.read_board()
        content = client.raw_content(document_id)
        content = _merge_board(self.workplace.read_board(), content)
        self.workplace.write_board(content)
        self.workplace.append_event(
            actor="imf",
            kind="board.pulled",
            summary="Pulled Feishu board into board.md",
        )
        return content

    def snapshot(self) -> str:
        """Pause/finish hook: Feishu wins when reachable, else keep local."""
        try:
            return self.pull()
        except FeishuError as exc:
            self.workplace.append_event(
                actor="imf",
                kind="feishu.sync_failed",
                summary=str(exc),
            )
            return self.workplace.read_board()
