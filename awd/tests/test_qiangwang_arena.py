"""Phase 1–2: Qiangwang PLATFORM_STUB adapter + round-state."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_arena():
    spec = importlib.util.spec_from_file_location(
        "_qw_arena_test", ROOT / "lib" / "arena.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QiangwangStubArenaTests(unittest.TestCase):
    def setUp(self):
        self.arena = load_arena()
        self.tmp = tempfile.TemporaryDirectory()
        self.runs = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stub_board_writes_round_state(self):
        env = {
            "PLATFORM": "qiangwang",
            "PLATFORM_STUB": "1",
            "SAPPER_ROOT": str(self.runs),
            "QW_STUB_ANCHOR": "1700000000",
            "QW_STUB_NOW": "1700000900",  # 1.5 rounds in → round_id=2
            "QW_ROUND_SECONDS": "600",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            client = self.arena.ArenaClient.load(None)
            board = client.board()
        self.assertTrue(board["stub"])
        self.assertEqual(board["platform"], "qiangwang")
        self.assertEqual(board["round_id"], 2)
        self.assertEqual(board["time_left_sec"], 300)
        self.assertGreaterEqual(len(board["challenges"]), 2)
        rs_path = self.runs / "round-state.json"
        self.assertTrue(rs_path.exists())
        rs = json.loads(rs_path.read_text(encoding="utf-8"))
        self.assertEqual(rs["round_id"], 2)
        self.assertEqual(rs["time_left_sec"], 300)
        self.assertTrue(rs["stub"])

    def test_list_challenges_exposes_unique_code(self):
        with mock.patch.dict(
            "os.environ",
            {"PLATFORM_STUB": "1", "SAPPER_ROOT": str(self.runs)},
            clear=False,
        ):
            rows = self.arena.ArenaClient.load(None).list_challenges()
        self.assertTrue(all(isinstance(r.get("unique_code"), str) for r in rows))
        self.assertIn("round_id", rows[0])

    def test_submit_flag_stub_accept_reject(self):
        with mock.patch.dict("os.environ", {"PLATFORM_STUB": "1"}, clear=False):
            c = self.arena.ArenaClient.load(None)
            self.assertTrue(c.submit_flag("web-1", "flag{x}")["correct"])
            self.assertFalse(c.submit_flag("web-1", "nope")["correct"])

    def test_submit_patch_and_reset_stub(self):
        artifact = self.runs / "patch.tar"
        artifact.write_text("stub", encoding="utf-8")
        with mock.patch.dict("os.environ", {"PLATFORM_STUB": "1"}, clear=False):
            c = self.arena.ArenaClient.load(None)
            ok = c.submit_patch("web-1", str(artifact))
            missing = c.submit_patch("web-1", str(self.runs / "missing.tar"))
            reset = c.reset("web-1")
        self.assertEqual(ok["check"], "pass")
        self.assertEqual(missing["check"], "fail")
        self.assertTrue(reset["abnormality"])

    def test_list_targets_stub(self):
        with mock.patch.dict("os.environ", {"PLATFORM_STUB": "1"}, clear=False):
            targets = self.arena.ArenaClient.load(None).list_targets("web-1")
        self.assertTrue(all(t["challenge"] == "web-1" for t in targets))
        self.assertTrue(all("host" in t and "port" in t for t in targets))
        self.assertTrue(all(":" in str(t["endpoint"]) for t in targets))

    def test_live_mode_refuses_invented_paths(self):
        with mock.patch.dict(
            "os.environ",
            {
                "PLATFORM_STUB": "0",
                "QW_BASE_URL": "https://example.invalid",
                "QW_TOKEN": "tok",
            },
            clear=False,
        ):
            c = self.arena.ArenaClient.load(None)
            with self.assertRaises(self.arena.ArenaError) as ctx:
                c.board()
            self.assertEqual(ctx.exception.code, "not_wired")

    def test_load_requires_creds_without_stub(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(self.arena.ArenaError):
                self.arena.ArenaClient.load(None)

    def test_qw_env_preferred_over_benchmark_alias(self):
        with mock.patch.dict(
            "os.environ",
            {
                "PLATFORM_STUB": "0",
                "QW_BASE_URL": "https://qw.example.invalid",
                "QW_TOKEN": "qw-tok",
                "BENCHMARK_BASE_URL": "https://bench.example.invalid",
                "BENCHMARK_TOKEN": "bench-tok",
            },
            clear=False,
        ):
            c = self.arena.ArenaClient.load(None)
        self.assertEqual(c.base_url, "https://qw.example.invalid")
        self.assertEqual(c.token, "qw-tok")
        self.assertFalse(c.stub)

    def test_write_template(self):
        store = self.runs / ".sapper"
        store.mkdir()
        path = self.arena.ArenaClient.write_template(store)
        cfg = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("Unknown", cfg["_comment"])
        self.assertTrue(str(cfg["token"]).startswith("ENV:QW_"))


if __name__ == "__main__":
    unittest.main()
