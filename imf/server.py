"""Stdlib web UI for IMF. Default port 3080, same as DeepSeek Harness."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .config import AGENT_IDS, Settings, load_settings
from .force import Force, doctor_report
from .plugins import load_catalog


CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw.strip():
        return {}
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


class IMFHandler(BaseHTTPRequestHandler):
    settings: Settings
    force: Force

    def log_message(self, format: str, *args: Any) -> None:
        if getattr(self.server, "quiet", False):
            return
        super().log_message(format, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        self._send(status, payload, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _static(self, path: str) -> None:
        web = self.settings.web_dir
        relative = path.lstrip("/") or "index.html"
        target = (web / relative).resolve()
        if web.resolve() not in target.parents and target != web.resolve():
            self._error(403, "forbidden")
            return
        if not target.is_file():
            self._error(404, f"missing: {relative}")
            return
        suffix = target.suffix.lower()
        self._send(200, target.read_bytes(), CONTENT_TYPES.get(suffix, "application/octet-stream"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/":
                self._static("index.html")
                return
            if path == "/api/health":
                self._json(200, {"ok": True, "product": "IMF"})
                return
            if path == "/api/doctor":
                query = parse_qs(parsed.query)
                probe = query.get("probe", ["0"])[0] == "1"
                self._json(200, doctor_report(self.settings, probe_cursor=probe))
                return
            if path == "/api/roster":
                self._json(
                    200,
                    {
                        "agents": [
                            {
                                "name": spec.name,
                                "display": spec.display,
                                "feature": spec.feature,
                                "ooda": spec.ooda,
                                "context_tokens": spec.context_tokens,
                                "duty": spec.duty,
                                "vendor": spec.vendor,
                                "backend": spec.backend,
                                "model": spec.model,
                                "reasoning": spec.reasoning,
                                "role": spec.role,
                            }
                            for spec in self.settings.agents.values()
                        ],
                    },
                )
                return
            if path == "/api/plugins":
                self._json(200, {"plugins": [item.to_dict() for item in self.force.catalog]})
                return
            if path == "/api/missions":
                self._json(200, {"missions": self.force.list_missions()})
                return
            if path.startswith("/api/missions/"):
                rest = path[len("/api/missions/") :]
                parts = [item for item in rest.split("/") if item]
                if not parts:
                    self._error(404, "missing mission")
                    return
                mission = self.force.open(parts[0])
                if len(parts) == 1:
                    self._json(200, mission.workplace.public_view())
                    return
                if parts[1] == "board":
                    self._json(200, {"board": mission.workplace.read_board()})
                    return
                if parts[1] == "events":
                    self._json(200, {"events": mission.workplace.events()})
                    return
                if parts[1] == "cells" and len(parts) >= 3:
                    agent = parts[2]
                    if agent not in AGENT_IDS:
                        self._error(404, f"unknown operative: {agent}")
                        return
                    self._json(
                        200,
                        {
                            "agent": agent,
                            "plugins": mission.workplace.plugins_for(agent),
                            "agents_md": mission.workplace.agents_md(agent).read_text(encoding="utf-8"),
                            "notes": mission.workplace.cell_notes(agent).read_text(encoding="utf-8"),
                        },
                    )
                    return
                self._error(404, "unknown mission route")
                return
            self._static(path)
        except FileNotFoundError as exc:
            self._error(404, str(exc))
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, str(exc))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            body = _json_body(self)
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(400, str(exc))
            return
        try:
            if path == "/api/missions":
                title = str(body.get("title") or "").strip()
                mission_text = str(body.get("mission") or "").strip()
                if not title or not mission_text:
                    self._error(400, "title and mission are required")
                    return
                attachments = body.get("attachments") or ""
                opened = self.force.start(
                    title=title,
                    mission=mission_text,
                    attachments=Path(attachments) if attachments else None,
                    sync=not body.get("no_sync"),
                )
                self._json(201, opened.workplace.public_view())
                return
            if not path.startswith("/api/missions/"):
                self._error(404, "unknown route")
                return
            rest = path[len("/api/missions/") :]
            parts = [item for item in rest.split("/") if item]
            mission = self.force.open(parts[0])
            action = parts[1] if len(parts) > 1 else ""
            if action == "dispatch":
                agents = body.get("agents") or list(AGENT_IDS)
                results = self.force.dispatch(
                    mission,
                    task=str(body.get("task") or mission.workplace.mission_path.read_text(encoding="utf-8")),
                    agents=tuple(agents),
                    mock=bool(body.get("mock")),
                    timeout=int(body.get("timeout") or 300),
                    sync=not body.get("no_sync"),
                    parallel=bool(body.get("parallel")),
                )
                self._json(200, {"results": results, "mission": mission.workplace.public_view()})
                return
            if action == "post":
                agent = str(body.get("agent") or "")
                text = str(body.get("text") or "")
                warning = mission.board.post(agent, text, sync=not body.get("no_sync"))
                self._json(200, {"feishu_warning": warning, "board": mission.workplace.read_board()})
                return
            if action == "pull":
                content = mission.board.pull()
                self._json(200, {"board": content})
                return
            if action == "snapshot":
                content = self.force.snapshot(mission)
                self._json(200, {"board": content})
                return
            if action == "pause":
                self._json(200, self.force.pause(mission))
                return
            if action == "finish":
                self._json(
                    200,
                    self.force.finish(
                        mission,
                        summary=str(body.get("summary") or ""),
                        notify=not body.get("no_sync"),
                    ),
                )
                return
            if action == "cells" and len(parts) >= 4 and parts[3] == "plugins":
                agent = parts[2]
                plugins = list(body.get("plugins") or [])
                self.force.set_plugins(mission, agent, plugins)
                self._json(200, {"agent": agent, "plugins": mission.workplace.plugins_for(agent)})
                return
            if action == "cells" and len(parts) >= 4 and parts[3] == "compose":
                agent = parts[2]
                selected = self.force.compose(mission, agent, str(body.get("description") or ""))
                self._json(200, {"agent": agent, "plugins": selected})
                return
            self._error(404, "unknown mission route")
        except FileNotFoundError as exc:
            self._error(404, str(exc))
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, str(exc))


def serve(settings: Settings | None = None, *, host: str = "127.0.0.1", port: int = 3080, quiet: bool = False) -> int:
    loaded = settings or load_settings()
    loaded.workplaces_dir.mkdir(parents=True, exist_ok=True)
    handler = IMFHandler
    handler.settings = loaded
    handler.force = Force(loaded)
    server = ThreadingHTTPServer((host, port), handler)
    server.quiet = quiet  # type: ignore[attr-defined]
    print(f"IMF web UI → http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


def make_server(settings: Settings, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Bind an ephemeral port for tests."""
    handler = type("TestHandler", (IMFHandler,), {})
    handler.settings = settings
    handler.force = Force(settings)
    server = ThreadingHTTPServer((host, port), handler)
    server.quiet = True  # type: ignore[attr-defined]
    return server
