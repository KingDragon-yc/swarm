"""Command line surface intended for a Codex orchestrator."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from .config import Settings, load_settings
from .core import EventLedger, latest_run_id, route_task, utc_now
from .feishu import FeishuClient, FeishuError, rebuild_document, sync_event
from .providers import (
    cursor_model_candidates,
    list_cursor_models,
    make_adapter,
    resolve_cursor_model,
)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _resolve_run(settings: Settings, run_id: str) -> EventLedger:
    selected = run_id
    if selected == "latest":
        selected = latest_run_id(settings.state_dir) or ""
    if not selected:
        raise FileNotFoundError("No collaboration run exists")
    return EventLedger.open(settings.state_dir, selected)


def _sync(
    settings: Settings,
    ledger: EventLedger,
    event: dict[str, Any],
    *,
    enabled: bool,
) -> dict[str, str]:
    if not enabled:
        return {}
    try:
        document_id = sync_event(settings, ledger, event)
        return {"document_id": document_id} if document_id else {}
    except FeishuError as exc:
        ledger.append(
            actor="system",
            kind="feishu.sync_failed",
            summary=str(exc),
        )
        return {"feishu_warning": str(exc)}


def _append(
    settings: Settings,
    ledger: EventLedger,
    *,
    actor: str,
    kind: str,
    summary: str,
    details: dict[str, Any] | None = None,
    sync: bool = True,
) -> tuple[dict[str, Any], dict[str, str]]:
    event = ledger.append(
        actor=actor,
        kind=kind,
        summary=summary,
        details=details,
    )
    return event, _sync(settings, ledger, event, enabled=sync)


def _doctor(settings: Settings, *, probe_cursor: bool) -> int:
    cursor_executable = shutil.which("agent.cmd") or shutil.which("agent")
    cursor_models: list[str] = []
    cursor_error = ""
    if probe_cursor and cursor_executable:
        try:
            cursor_models = list_cursor_models(timeout=30)
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            cursor_error = str(exc)

    roster = []
    for spec in settings.agents.values():
        resolved_model = ""
        candidates: list[str] = []
        if spec.backend == "orchestrator":
            status = "ready (current Codex task)"
        elif spec.backend == "cursor":
            if not cursor_executable:
                status = "blocked: Cursor agent CLI missing"
            elif probe_cursor and cursor_error:
                status = "blocked: Cursor model probe failed"
            elif probe_cursor:
                candidates = cursor_model_candidates(spec.name, cursor_models)[:5]
                try:
                    resolved_model, selection = resolve_cursor_model(
                        spec,
                        cursor_models,
                    )
                    status = f"ready ({selection} -> {resolved_model})"
                except RuntimeError as exc:
                    status = "blocked: " + str(exc)
            else:
                status = "ready" if probe_cursor else "installed; use --probe-cursor to verify login/model"
        else:
            missing = []
            if not os.getenv(spec.api_key_env, "").strip():
                missing.append(spec.api_key_env)
            if not spec.model:
                missing.append(f"{spec.name.upper()}_MODEL")
            status = "ready" if not missing else "blocked: missing " + ", ".join(missing)
        roster.append(
            {
                "name": spec.name,
                "vendor": spec.vendor,
                "backend": spec.backend,
                "model": spec.model or "(orchestrator/required env)",
                "resolved_model": resolved_model,
                "candidates": candidates,
                "endpoint": spec.base_url or "(inside current Codex/Cursor)",
                "credential_env": spec.api_key_env or "(current session)",
                "status": status,
            },
        )

    _print_json(
        {
            "harness_root": str(settings.root),
            "state_dir": str(settings.state_dir),
            "local_env": (
                f"loaded: {settings.env_file}"
                if settings.env_file.is_file()
                else f"missing (optional): {settings.env_file}"
            ),
            "cursor_cli": cursor_executable or "missing",
            "cursor_probe_error": cursor_error,
            "feishu": (
                "credentials present"
                if settings.feishu_configured
                else "blocked: missing FEISHU_APP_ID/FEISHU_APP_SECRET"
            ),
            "roster": roster,
        },
    )
    return 0


def _start(args: argparse.Namespace, settings: Settings) -> int:
    workspace = Path(args.workspace or settings.root).resolve()
    if not workspace.is_dir():
        raise FileNotFoundError(f"Workspace directory not found: {workspace}")
    ledger = EventLedger.create(settings.state_dir, args.task, workspace)
    first_event = ledger.events()[0]
    sync_info = _sync(settings, ledger, first_event, enabled=not args.no_sync)
    decision = route_task(args.task)
    _, route_sync = _append(
        settings,
        ledger,
        actor="gpt",
        kind="routing.recommended",
        summary=f"Delegation gate recommends: {decision.mode}",
        details=decision.to_dict(),
        sync=not args.no_sync,
    )
    sync_info.update(route_sync)
    _print_json(
        {
            "run_id": ledger.run_id,
            "route": decision.to_dict(),
            "brief": str(ledger.brief_path),
            **sync_info,
        },
    )
    return 0


def _route(args: argparse.Namespace) -> int:
    _print_json(route_task(args.task).to_dict())
    return 0


def _note(args: argparse.Namespace, settings: Settings) -> int:
    ledger = _resolve_run(settings, args.run_id)
    event, sync_info = _append(
        settings,
        ledger,
        actor="gpt",
        kind=args.kind,
        summary=args.text,
        sync=not args.no_sync,
    )
    _print_json({"event": event, **sync_info})
    return 0


def _delegate(args: argparse.Namespace, settings: Settings) -> int:
    ledger = _resolve_run(settings, args.run_id)
    spec = settings.agents.get(args.agent)
    if spec is None:
        raise ValueError(f"Unknown agent: {args.agent}")
    if spec.backend == "orchestrator":
        raise ValueError("GPT is the orchestrator and cannot delegate to itself")

    state = ledger.read_state()
    workspace = Path(state["workspace"]).resolve()
    context = ledger.context(max_chars=6_000)
    if args.context_file:
        path = Path(args.context_file).resolve()
        if spec.backend == "cursor":
            try:
                relative = path.relative_to(workspace)
            except ValueError as exc:
                raise ValueError(
                    "Cursor context file must be inside the authorized workspace",
                ) from exc
            context += (
                "\nExplicit context file available inside the workspace: "
                + str(relative)
            )
        else:
            context += "\n\n## Explicit context file\n" + path.read_text(
                encoding="utf-8",
                errors="replace",
            )[-30_000:]

    _append(
        settings,
        ledger,
        actor="gpt",
        kind="delegation.started",
        summary=f"Delegated a bounded read-only question to {spec.name}",
        details={"agent": spec.name, "task": args.task, "mock": args.mock},
        sync=not args.no_sync,
    )
    adapter = make_adapter(spec, mock=args.mock)
    try:
        result = adapter.invoke(
            task=args.task,
            context=context,
            workspace=workspace,
            timeout=args.timeout,
        )
    except Exception as exc:
        event, sync_info = _append(
            settings,
            ledger,
            actor=spec.name,
            kind="delegation.failed",
            summary=str(exc),
            details={"agent": spec.name},
            sync=not args.no_sync,
        )
        _print_json({"ok": False, "event": event, **sync_info})
        return 2

    event, sync_info = _append(
        settings,
        ledger,
        actor=result.agent,
        kind="delegation.completed",
        summary=f"{result.agent} returned an independent specialist report",
        details={
            "report": result.text,
            "duration_ms": result.duration_ms,
            "metadata": result.metadata,
        },
        sync=not args.no_sync,
    )
    _print_json(
        {
            "ok": True,
            "agent": result.agent,
            "report": result.text,
            "event": event,
            **sync_info,
        },
    )
    return 0


def _finish(args: argparse.Namespace, settings: Settings) -> int:
    ledger = _resolve_run(settings, args.run_id)
    summary = args.summary
    if args.summary_file:
        summary = Path(args.summary_file).read_text(
            encoding="utf-8",
            errors="replace",
        )
    if not summary.strip():
        raise ValueError("A non-empty --summary or --summary-file is required")
    ledger.update_state(status="completed", completed_at=args.completed_at or utc_now())
    event, sync_info = _append(
        settings,
        ledger,
        actor="gpt",
        kind="run.completed",
        summary="Codex completed the run",
        details={"final_summary": summary},
        sync=not args.no_sync,
    )

    notification_warning = ""
    chat_id = os.getenv(settings.feishu_chat_id_env, "").strip()
    document_id = ledger.read_state().get("document_id", "")
    if not args.no_sync and settings.feishu_configured and chat_id:
        try:
            FeishuClient.from_settings(settings).send_chat_text(
                chat_id,
                f"协作任务 {ledger.run_id} 已完成。飞书文档 ID：{document_id}",
            )
        except FeishuError as exc:
            notification_warning = str(exc)
    _print_json(
        {
            "event": event,
            "brief": str(ledger.brief_path),
            "notification_warning": notification_warning,
            **sync_info,
        },
    )
    return 0


def _fail(args: argparse.Namespace, settings: Settings) -> int:
    ledger = _resolve_run(settings, args.run_id)
    ledger.update_state(status="failed", failed_at=utc_now())
    event, sync_info = _append(
        settings,
        ledger,
        actor="system",
        kind="run.failed",
        summary=args.reason,
        sync=not args.no_sync,
    )
    _print_json({"event": event, **sync_info})
    return 0


def _show(args: argparse.Namespace, settings: Settings) -> int:
    ledger = _resolve_run(settings, args.run_id)
    _print_json({"state": ledger.read_state(), "events": ledger.events()})
    return 0


def _feishu_chats(settings: Settings) -> int:
    if not settings.feishu_configured:
        raise ValueError(
            "Configure FEISHU_APP_ID and FEISHU_APP_SECRET in harness/.env first",
        )
    chats = FeishuClient.from_settings(settings).list_chats()
    _print_json({"count": len(chats), "chats": chats})
    return 0


def _feishu_create_doc(args: argparse.Namespace, settings: Settings) -> int:
    if not settings.feishu_configured:
        raise ValueError(
            "Configure FEISHU_APP_ID and FEISHU_APP_SECRET in harness/.env first",
        )
    folder_token = args.folder_token or os.getenv(
        settings.feishu_folder_token_env,
        "",
    ).strip()
    document_id = FeishuClient.from_settings(settings).create_document(
        args.title,
        folder_token,
    )
    _print_json(
        {
            "document_id": document_id,
            "folder_token": folder_token,
            "next": "Set FEISHU_DOCUMENT_ID only when all runs should reuse this document.",
        },
    )
    return 0


def _feishu_rebuild_run_doc(args: argparse.Namespace, settings: Settings) -> int:
    if not settings.feishu_configured:
        raise ValueError(
            "Configure FEISHU_APP_ID and FEISHU_APP_SECRET in harness/.env first",
        )
    ledger = _resolve_run(settings, args.run_id)
    folder_token = args.folder_token or os.getenv(
        settings.feishu_folder_token_env,
        "",
    ).strip()
    event = ledger.append(
        actor="gpt",
        kind="feishu.doc_rebuilt",
        summary="Rebuilt the run document in WP form and kept the full progress log.",
        details={
            "reason": args.reason,
            "title": args.title or f"CTF/SRC collaboration · {ledger.run_id}",
        },
    )
    try:
        document_id = rebuild_document(
            settings,
            ledger,
            title=args.title,
            folder_token=folder_token,
        )
    except FeishuError as exc:
        ledger.append(
            actor="system",
            kind="feishu.sync_failed",
            summary=str(exc),
        )
        raise
    _print_json(
        {
            "event": event,
            "document_id": document_id,
            "brief": str(ledger.brief_path),
        },
    )
    return 0


def _demo(settings: Settings) -> int:
    """Prove that a simple task stays with the orchestrator and uses no peer."""
    sample = settings.root / "samples" / "catmisc01" / "note.txt"
    task = "读取 samples/catmisc01/note.txt，识别并解码其中的 Base64。"
    decision = route_task(task)
    if decision.mode != "solo":
        raise RuntimeError("Offline demo routing invariant failed")
    ledger = EventLedger.create(settings.state_dir, task, sample.parent)
    raw = sample.read_text(encoding="utf-8").strip()
    decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    ledger.append(
        actor="gpt",
        kind="orchestrator.completed_solo",
        summary="Codex path completed the simple task without invoking any peer",
        details={"decoded": decoded, "delegations": 0},
    )
    ledger.update_state(status="completed")
    _print_json(
        {
            "run_id": ledger.run_id,
            "route": decision.to_dict(),
            "delegations": 0,
            "decoded": decoded,
            "brief": str(ledger.brief_path),
        },
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m collab_demo",
        description="Codex-led, sparse multi-model collaboration harness",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check providers without printing secrets")
    doctor.add_argument("--probe-cursor", action="store_true")

    route = subparsers.add_parser("route", help="Recommend whether delegation is justified")
    route.add_argument("task")

    start = subparsers.add_parser("start", help="Create an append-only collaboration run")
    start.add_argument("task")
    start.add_argument("--workspace", default="")
    start.add_argument("--no-sync", action="store_true")

    note = subparsers.add_parser("note", help="Record a Codex progress note")
    note.add_argument("run_id", help="Run id or 'latest'")
    note.add_argument("text")
    note.add_argument("--kind", default="orchestrator.note")
    note.add_argument("--no-sync", action="store_true")

    delegate = subparsers.add_parser("delegate", help="Ask exactly one specialist")
    delegate.add_argument("run_id", help="Run id or 'latest'")
    delegate.add_argument("agent", choices=("claude", "gemini", "grok", "deepseek", "kimi", "glm", "qwen"))
    delegate.add_argument("task")
    delegate.add_argument("--context-file", default="")
    delegate.add_argument("--timeout", type=int, default=300)
    delegate.add_argument("--mock", action="store_true")
    delegate.add_argument("--no-sync", action="store_true")

    finish = subparsers.add_parser("finish", help="Complete a run and optionally notify Feishu")
    finish.add_argument("run_id", help="Run id or 'latest'")
    finish_group = finish.add_mutually_exclusive_group(required=True)
    finish_group.add_argument("--summary", default="")
    finish_group.add_argument("--summary-file", default="")
    finish.add_argument("--completed-at", default="")
    finish.add_argument("--no-sync", action="store_true")

    fail = subparsers.add_parser("fail", help="Close a run after an orchestrator failure")
    fail.add_argument("run_id", help="Run id or 'latest'")
    fail.add_argument("reason")
    fail.add_argument("--no-sync", action="store_true")

    show = subparsers.add_parser("show", help="Print sanitized run state and events")
    show.add_argument("run_id", help="Run id or 'latest'")

    subparsers.add_parser(
        "feishu-chats",
        help="List chat_id values visible to the configured bot",
    )

    create_doc = subparsers.add_parser(
        "feishu-create-doc",
        help="Create a Feishu Docx and print its document_id",
    )
    create_doc.add_argument("--title", default="Buzz CTF/SRC collaboration")
    create_doc.add_argument("--folder-token", default="")

    rebuild_doc = subparsers.add_parser(
        "feishu-rebuild-run-doc",
        help="Create a fresh Feishu Docx from a run's full WP projection",
    )
    rebuild_doc.add_argument("run_id", help="Run id or 'latest'")
    rebuild_doc.add_argument("--title", default="")
    rebuild_doc.add_argument("--folder-token", default="")
    rebuild_doc.add_argument(
        "--reason",
        default="Switch Feishu projection to a reproducible WP with a preserved progress log.",
    )

    subparsers.add_parser("demo", help="Run a zero-network, zero-delegation sample")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    try:
        if args.command == "doctor":
            return _doctor(settings, probe_cursor=args.probe_cursor)
        if args.command == "route":
            return _route(args)
        if args.command == "start":
            return _start(args, settings)
        if args.command == "note":
            return _note(args, settings)
        if args.command == "delegate":
            return _delegate(args, settings)
        if args.command == "finish":
            return _finish(args, settings)
        if args.command == "fail":
            return _fail(args, settings)
        if args.command == "show":
            return _show(args, settings)
        if args.command == "feishu-chats":
            return _feishu_chats(settings)
        if args.command == "feishu-create-doc":
            return _feishu_create_doc(args, settings)
        if args.command == "feishu-rebuild-run-doc":
            return _feishu_rebuild_run_doc(args, settings)
        if args.command == "demo":
            return _demo(settings)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"Unknown command: {args.command}")
    return 2
