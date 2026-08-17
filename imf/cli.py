"""CLI for IMF: doctor, web, start, dispatch, snapshot, finish."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import AGENT_IDS, load_settings
from .feishu import FeishuClient, FeishuError
from .force import Force, doctor_report
from .plugins import load_catalog


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m imf",
        description="IMF — Instant Message Force. Four operatives, one Feishu board.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Check providers without printing secrets")
    doctor.add_argument("--probe-cursor", action="store_true")

    web = sub.add_parser("web", help="Serve the IMF web UI")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=3080)

    start = sub.add_parser("start", help="Create a workplace and open the Feishu board")
    start.add_argument("title")
    start.add_argument("mission")
    start.add_argument("--attachments", default="")
    start.add_argument("--no-sync", action="store_true")

    dispatch = sub.add_parser("dispatch", help="Run one or more operatives onto the board")
    dispatch.add_argument("mission_id")
    dispatch.add_argument("task")
    dispatch.add_argument("--agents", default=",".join(AGENT_IDS))
    dispatch.add_argument("--timeout", type=int, default=300)
    dispatch.add_argument("--mock", action="store_true")
    dispatch.add_argument("--no-sync", action="store_true")

    post = sub.add_parser("post", help="Write a board entry as an operative")
    post.add_argument("mission_id")
    post.add_argument("agent", choices=AGENT_IDS)
    post.add_argument("text")
    post.add_argument("--no-sync", action="store_true")

    pull = sub.add_parser("pull", help="Pull the live Feishu document into board.md")
    pull.add_argument("mission_id")

    snap = sub.add_parser("snapshot", help="Save the Feishu board into the workplace")
    snap.add_argument("mission_id")

    pause = sub.add_parser("pause", help="Pause a mission and snapshot the board")
    pause.add_argument("mission_id")

    finish = sub.add_parser("finish", help="Finish a mission and snapshot the board")
    finish.add_argument("mission_id")
    finish.add_argument("--summary", default="")
    finish.add_argument("--no-sync", action="store_true")

    show = sub.add_parser("show", help="Print a workplace")
    show.add_argument("mission_id")

    plugins = sub.add_parser("plugins", help="List the plugin catalog")
    plugins.add_argument("--mission", default="")
    plugins.add_argument("--agent", choices=AGENT_IDS, default="")

    compose = sub.add_parser("compose", help="Creator-mode plugin selection for one cell")
    compose.add_argument("mission_id")
    compose.add_argument("agent", choices=AGENT_IDS)
    compose.add_argument("description")

    set_plugins = sub.add_parser("set-plugins", help="Write a cell plugin list")
    set_plugins.add_argument("mission_id")
    set_plugins.add_argument("agent", choices=AGENT_IDS)
    set_plugins.add_argument("plugins")

    chats = sub.add_parser("chats", help="List Feishu chats visible to the bot")
    new_doc = sub.add_parser("new-doc", help="Create an empty Feishu document")
    new_doc.add_argument("--title", default="IMF Board")
    new_doc.add_argument("--folder-token", default="")

    sub.add_parser("demo", help="Offline four-operative mock against samples/src-lab")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    force = Force(settings)
    try:
        if args.command == "doctor":
            _print_json(doctor_report(settings, probe_cursor=args.probe_cursor))
            return 0
        if args.command == "web":
            from .server import serve

            return serve(settings, host=args.host, port=args.port)
        if args.command == "start":
            attachments = Path(args.attachments) if args.attachments else None
            mission = force.start(
                title=args.title,
                mission=args.mission,
                attachments=attachments,
                sync=not args.no_sync,
            )
            _print_json(mission.workplace.public_view())
            return 0
        if args.command == "dispatch":
            mission = force.open(args.mission_id)
            agents = tuple(item.strip() for item in args.agents.split(",") if item.strip())
            results = force.dispatch(
                mission,
                task=args.task,
                agents=agents,
                mock=args.mock,
                timeout=args.timeout,
                sync=not args.no_sync,
            )
            _print_json({"mission_id": mission.id, "results": results})
            return 0 if all(item.get("ok") for item in results) else 2
        if args.command == "post":
            mission = force.open(args.mission_id)
            warning = mission.board.post(args.agent, args.text, sync=not args.no_sync)
            _print_json({"mission_id": mission.id, "feishu_warning": warning})
            return 0
        if args.command == "pull":
            mission = force.open(args.mission_id)
            content = mission.board.pull()
            _print_json({"mission_id": mission.id, "chars": len(content)})
            return 0
        if args.command == "snapshot":
            mission = force.open(args.mission_id)
            content = force.snapshot(mission)
            _print_json(
                {
                    "mission_id": mission.id,
                    "board": str(mission.workplace.board_path),
                    "chars": len(content),
                },
            )
            return 0
        if args.command == "pause":
            mission = force.open(args.mission_id)
            _print_json({"mission_id": mission.id, **force.pause(mission)})
            return 0
        if args.command == "finish":
            mission = force.open(args.mission_id)
            _print_json(
                {
                    "mission_id": mission.id,
                    **force.finish(
                        mission,
                        summary=args.summary,
                        notify=not args.no_sync,
                    ),
                },
            )
            return 0
        if args.command == "show":
            mission = force.open(args.mission_id)
            view = mission.workplace.public_view()
            view["events"] = mission.workplace.events()
            _print_json(view)
            return 0
        if args.command == "plugins":
            catalog = [item.to_dict() for item in load_catalog(settings.catalog_path)]
            payload: dict[str, Any] = {"catalog": catalog}
            if args.mission and args.agent:
                mission = force.open(args.mission)
                payload["selected"] = mission.workplace.plugins_for(args.agent)
            _print_json(payload)
            return 0
        if args.command == "compose":
            mission = force.open(args.mission_id)
            selected = force.compose(mission, args.agent, args.description)
            _print_json({"agent": args.agent, "plugins": selected})
            return 0
        if args.command == "set-plugins":
            mission = force.open(args.mission_id)
            plugins = [item.strip() for item in args.plugins.split(",") if item.strip()]
            force.set_plugins(mission, args.agent, plugins)
            _print_json({"agent": args.agent, "plugins": mission.workplace.plugins_for(args.agent)})
            return 0
        if args.command == "chats":
            if not settings.feishu_configured:
                raise ValueError("Configure FEISHU_APP_ID and FEISHU_APP_SECRET first")
            chats = FeishuClient.from_settings(settings).list_chats()
            _print_json({"count": len(chats), "chats": chats})
            return 0
        if args.command == "new-doc":
            if not settings.feishu_configured:
                raise ValueError("Configure FEISHU_APP_ID and FEISHU_APP_SECRET first")
            document_id = FeishuClient.from_settings(settings).create_document(
                args.title,
                args.folder_token,
            )
            _print_json({"document_id": document_id})
            return 0
        if args.command == "demo":
            mission = force.demo()
            _print_json(
                {
                    "mission_id": mission.id,
                    "board": str(mission.workplace.board_path),
                    "status": mission.workplace.read_state()["status"],
                },
            )
            return 0
    except (FileNotFoundError, ValueError, RuntimeError, FeishuError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"Unknown command: {args.command}")
    return 2
