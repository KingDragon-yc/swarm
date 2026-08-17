from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from imf.board import Board, _merge_board, format_post
from imf.config import AGENT_IDS, load_local_env, load_settings
from imf.context import append_notes, board_budget_chars, excerpt_board, extract_lessons
from imf.feishu import FeishuClient, text_blocks
from imf.force import Force
from imf.plugins import apply_plugins, parse_plugins_markdown, suggest_plugins
from imf.providers import (
    MockAdapter,
    cursor_sandbox_mode,
    parse_cursor_json,
    parse_cursor_model_list,
    resolve_cursor_model,
    validate_operative_report,
)
from imf.server import make_server
from imf.workplace import create_workplace, open_workplace


def _settings(temporary: str):
    base = load_settings()
    return replace(base, workplaces_dir=Path(temporary) / "workplaces")


class RosterTest(unittest.TestCase):
    def test_force_has_four_operatives(self) -> None:
        agents = load_settings().agents
        self.assertEqual(set(agents), {"flash", "pro", "grok", "luna"})
        self.assertEqual(agents["flash"].model, "deepseek-v4-flash")
        self.assertEqual(agents["flash"].reasoning, "high")
        self.assertEqual(agents["pro"].model, "deepseek-v4-pro")
        self.assertEqual(agents["pro"].reasoning, "max")
        self.assertEqual(agents["grok"].model, "cursor-grok-4.6-high")
        self.assertEqual(agents["luna"].model, "gpt-5.6-luna-high")
        self.assertEqual(tuple(agents), AGENT_IDS)
        self.assertEqual(agents["flash"].ooda, "observe")
        self.assertEqual(agents["pro"].ooda, "orient")
        self.assertEqual(agents["luna"].ooda, "decide")
        self.assertEqual(agents["grok"].ooda, "act")
        self.assertEqual(agents["flash"].context_tokens, 1_000_000)
        self.assertEqual(agents["pro"].context_tokens, 1_000_000)
        self.assertEqual(agents["luna"].context_tokens, 500_000)
        self.assertEqual(agents["grok"].context_tokens, 256_000)
        self.assertEqual(agents["flash"].backend, "openai_compatible")
        self.assertEqual(agents["grok"].backend, "cursor")
        self.assertEqual(agents["luna"].backend, "cursor")


class PluginTest(unittest.TestCase):
    def test_parse_and_apply_plugins_section(self) -> None:
        text = "# Agent: Flash\n\n## Feature\n\nScout\n\n## Plugins\n\n- files\n\n## Notes\n\nkeep me\n"
        self.assertEqual(parse_plugins_markdown(text), ["files"])
        updated = apply_plugins(text, ["feishu-board", "files", "search"])
        self.assertEqual(parse_plugins_markdown(updated), ["feishu-board", "files", "search"])
        self.assertIn("keep me", updated)

    def test_creator_suggests_plugins_from_description(self) -> None:
        catalog = Force(load_settings()).catalog
        selected = suggest_plugins("需要 grep 源码并用 HTTP 观察接口", catalog)
        self.assertIn("feishu-board", selected)
        self.assertIn("search", selected)
        self.assertIn("http-observe", selected)


class WorkplaceTest(unittest.TestCase):
    def test_large_area_and_four_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = _settings(temporary)
            settings.workplaces_dir.mkdir(parents=True)
            workplace = create_workplace(
                settings,
                title="auth-lab",
                mission="Stay inside this authorized folder.",
                attachments=settings.root / "samples" / "src-lab",
            )
            self.assertTrue(workplace.board_path.is_file())
            self.assertTrue(workplace.mission_path.is_file())
            self.assertTrue(workplace.attachments.joinpath("app.py").is_file())
            for agent in ("flash", "pro", "luna", "grok"):
                self.assertTrue(workplace.agents_md(agent).is_file())
                self.assertIn("feishu-board", workplace.plugins_for(agent))
                markdown = workplace.agents_md(agent).read_text(encoding="utf-8")
                self.assertIn("OODA:", markdown)
                self.assertIn("## Context", markdown)
            raw = workplace.read_board()
            self.assertIn("IMF Board", raw)
            self.assertNotIn("secret-should-not-leak", raw)

    def test_workplace_id_cannot_escape_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = _settings(temporary)
            settings.workplaces_dir.mkdir(parents=True)
            outside = Path(temporary) / "outside"
            outside.mkdir()
            (outside / "state.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                open_workplace(settings, r"..\outside")

    def test_attachment_source_cannot_contain_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = _settings(temporary)
            settings.workplaces_dir.mkdir(parents=True)
            with self.assertRaises(ValueError):
                create_workplace(
                    settings,
                    title="self-copy",
                    mission="lab",
                    attachments=settings.workplaces_dir,
                )

    def test_concurrent_board_posts_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = _settings(temporary)
            force = Force(settings)
            mission = force.start(title="race", mission="lab", sync=False)
            first = force.open(mission.id)
            second = force.open(mission.id)

            def post(index: int) -> None:
                board = first.board if index % 2 else second.board
                board.post(f"agent-{index}", "entry", sync=False)

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(post, range(40)))
            self.assertEqual(mission.workplace.read_board().count("### agent-"), 40)


class LedgerRedactTest(unittest.TestCase):
    def test_board_and_events_redact_configured_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = _settings(temporary)
            settings.workplaces_dir.mkdir(parents=True)
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret-123456"}):
                workplace = create_workplace(settings, title="redact", mission="safe")
                workplace.append_event(
                    actor="flash",
                    kind="test",
                    summary="key=secret-123456",
                    details={"raw": "secret-123456"},
                )
                Board(settings, workplace).post("flash", "token secret-123456", sync=False)
            events = workplace.events_path.read_text(encoding="utf-8")
            board = workplace.read_board()
            self.assertNotIn("secret-123456", events)
            self.assertNotIn("secret-123456", board)
            self.assertIn("<redacted:DEEPSEEK_API_KEY>", events)


class ForceTest(unittest.TestCase):
    def test_mock_dispatch_writes_four_board_posts_and_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = _settings(temporary)
            force = Force(settings)
            mission = force.start(
                title="src-lab",
                mission="Read attachments.",
                attachments=settings.root / "samples" / "src-lab",
                sync=False,
            )
            results = force.dispatch(
                mission,
                task="First pass.",
                mock=True,
                sync=False,
            )
            self.assertEqual(len(results), 4)
            self.assertEqual([item["agent"] for item in results], ["flash", "pro", "luna", "grok"])
            self.assertEqual([item["ooda"] for item in results], ["observe", "orient", "decide", "act"])
            self.assertTrue(all(item["ok"] for item in results))
            self.assertTrue(all(item["session_cleared"] for item in results))
            event_kinds = [event["kind"] for event in mission.workplace.events()]
            self.assertIn("checkin.finished", event_kinds)
            self.assertLess(event_kinds.index("checkin.finished"), event_kinds.index("dispatch.started"))
            board = mission.workplace.read_board()
            self.assertLess(board.index("### flash ·"), board.index("### pro ·"))
            self.assertLess(board.index("### pro ·"), board.index("### luna ·"))
            self.assertLess(board.index("### luna ·"), board.index("### grok ·"))
            pro_notes = mission.workplace.cell_notes("pro").read_text(encoding="utf-8")
            self.assertIn("Saw flash on the board.", pro_notes)
            grok_agents = mission.workplace.agents_md("grok").read_text(encoding="utf-8")
            self.assertIn("Keep act outputs on the board", grok_agents)
            self.assertTrue(mission.workplace.session_path("luna").is_file())
            for agent in ("flash", "pro", "luna", "grok"):
                self.assertIn(f"### {agent} ·", board)
                self.assertTrue(mission.workplace.cell_notes(agent).read_text(encoding="utf-8").strip())
            force.pause(mission)
            self.assertEqual(mission.workplace.read_state()["status"], "paused")
            self.assertTrue(mission.workplace.board_path.is_file())

    def test_compose_updates_cell_agents_md(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = _settings(temporary)
            force = Force(settings)
            mission = force.start(title="compose", mission="lab", sync=False)
            selected = force.compose(mission, "flash", "shell and grep")
            self.assertIn("shell", selected)
            self.assertIn("search", selected)
            markdown = mission.workplace.agents_md("flash").read_text(encoding="utf-8")
            self.assertIn("- shell", markdown)


class AdapterTest(unittest.TestCase):
    def test_cursor_sandbox_mode_matches_host_capability(self) -> None:
        expected = "disabled" if os.name == "nt" else "enabled"
        self.assertEqual(cursor_sandbox_mode(), expected)

    def test_cursor_model_list_parser(self) -> None:
        output = (
            "\x1b[32mcursor-grok-4.6-high\x1b[0m - Grok\n"
            "gpt-5.6-luna-high - Luna\n"
            "informational line without a model\n"
        )
        self.assertEqual(
            parse_cursor_model_list(output),
            ["cursor-grok-4.6-high", "gpt-5.6-luna-high"],
        )

    def test_cursor_auto_prefers_pinned_family(self) -> None:
        spec = load_settings().agents["grok"]
        spec = replace(spec, model="auto")
        selected, selection = resolve_cursor_model(
            spec,
            ["cursor-grok-4.5-high", "cursor-grok-4.6-high"],
        )
        self.assertEqual(selected, "cursor-grok-4.6-high")
        self.assertEqual(selection, "auto")

    def test_cursor_result_parser(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "IMF_DEMO_OK",
                "usage": {"inputTokens": 1, "outputTokens": 1},
            },
        )
        text, metadata = parse_cursor_json(stdout)
        self.assertEqual(text, "IMF_DEMO_OK")
        self.assertIn("usage", metadata)

    def test_mock_adapter_posts_plugin_list(self) -> None:
        spec = load_settings().agents["flash"]
        result = MockAdapter(spec).invoke(
            task="bounded test",
            context="",
            workspace=Path.cwd(),
            timeout=1,
            plugins=["feishu-board", "files"],
            cell="flash",
        )
        self.assertIn("## Findings", result.text)
        self.assertEqual(result.plugins, ["feishu-board", "files"])
        self.assertIn("## Lessons", result.text)

    def test_operative_report_contract_rejects_unstructured_text(self) -> None:
        with self.assertRaises(RuntimeError):
            validate_operative_report("free-form answer")


class FeishuPayloadTest(unittest.TestCase):
    def test_text_is_chunked_into_docx_paragraph_blocks(self) -> None:
        blocks = text_blocks("x" * 4000, chunk_size=1800)
        self.assertEqual(len(blocks), 3)
        self.assertTrue(all(block["block_type"] == 2 for block in blocks))

    def test_list_chats_follows_pagination(self) -> None:
        class FakeClient(FeishuClient):
            def __init__(self) -> None:
                self.calls = 0

            def request(self, method, path, body=None):
                del body
                self.calls += 1
                if method != "GET" or "/im/v1/chats" not in path:
                    raise AssertionError((method, path))
                if self.calls == 1:
                    return {
                        "data": {
                            "items": [{"chat_id": "oc_one", "name": "one"}],
                            "has_more": True,
                            "page_token": "next token",
                        },
                    }
                return {
                    "data": {
                        "items": [{"chat_id": "oc_two", "name": "two"}],
                        "has_more": False,
                    },
                }

        client = FakeClient()
        chats = client.list_chats()
        self.assertEqual([item["chat_id"] for item in chats], ["oc_one", "oc_two"])
        self.assertEqual(client.calls, 2)

    def test_format_post_is_signed(self) -> None:
        text = format_post("flash", "finding")
        self.assertTrue(text.startswith("### flash · "))
        self.assertIn("finding", text)


class LocalEnvTest(unittest.TestCase):
    def test_local_env_does_not_override_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / ".env"
            env_file.write_text(
                "IMF_TEST_VALUE=from-file\nIMF_FILE_ONLY=loaded\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"IMF_TEST_VALUE": "from-process"}, clear=False):
                os.environ.pop("IMF_FILE_ONLY", None)
                load_local_env(env_file)
                self.assertEqual(os.environ["IMF_TEST_VALUE"], "from-process")
                self.assertEqual(os.environ["IMF_FILE_ONLY"], "loaded")

    def test_siliconflow_provider_rewrites_deepseek_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text(
                "SILICONFLOW_API_KEY=sf-test-secret\n"
                "SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1\n"
                "DEEPSEEK_PROVIDER=siliconflow\n"
                "FLASH_MODEL=vendor/flash\n"
                "PRO_MODEL=vendor/pro\n",
                encoding="utf-8",
            )
            (root / "plugins").mkdir()
            (root / "plugins" / "catalog.json").write_text(
                json.dumps({"plugins": [{"id": "feishu-board", "title": "Board", "summary": "x"}]}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(root)
                self.assertEqual(settings.agents["flash"].api_key_env, "SILICONFLOW_API_KEY")
                self.assertEqual(settings.agents["flash"].base_url, "https://api.siliconflow.cn/v1")
                self.assertEqual(settings.agents["flash"].model, "vendor/flash")
                self.assertEqual(settings.agents["pro"].model, "vendor/pro")


class WebTest(unittest.TestCase):
    def test_web_ui_and_mission_api(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = _settings(temporary)
            server = make_server(settings, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                base = f"http://127.0.0.1:{port}"
                home = urlopen(base + "/").read().decode("utf-8")
                self.assertIn("Instant Message Force", home)
                roster = json.loads(urlopen(base + "/api/roster").read().decode("utf-8"))
                self.assertEqual(len(roster["agents"]), 4)
                self.assertEqual([item["ooda"] for item in roster["agents"]], ["observe", "orient", "decide", "act"])
                created = json.loads(
                    urlopen(
                        Request(
                            base + "/api/missions",
                            data=json.dumps(
                                {
                                    "title": "web-lab",
                                    "mission": "authorized local lab",
                                    "no_sync": True,
                                },
                            ).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                    ).read().decode("utf-8"),
                )
                self.assertEqual(created["title"], "web-lab")
                self.assertIn("flash", created["cells"])
                checked = json.loads(
                    urlopen(
                        Request(
                            base + f"/api/missions/{created['id']}/checkin",
                            data=json.dumps({"mock": True}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                    ).read().decode("utf-8"),
                )
                self.assertEqual(len(checked["checkins"]), 4)
                self.assertTrue(all(item["ok"] for item in checked["checkins"]))
                dispatched = json.loads(
                    urlopen(
                        Request(
                            base + f"/api/missions/{created['id']}/dispatch",
                            data=json.dumps({"task": "mock pass", "mock": True, "no_sync": True}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                    ).read().decode("utf-8"),
                )
                self.assertEqual(len(dispatched["results"]), 4)
                self.assertEqual(dispatched["results"][0]["agent"], "flash")
                self.assertEqual(dispatched["results"][-1]["agent"], "grok")
                board = json.loads(
                    urlopen(base + f"/api/missions/{created['id']}/board").read().decode("utf-8"),
                )
                self.assertIn("### flash ·", board["board"])
            finally:
                server.shutdown()
                server.server_close()


class ContextTest(unittest.TestCase):
    def test_excerpt_keeps_log_tail_after_budget(self) -> None:
        spec = load_settings().agents["grok"]
        budget = board_budget_chars(spec)
        board = "# IMF Board\n\n## Log\n\n" + ("flash saw A\n" * 20) + ("luna order Z\n" * (budget // 8))
        excerpt, compacted = excerpt_board(board, spec)
        self.assertTrue(compacted)
        self.assertLessEqual(len(excerpt), budget + 200)
        self.assertIn("luna order Z", excerpt)
        self.assertIn("compacted", excerpt)

    def test_small_board_is_not_compacted(self) -> None:
        spec = load_settings().agents["flash"]
        excerpt, compacted = excerpt_board("# IMF Board\n\n## Log\n\nhi\n", spec)
        self.assertFalse(compacted)
        self.assertIn("hi", excerpt)

    def test_lessons_append_under_notes(self) -> None:
        text = "# Agent: Flash\n\n## Plugins\n\n- files\n\n## Notes\n\nseed\n"
        self.assertEqual(extract_lessons("## Lessons\nKeep recon on the board.\n"), "Keep recon on the board.")
        updated = append_notes(text, "Keep recon on the board.", stamp="2026-08-17T00:00:00+00:00")
        self.assertIn("seed", updated)
        self.assertIn("Keep recon on the board.", updated)


class CheckinTest(unittest.TestCase):
    def test_all_four_operatives_check_in_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = _settings(temporary)
            force = Force(settings)
            mission = force.start(title="checkin", mission="lab", sync=False)
            results = force.check_in(mission, mock=True)
            self.assertEqual([item["agent"] for item in results], list(AGENT_IDS))
            self.assertTrue(all(item["ok"] for item in results))
            self.assertEqual(mission.workplace.read_state()["status"], "active")
            self.assertEqual(set(mission.workplace.read_state()["presence"]), set(AGENT_IDS))


class BoardMergeTest(unittest.TestCase):
    def test_local_unsynced_posts_survive_feishu_pull(self) -> None:
        local = "# IMF Board\n\n## Log\n\n### flash · one\n\nlocal\n"
        remote = "# IMF Board\n\n## Log\n\n### pro · one\n\nremote\n"
        merged = _merge_board(local, remote)
        self.assertIn("### flash · one", merged)
        self.assertIn("### pro · one", merged)


if __name__ == "__main__":
    unittest.main()
