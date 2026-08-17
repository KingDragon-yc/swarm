"""Mission lifecycle: open a workplace, run the Force, snapshot the board."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from .board import Board
from .config import AGENT_IDS, Settings
from .context import board_budget_chars, clear_session, excerpt_board, extract_lessons
from .feishu import FeishuClient, FeishuError
from .plugins import catalog_ids, load_catalog, suggest_plugins
from .providers import make_adapter
from .workplace import Workplace, create_workplace, list_workplaces, open_workplace, utc_now


@dataclass
class Mission:
    workplace: Workplace
    board: Board

    @property
    def id(self) -> str:
        return self.workplace.mission_id


class Force:
    """The four operatives plus the Feishu board they share."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.catalog = load_catalog(settings.catalog_path)

    def open(self, mission_id: str) -> Mission:
        workplace = open_workplace(self.settings, mission_id)
        return Mission(workplace=workplace, board=Board(self.settings, workplace))

    def list_missions(self) -> list[dict[str, Any]]:
        rows = []
        for workplace in list_workplaces(self.settings):
            state = workplace.read_state()
            rows.append(
                {
                    "id": workplace.mission_id,
                    "title": state.get("title", workplace.mission_id),
                    "status": state.get("status", "unknown"),
                    "created_at": state.get("created_at", ""),
                    "document_id": state.get("document_id", ""),
                    "feishu_url": state.get("feishu_url", ""),
                },
            )
        return rows

    def start(
        self,
        *,
        title: str,
        mission: str,
        attachments: Path | None = None,
        sync: bool = True,
    ) -> Mission:
        self.settings.workplaces_dir.mkdir(parents=True, exist_ok=True)
        workplace = create_workplace(
            self.settings,
            title=title,
            mission=mission,
            attachments=attachments,
        )
        opened = Mission(workplace=workplace, board=Board(self.settings, workplace))
        if sync and self.settings.feishu_configured:
            try:
                opened.board.ensure_document()
            except FeishuError as exc:
                workplace.append_event(
                    actor="imf",
                    kind="feishu.sync_failed",
                    summary=str(exc),
                )
        return opened

    def set_plugins(self, mission: Mission, agent: str, plugins: list[str]) -> str:
        known = catalog_ids(self.catalog)
        cleaned = []
        for item in plugins:
            if item not in known:
                raise ValueError(f"unknown plugin: {item}")
            if item not in cleaned:
                cleaned.append(item)
        if "feishu-board" not in cleaned:
            cleaned.insert(0, "feishu-board")
        spec = self.settings.agents[agent]
        text = mission.workplace.set_plugins(agent, cleaned, spec)
        mission.workplace.append_event(
            actor=agent,
            kind="plugins.updated",
            summary=f"{agent} plugins: {', '.join(cleaned)}",
            details={"plugins": cleaned},
        )
        return text

    def compose(self, mission: Mission, agent: str, description: str) -> list[str]:
        selected = suggest_plugins(description, self.catalog)
        self.set_plugins(mission, agent, selected)
        return selected

    def dispatch(
        self,
        mission: Mission,
        *,
        task: str,
        agents: tuple[str, ...] | None = None,
        mock: bool = False,
        timeout: int = 300,
        sync: bool = True,
        parallel: bool = False,
    ) -> list[dict[str, Any]]:
        selected = agents or AGENT_IDS
        for name in selected:
            if name not in self.settings.agents:
                raise ValueError(f"unknown operative: {name}")
        mission.workplace.update_state(status="running")
        mission.workplace.append_event(
            actor="imf",
            kind="dispatch.started",
            summary="Dispatched " + " → ".join(selected),
            details={"task": task, "mock": mock, "parallel": parallel, "ooda": True},
        )

        def run_one(name: str) -> dict[str, Any]:
            spec = self.settings.agents[name]
            plugins = mission.workplace.plugins_for(name)
            board = mission.workplace.read_board()
            excerpt, compacted = excerpt_board(board, spec)
            if compacted:
                mission.workplace.append_event(
                    actor=name,
                    kind="context.compacted",
                    summary=(
                        f"{name} loaded a board excerpt "
                        f"({len(excerpt)} chars, budget {board_budget_chars(spec)})"
                    ),
                    details={"budget_chars": board_budget_chars(spec)},
                )
            cell_path = mission.workplace.agents_md(name)
            cell_brief = cell_path.read_text(encoding="utf-8") if cell_path.is_file() else ""
            adapter = make_adapter(spec, mock=mock)
            try:
                result = adapter.invoke(
                    task=task,
                    context=excerpt,
                    workspace=mission.workplace.path,
                    timeout=timeout,
                    plugins=plugins,
                    cell=str(mission.workplace.cell(name)),
                    cell_brief=cell_brief,
                    compacted=compacted,
                )
            except Exception as exc:
                mission.workplace.append_event(
                    actor=name,
                    kind="dispatch.failed",
                    summary=str(exc),
                )
                return {"ok": False, "agent": name, "ooda": spec.ooda, "error": str(exc)}
            warning = mission.board.post(name, result.text, sync=sync)
            notes = mission.workplace.cell_notes(name)
            existing = notes.read_text(encoding="utf-8") if notes.is_file() else ""
            notes.write_text(
                existing.rstrip() + "\n\n" + result.text.strip() + "\n",
                encoding="utf-8",
            )
            lessons = extract_lessons(result.text)
            if lessons:
                mission.workplace.append_lessons(name, lessons)
            if result.plugins:
                try:
                    self.set_plugins(mission, name, result.plugins)
                except ValueError:
                    pass
            session = clear_session(
                mission.workplace.cell(name),
                metadata=result.metadata,
                task=task,
            )
            mission.workplace.append_event(
                actor=name,
                kind="session.cleared",
                summary=f"{name} /clear after {spec.ooda}",
                details={"method": session.get("method", "new_conversation")},
            )
            return {
                "ok": True,
                "agent": name,
                "ooda": spec.ooda,
                "duration_ms": result.duration_ms,
                "feishu_warning": warning,
                "plugins": mission.workplace.plugins_for(name),
                "compacted": compacted,
                "session_cleared": True,
                "lessons": bool(lessons),
            }

        results: list[dict[str, Any]] = []
        if parallel:
            with ThreadPoolExecutor(max_workers=len(selected)) as pool:
                futures = {pool.submit(run_one, name): name for name in selected}
                for future in as_completed(futures):
                    results.append(future.result())
        else:
            for name in selected:
                results.append(run_one(name))
        mission.workplace.update_state(status="active")
        mission.workplace.append_event(
            actor="imf",
            kind="dispatch.finished",
            summary="Dispatch finished",
            details={"results": results},
        )
        return results

    def snapshot(self, mission: Mission) -> str:
        content = mission.board.snapshot()
        mission.workplace.append_event(
            actor="imf",
            kind="board.snapshot",
            summary="Saved Feishu board to board.md",
        )
        return content

    def pause(self, mission: Mission) -> dict[str, Any]:
        content = self.snapshot(mission)
        state = mission.workplace.update_state(status="paused", paused_at=utc_now())
        return {"status": state["status"], "board": content}

    def finish(self, mission: Mission, *, summary: str = "", notify: bool = True) -> dict[str, Any]:
        content = self.snapshot(mission)
        if summary.strip():
            mission.board.post("imf", summary, sync=notify)
            content = mission.workplace.read_board()
        state = mission.workplace.update_state(status="completed", completed_at=utc_now())
        mission.workplace.append_event(
            actor="imf",
            kind="mission.completed",
            summary="Mission finished; board.md saved",
        )
        warning = ""
        chat_id = os.getenv(self.settings.feishu_chat_id_env, "").strip()
        if notify and self.settings.feishu_configured and chat_id:
            try:
                FeishuClient.from_settings(self.settings).send_chat_text(
                    chat_id,
                    f"IMF mission {mission.id} finished. board.md saved in the workplace.",
                )
            except FeishuError as exc:
                warning = str(exc)
        return {"status": state["status"], "board": content, "notification_warning": warning}

    def demo(self) -> Mission:
        sample = self.settings.root / "samples" / "src-lab"
        mission_text = sample.joinpath("README.md").read_text(encoding="utf-8")
        opened = self.start(
            title="src-lab",
            mission=mission_text,
            attachments=sample,
            sync=False,
        )
        self.dispatch(
            opened,
            task="Read the authorized lab attachments and post a first-pass board note.",
            mock=True,
            sync=False,
        )
        self.snapshot(opened)
        opened.workplace.update_state(status="completed", completed_at=utc_now())
        return opened


def doctor_report(settings: Settings, *, probe_cursor: bool = False) -> dict[str, Any]:
    from .providers import _cursor_executable, cursor_model_candidates, list_cursor_models, resolve_cursor_model

    cursor_executable = _cursor_executable()
    cursor_models: list[str] = []
    cursor_error = ""
    if probe_cursor and cursor_executable:
        try:
            cursor_models = list_cursor_models(timeout=30)
        except (OSError, RuntimeError) as exc:
            cursor_error = str(exc)

    roster = []
    for spec in settings.agents.values():
        resolved_model = ""
        candidates: list[str] = []
        if spec.backend == "cursor":
            if not cursor_executable and not (spec.base_url and spec.configured):
                status = "blocked: Cursor CLI missing and CURSOR_API_BASE unset"
            elif probe_cursor and cursor_error:
                status = "blocked: Cursor model probe failed"
            elif probe_cursor and cursor_models:
                candidates = cursor_model_candidates(spec.name, cursor_models)[:5]
                try:
                    resolved_model, selection = resolve_cursor_model(spec, cursor_models)
                    status = f"ready ({selection} -> {resolved_model})"
                except RuntimeError as exc:
                    status = "blocked: " + str(exc)
            elif cursor_executable:
                status = "installed; use --probe-cursor to verify login/model"
            else:
                status = "ready via CURSOR_API_BASE"
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
                "display": spec.display,
                "feature": spec.feature,
                "ooda": spec.ooda,
                "context_tokens": spec.context_tokens,
                "vendor": spec.vendor,
                "backend": spec.backend,
                "model": spec.model,
                "reasoning": spec.reasoning,
                "resolved_model": resolved_model,
                "candidates": candidates,
                "credential_env": spec.api_key_env or "(current session)",
                "status": status,
            },
        )
    return {
        "product": "IMF",
        "meaning": "Instant Message Force",
        "root": str(settings.root),
        "workplaces": str(settings.workplaces_dir),
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
    }
