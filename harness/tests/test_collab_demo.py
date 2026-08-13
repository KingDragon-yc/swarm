from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collab_demo.config import AgentSpec, load_local_env, load_settings
from collab_demo.core import EventLedger, route_task
from collab_demo.feishu import FeishuClient, text_blocks
from collab_demo.providers import (
    MockAdapter,
    parse_cursor_json,
    parse_cursor_model_list,
    resolve_cursor_model,
)


class RoutingTest(unittest.TestCase):
    def test_simple_task_stays_with_orchestrator(self) -> None:
        decision = route_task("识别 note.txt 的编码并解码")
        self.assertEqual(decision.mode, "solo")
        self.assertEqual(decision.suggested_agents, ())

    def test_complex_review_uses_at_most_two_specialists(self) -> None:
        decision = route_task("全项目审计认证攻击链，并独立复核可能的绕过")
        self.assertEqual(decision.mode, "delegate")
        self.assertLessEqual(len(decision.suggested_agents), 2)
        self.assertEqual(decision.suggested_agents[0], "claude")

    def test_explicit_agent_is_honored_without_fanout(self) -> None:
        decision = route_task("请让 Gemini 复核这个结论")
        self.assertEqual(decision.suggested_agents, ("gemini",))

    def test_agent_name_adjacent_to_chinese_is_detected(self) -> None:
        decision = route_task("请让Gemini复核这个结论")
        self.assertEqual(decision.suggested_agents, ("gemini",))


class LedgerTest(unittest.TestCase):
    def test_append_only_ledger_redacts_configured_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret-123456"}):
                ledger = EventLedger.create(state_dir, "safe task", Path(temporary))
                ledger.append(
                    actor="gpt",
                    kind="test",
                    summary="key=secret-123456",
                    details={"raw": "secret-123456"},
                )
            raw = ledger.events_path.read_text(encoding="utf-8")
            self.assertNotIn("secret-123456", raw)
            self.assertIn("<redacted:DEEPSEEK_API_KEY>", raw)


class AdapterTest(unittest.TestCase):
    def test_cursor_model_list_parser_strips_ansi_and_description(self) -> None:
        output = (
            "\x1b[32mclaude-opus-5-thinking-high\x1b[0m - Opus 5\n"
            "gemini-3.6-flash-high - Gemini 3.6 Flash\n"
            "informational line without a model\n"
        )
        self.assertEqual(
            parse_cursor_model_list(output),
            ["claude-opus-5-thinking-high", "gemini-3.6-flash-high"],
        )

    def test_cursor_auto_model_uses_vendor_preference(self) -> None:
        spec = AgentSpec(
            name="grok",
            vendor="xAI via Cursor",
            backend="cursor",
            role="review",
            model="auto",
        )
        selected, selection = resolve_cursor_model(
            spec,
            ["cursor-grok-4.5-high", "cursor-grok-4.6-high"],
        )
        self.assertEqual(selected, "cursor-grok-4.6-high")
        self.assertEqual(selection, "auto")

    def test_unavailable_cursor_pin_reports_candidates(self) -> None:
        spec = AgentSpec(
            name="gemini",
            vendor="Google via Cursor",
            backend="cursor",
            role="review",
            model="retired-model",
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "gemini-3.6-flash-high",
        ):
            resolve_cursor_model(spec, ["gemini-3.6-flash-high"])

    def test_cursor_result_parser(self) -> None:
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "BUZZ_DEMO_OK",
                "usage": {"inputTokens": 1, "outputTokens": 1},
            },
        )
        text, metadata = parse_cursor_json(stdout)
        self.assertEqual(text, "BUZZ_DEMO_OK")
        self.assertIn("usage", metadata)

    def test_mock_adapter_has_structured_report(self) -> None:
        spec = load_settings().agents["deepseek"]
        result = MockAdapter(spec).invoke(
            task="bounded test",
            context="",
            workspace=Path.cwd(),
            timeout=1,
        )
        self.assertIn("## Findings", result.text)
        self.assertTrue(result.metadata["mock"])


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
                self.assert_request(method, path)
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

            @staticmethod
            def assert_request(method, path):
                if method != "GET" or "/im/v1/chats" not in path:
                    raise AssertionError((method, path))

        client = FakeClient()
        chats = client.list_chats()
        self.assertEqual([item["chat_id"] for item in chats], ["oc_one", "oc_two"])
        self.assertEqual(client.calls, 2)


class RosterTest(unittest.TestCase):
    def test_fixed_roster_contains_eight_distinct_agents(self) -> None:
        agents = load_settings().agents
        self.assertEqual(
            set(agents),
            {"gpt", "claude", "gemini", "grok", "deepseek", "kimi", "glm", "qwen"},
        )
        self.assertEqual(len(agents), 8)


class LocalEnvTest(unittest.TestCase):
    def test_local_env_does_not_override_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / ".env"
            env_file.write_text(
                "BUZZ_TEST_VALUE=from-file\nBUZZ_FILE_ONLY=loaded\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"BUZZ_TEST_VALUE": "from-process"},
                clear=False,
            ):
                os.environ.pop("BUZZ_FILE_ONLY", None)
                load_local_env(env_file)
                self.assertEqual(os.environ["BUZZ_TEST_VALUE"], "from-process")
                self.assertEqual(os.environ["BUZZ_FILE_ONLY"], "loaded")

    def test_siliconflow_provider_uses_shared_endpoint_and_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text(
                "SILICONFLOW_API_KEY=sf-test-secret\n"
                "SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1\n"
                "GLM_PROVIDER=siliconflow\n"
                "GLM_MODEL=vendor/model-id\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                spec = load_settings(root).agents["glm"]
                self.assertEqual(spec.api_key_env, "SILICONFLOW_API_KEY")
                self.assertEqual(spec.base_url, "https://api.siliconflow.cn/v1")
                self.assertEqual(spec.model, "vendor/model-id")
                self.assertTrue(spec.configured)


if __name__ == "__main__":
    unittest.main()
