"""Small Feishu document client used as a projection of the local ledger."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import error, parse, request

from .config import Settings
from .core import EventLedger, redact_text


_FEISHU_API = "https://open.feishu.cn/open-apis"
_TOKEN_EXPIRED_CODES = frozenset({99991663, 99991664})
_RATE_LIMIT_CODES = frozenset({99991400})


class FeishuError(RuntimeError):
    """A sanitized Feishu API failure."""


def _text_block(content: str) -> dict[str, Any]:
    return {
        "block_type": 2,
        "text": {
            "elements": [
                {
                    "text_run": {
                        "content": content,
                        "text_element_style": {},
                    },
                },
            ],
            "style": {},
        },
    }


def text_blocks(content: str, *, chunk_size: int = 1800) -> list[dict[str, Any]]:
    """Convert plain text to conservative Docx paragraph blocks."""
    normalized = redact_text(content).replace("\r\n", "\n")
    if not normalized:
        return [_text_block(" ")]
    chunks = [
        normalized[index : index + chunk_size]
        for index in range(0, len(normalized), chunk_size)
    ]
    return [_text_block(chunk) for chunk in chunks]


def event_text(ledger: EventLedger, event: dict[str, Any]) -> str:
    events = ledger.events()
    for index, candidate in enumerate(events, start=1):
        if candidate == event:
            return ledger.render_event_text(event, index=index)
    return ledger.render_event_text(event)


class FeishuClient:
    """Use tenant credentials without persisting tokens or secrets."""

    def __init__(self, app_id: str, app_secret: str, *, timeout: int = 30) -> None:
        if not app_id or not app_secret:
            raise ValueError("Feishu app_id and app_secret are required")
        self._app_id = app_id
        self._app_secret = app_secret
        self._timeout = timeout
        self._token = ""

    @classmethod
    def from_settings(cls, settings: Settings) -> "FeishuClient":
        return cls(
            os.getenv(settings.feishu_app_id_env, "").strip(),
            os.getenv(settings.feishu_app_secret_env, "").strip(),
        )

    def _fetch_token(self) -> str:
        payload = {
            "app_id": self._app_id,
            "app_secret": self._app_secret,
        }
        data = self._raw_request(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            payload,
            authenticated=False,
        )
        token = data.get("tenant_access_token")
        if data.get("code") != 0 or not isinstance(token, str) or not token:
            raise FeishuError(
                "Feishu token request failed: "
                + redact_text(str(data.get("msg", "unknown error"))),
            )
        self._token = token
        return token

    def _raw_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        *,
        authenticated: bool,
        attempt: int = 0,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if authenticated:
            token = self._token or self._fetch_token()
            headers["Authorization"] = f"Bearer {token}"
        http_request = request.Request(
            f"{_FEISHU_API}{path}",
            method=method,
            headers=headers,
            data=(
                json.dumps(body, ensure_ascii=False).encode("utf-8")
                if body is not None
                else None
            ),
        )
        try:
            with request.urlopen(http_request, timeout=self._timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"code": exc.code, "msg": raw[-1000:]}
            if exc.code == 429 and attempt < 3:
                time.sleep(2**attempt)
                return self._raw_request(
                    method,
                    path,
                    body,
                    authenticated=authenticated,
                    attempt=attempt + 1,
                )
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FeishuError(
                "Feishu request failed: " + redact_text(str(exc)),
            ) from exc

        code = data.get("code", 0)
        if authenticated and code in _TOKEN_EXPIRED_CODES and attempt < 1:
            self._token = ""
            return self._raw_request(
                method,
                path,
                body,
                authenticated=True,
                attempt=attempt + 1,
            )
        if code in _RATE_LIMIT_CODES and attempt < 3:
            time.sleep(2**attempt)
            return self._raw_request(
                method,
                path,
                body,
                authenticated=authenticated,
                attempt=attempt + 1,
            )
        if code != 0:
            raise FeishuError(
                f"Feishu API code {code}: "
                + redact_text(str(data.get("msg", "unknown error"))),
            )
        return data

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._raw_request(method, path, body, authenticated=True)

    def list_chats(self, *, page_size: int = 50) -> list[dict[str, str]]:
        """List groups visible to the bot, following bounded pagination."""
        chats: list[dict[str, str]] = []
        page_token = ""
        for _ in range(10):
            path = f"/im/v1/chats?page_size={page_size}"
            if page_token:
                path += "&page_token=" + parse.quote(page_token, safe="")
            response = self.request("GET", path)
            data = response.get("data") or {}
            for item in data.get("items") or []:
                chat_id = item.get("chat_id")
                if isinstance(chat_id, str) and chat_id:
                    chats.append(
                        {
                            "chat_id": chat_id,
                            "name": str(item.get("name", "")),
                            "chat_mode": str(item.get("chat_mode", "")),
                            "chat_type": str(item.get("chat_type", "")),
                        },
                    )
            if not data.get("has_more"):
                break
            next_token = data.get("page_token")
            if not isinstance(next_token, str) or not next_token:
                break
            page_token = next_token
        return chats

    def create_document(self, title: str, folder_token: str = "") -> str:
        body: dict[str, Any] = {"title": title[:500]}
        if folder_token:
            body["folder_token"] = folder_token
        data = self.request("POST", "/docx/v1/documents", body)
        try:
            document_id = data["data"]["document"]["document_id"]
        except (KeyError, TypeError) as exc:
            raise FeishuError("Feishu create-document response had no document_id") from exc
        if not isinstance(document_id, str) or not document_id:
            raise FeishuError("Feishu returned an empty document_id")
        return document_id

    def append_text(self, document_id: str, content: str) -> None:
        safe_document_id = parse.quote(document_id, safe="")
        blocks = text_blocks(content)
        for start in range(0, len(blocks), 50):
            self.request(
                "POST",
                (
                    f"/docx/v1/documents/{safe_document_id}/blocks/"
                    f"{safe_document_id}/children?document_revision_id=-1"
                ),
                {"children": blocks[start : start + 50]},
            )

    def send_chat_text(self, chat_id: str, content: str) -> None:
        self.request(
            "POST",
            f"/im/v1/messages?receive_id_type=chat_id",
            {
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps(
                    {"text": redact_text(content)},
                    ensure_ascii=False,
                ),
            },
        )


def rebuild_document(
    settings: Settings,
    ledger: EventLedger,
    *,
    title: str = "",
    folder_token: str = "",
) -> str:
    """Create a fresh Feishu Docx from the current run projection."""
    client = FeishuClient.from_settings(settings)
    document_id = client.create_document(
        title or f"CTF/SRC collaboration · {ledger.run_id}",
        folder_token,
    )
    client.append_text(document_id, ledger.render_wp_text())
    ledger.update_state(document_id=document_id)
    return document_id


def sync_event(
    settings: Settings,
    ledger: EventLedger,
    event: dict[str, Any],
) -> str | None:
    """Append one event to the run document, creating it when needed."""
    if not settings.feishu_configured:
        return None
    state = ledger.read_state()
    document_id = str(state.get("document_id", "")).strip()
    if not document_id:
        document_id = os.getenv(settings.feishu_document_id_env, "").strip()
    client = FeishuClient.from_settings(settings)
    created_new = False
    if not document_id:
        folder_token = os.getenv(settings.feishu_folder_token_env, "").strip()
        document_id = client.create_document(
            f"CTF/SRC collaboration · {ledger.run_id}",
            folder_token,
        )
        created_new = True
    if created_new:
        ledger.update_state(document_id=document_id)
        client.append_text(document_id, ledger.render_wp_text())
        return document_id
    if document_id != state.get("document_id"):
        ledger.update_state(document_id=document_id)
    client.append_text(document_id, event_text(ledger, event))
    return document_id
