"""Live Feishu board plus the local board.md snapshot."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from .config import Settings
from .feishu import FeishuClient, FeishuError, document_url
from .secrets import redact_text
from .workplace import Workplace


def format_post(agent: str, text: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    body = redact_text(text).strip() or "(empty)"
    return f"### {agent} · {stamp}\n\n{body}\n"


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
        state = self.workplace.read_state()
        document_id = str(state.get("document_id", "")).strip()
        if document_id:
            return document_id
        client = self.client
        if client is None:
            return ""
        folder_token = os.getenv(self.settings.feishu_folder_token_env, "").strip()
        title = f"IMF Board · {state.get('title', self.workplace.mission_id)}"
        document_id = client.create_document(title, folder_token)
        client.append_text(document_id, self.workplace.read_board())
        self.workplace.update_state(
            document_id=document_id,
            feishu_url=document_url(document_id),
        )
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
