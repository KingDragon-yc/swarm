"""Phase 3: harvester, unlock gate, patcher stubs."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))

import arena  # noqa: E402
import harvester  # noqa: E402
import patcher  # noqa: E402
import unlock  # noqa: E402


def _write_stub_exp(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "ip, port = sys.argv[1], sys.argv[2]\n"
        "print(f'flag{{stub-{ip}-{port}}}')\n",
        encoding="utf-8",
    )


class UnlockGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.gate = unlock.UnlockGate(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_patch_blocked_before_first_blood(self):
        ok, reason = self.gate.can_submit_patch("web-1")
        self.assertFalse(ok)
        self.assertIn("FIRST_BLOOD", reason)
        with self.assertRaises(unlock.UnlockError):
            self.gate.require_patch("web-1")

    def test_first_blood_unlocks_and_quota_resets(self):
        self.gate.sync_round(1)
        self.gate.mark_first_blood("web-1", round_id=1)
        ok, _ = self.gate.can_submit_patch("web-1")
        self.assertTrue(ok)
        self.gate.record_patch_success("web-1", round_id=1)
        ok2, reason = self.gate.can_submit_patch("web-1")
        self.assertFalse(ok2)
        self.assertIn("PATCH_QUOTA", reason)
        # New round: FIRST_BLOOD persists, quota resets
        self.gate.sync_round(2)
        flags = self.gate.flags_for("web-1")
        self.assertEqual(flags["FIRST_BLOOD"], "yes")
        self.assertEqual(flags["PATCH_QUOTA"], "available")
        self.assertTrue(self.gate.can_submit_patch("web-1")[0])


class HarvesterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _write_stub_exp(self.root / "web-1" / "exploits" / "exp_v1.py")

    def tearDown(self):
        self.tmp.cleanup()

    def test_harvest_submits_and_unlocks(self):
        env = {
            "PLATFORM": "qiangwang",
            "PLATFORM_STUB": "1",
            "SAPPER_ROOT": str(self.root),
            "QW_STUB_ANCHOR": "1700000000",
            "QW_STUB_NOW": "1700000100",
            "QW_ROUND_SECONDS": "600",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            summary = harvester.harvest_once(
                runs_root=self.root,
                challenge_id="web-1",
                workers=2,
                submit=True,
            )
        self.assertEqual(summary["round_id"], 1)
        self.assertGreaterEqual(summary["submitted"], 1)
        self.assertIn("web-1", summary["unlocks"])
        ledger = json.loads((self.root / "harvest-ledger.json").read_text(encoding="utf-8"))
        self.assertTrue(any(e.get("status") == "submitted" for e in ledger["entries"]))
        # Round-tagged fact written
        facts = list((self.root / "web-1" / ".sapper" / "facts").glob("flag-r1-*.json"))
        self.assertTrue(facts)
        fact = json.loads(facts[0].read_text(encoding="utf-8"))
        self.assertEqual(fact["data"]["round_id"], 1)
        gate = unlock.UnlockGate(self.root)
        self.assertEqual(gate.flags_for("web-1")["FIRST_BLOOD"], "yes")

    def test_harvest_dedupes_second_sweep(self):
        env = {
            "PLATFORM_STUB": "1",
            "SAPPER_ROOT": str(self.root),
            "QW_STUB_ANCHOR": "1700000000",
            "QW_STUB_NOW": "1700000100",
            "QW_ROUND_SECONDS": "600",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            first = harvester.harvest_once(runs_root=self.root, challenge_id="web-1")
            second = harvester.harvest_once(runs_root=self.root, challenge_id="web-1")
        self.assertGreaterEqual(first["submitted"], 1)
        self.assertEqual(second["submitted"], 0)
        self.assertGreaterEqual(second["duplicates"], 1)

    def test_parse_endpoint_host_port(self):
        host, port = harvester.parse_endpoint(
            {"host": "1.2.3.4", "port": 8080, "endpoint": "1.2.3.4:8080"}
        )
        self.assertEqual((host, port), ("1.2.3.4", 8080))


class PatcherGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.artifact = self.root / "patch.bin"
        self.artifact.write_text("x", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_patcher_refuses_without_first_blood(self):
        env = {
            "PLATFORM_STUB": "1",
            "SAPPER_ROOT": str(self.root),
            "QW_STUB_ANCHOR": "1700000000",
            "QW_STUB_NOW": "1700000100",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            with self.assertRaises(unlock.UnlockError):
                patcher.submit_patch_gated(
                    "web-1", self.artifact, runs_root=self.root
                )

    def test_patcher_succeeds_after_unlock_and_consumes_quota(self):
        env = {
            "PLATFORM_STUB": "1",
            "SAPPER_ROOT": str(self.root),
            "QW_STUB_ANCHOR": "1700000000",
            "QW_STUB_NOW": "1700000100",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            gate = unlock.UnlockGate(self.root)
            gate.mark_first_blood("web-1", round_id=1)
            result = patcher.submit_patch_gated(
                "web-1", self.artifact, runs_root=self.root, gate=gate
            )
            self.assertEqual(result["gate"]["PATCH_QUOTA"], "used")
            with self.assertRaises(unlock.UnlockError):
                patcher.submit_patch_gated(
                    "web-1", self.artifact, runs_root=self.root, gate=gate
                )

    def test_sandbox_red_line(self):
        env = {"PLATFORM_STUB": "1", "SAPPER_ROOT": str(self.root)}
        with mock.patch.dict("os.environ", env, clear=False):
            gate = unlock.UnlockGate(self.root)
            gate.mark_first_blood("web-1", round_id=1)
            with self.assertRaises(unlock.UnlockError) as ctx:
                patcher.submit_patch_gated(
                    "web-1",
                    self.artifact,
                    runs_root=self.root,
                    gate=gate,
                    sandbox_ok=False,
                )
            self.assertEqual(ctx.exception.code, "sandbox_failed")


class BoardUnlockEnrichmentTests(unittest.TestCase):
    def test_board_includes_unlock_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "PLATFORM_STUB": "1",
                "SAPPER_ROOT": tmp,
                "QW_STUB_ANCHOR": "1700000000",
                "QW_STUB_NOW": "1700000100",
            }
            with mock.patch.dict("os.environ", env, clear=False):
                gate = unlock.UnlockGate(Path(tmp))
                gate.mark_first_blood("misc-1", round_id=1)
                board = arena.ArenaClient.load(None).board()
            misc = [c for c in board["challenges"] if c.get("id") == "misc-1"][0]
            self.assertEqual(misc["FIRST_BLOOD"], "yes")
            self.assertIn("PATCH_QUOTA", misc)
            self.assertIn("unlock", board)


if __name__ == "__main__":
    unittest.main()
