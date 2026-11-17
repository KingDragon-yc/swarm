import contextlib
import io
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import sapper  # noqa: E402

SETTINGS_PATH = ROOT / "lib" / "_settings_patch.py"
_spec = importlib.util.spec_from_file_location("settings_patch_for_tests", SETTINGS_PATH)
settings_patch = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(settings_patch)


class GraphIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = sapper.Store(Path(self.tmp.name) / ".sapper")
        self.store.init("test", "ship it")

    def tearDown(self):
        self.tmp.cleanup()

    def test_corrupt_node_is_skipped_and_reported_by_validate(self):
        (self.store.facts_dir / "f001.json").write_text('{"id":"f001"}\n', encoding="utf-8")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            rendered = sapper.render_graph_text(self.store)
        self.assertIn("goal: ship it", rendered)
        self.assertIn("missing string 'description'", stderr.getvalue())
        errors = sapper.validate_graph(self.store)
        self.assertTrue(any("f001.json" in error for error in errors))

    def test_init_refuses_non_empty_directory(self):
        root = Path(self.tmp.name) / "new-store"
        root.mkdir()
        (root / "unrelated.txt").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(sapper.FGError, "non-empty directory"):
            sapper.Store(root).init("", "goal")
        self.assertTrue((root / "unrelated.txt").exists())

    def test_validate_detects_goal_metadata_drift(self):
        goal = self.store.facts_dir / "goal.json"
        data = json.loads(goal.read_text(encoding="utf-8"))
        data["description"] = "different goal"
        goal.write_text(json.dumps(data), encoding="utf-8")
        errors = sapper.validate_graph(self.store)
        self.assertIn("goal fact description does not match project goal", errors)

    def test_validate_detects_intent_state_mismatch(self):
        seed = self.store.add_fact("seed", "test", None)
        intent = self.store.add_intent([seed["id"]], "work", "test")
        path = self.store.intents_dir / f"{intent['id']}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["status"] = "done"
        path.write_text(json.dumps(data), encoding="utf-8")
        errors = sapper.validate_graph(self.store)
        self.assertTrue(any("completed status requires a target" in error for error in errors))

    def test_confirmation_is_enforced_at_write_boundary(self):
        fact = self.store.add_fact("starting point", "test", None)
        intent = self.store.add_intent(
            [fact["id"]], "delete data", "test", requires_confirmation=True
        )
        with self.assertRaisesRegex(sapper.FGError, "requires confirmation"):
            self.store.conclude_with_new_fact(intent["id"], "must not run", "test")
        self.store.confirm_intent(intent["id"])
        self.store.claim_intent(intent["id"], "owner")
        with self.assertRaisesRegex(sapper.FGError, "claimed by owner"):
            self.store.conclude_with_new_fact(intent["id"], "wrong worker", "other")
        result, concluded = self.store.conclude_with_new_fact(
            intent["id"], "approval was recorded", "owner"
        )
        self.assertEqual(concluded["to"], result["id"])

    def test_lookup_ids_cannot_escape_store(self):
        with self.assertRaisesRegex(sapper.FGError, "invalid fact id"):
            self.store.get_fact("../../outside")

    def test_stale_counter_never_overwrites_existing_node(self):
        first = self.store.add_fact("first", "test", None)
        project = self.store.load_project()
        project["counters"]["fact"] = 0
        self.store.save_project(project)
        second = self.store.add_fact("second", "test", None)
        self.assertEqual(first["id"], "f001")
        self.assertEqual(second["id"], "f002")
        self.assertEqual(self.store.get_fact("f001")["description"], "first")

    def test_fact_from_intent_closes_the_intent_atomically(self):
        seed = self.store.add_fact("seed", "test", None)
        intent = self.store.add_intent([seed["id"]], "observe", "test")
        fact = self.store.add_fact("observed", "test", intent["id"])
        self.assertEqual(self.store.get_intent(intent["id"])["to"], fact["id"])
        self.assertEqual(sapper.validate_graph(self.store), [])

    def test_linking_existing_fact_sets_its_source_edge(self):
        seed = self.store.add_fact("seed", "test", None)
        result = self.store.add_fact("already observed", "test", None)
        intent = self.store.add_intent([seed["id"]], "link result", "test")
        self.store.conclude_intent(intent["id"], result["id"], "test")
        self.assertEqual(self.store.get_fact(result["id"])["source_intent"], intent["id"])
        self.assertEqual(sapper.validate_graph(self.store), [])

    def test_pick_claim_skips_another_workers_lease(self):
        seed = self.store.add_fact("seed", "test", None)
        first = self.store.add_intent([seed["id"]], "first", "test")
        second = self.store.add_intent([seed["id"]], "second", "test")
        self.store.claim_intent(first["id"], "other-worker")
        args = type("Args", (), {
            "store": str(self.store.root), "claim": True,
            "worker": "my-worker", "any": False,
        })()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            sapper.cmd_pick(args)
        self.assertEqual(output.getvalue().strip(), second["id"])

    def test_static_view_escapes_script_terminators(self):
        self.store.add_fact("</script><script>alert(1)</script>", "test", None)
        html = sapper._render_static_html(self.store)
        self.assertNotIn("</script><script>", html)
        self.assertIn(r"\u003c/script\u003e", html)

    def test_prompt_substitution_does_not_rewrite_inserted_braces(self):
        self.store.add_fact("literal {goal} marker", "test", None)
        prompt = sapper.render_prompt(self.store, "{graph_yaml}\nGOAL={goal}")
        self.assertIn("literal {goal} marker", prompt)
        self.assertTrue(prompt.endswith("GOAL=ship it"))

    def test_brainstorm_template_token_does_not_rewrite_graph_text(self):
        self.store.add_fact("literal {max_options} marker", "test", None)
        args = type("Args", (), {
            "max_options": 2, "dry_run": True,
        })()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            sapper._dispatch_brainstorm(
                self.store, ROOT / "templates", "sonnet", 10, False, ROOT, args
            )
        self.assertIn("literal {max_options} marker", output.getvalue())

    def test_verify_payload_requires_actionable_issues_on_failure(self):
        with self.assertRaisesRegex(sapper.FGError, "explain"):
            sapper._parse_verify_payload({"verified": False, "issues": ""})
        with self.assertRaisesRegex(sapper.FGError, "leave 'issues' empty"):
            sapper._parse_verify_payload({"verified": True, "issues": "warning"})

    def test_dispatch_verify_returns_failure_status(self):
        seed = self.store.add_fact("seed", "test", None)
        intent = self.store.add_intent([seed["id"]], "check", "test")
        result, _ = self.store.conclude_with_new_fact(intent["id"], "result", "test")
        args = type("Args", (), {
            "intent": intent["id"], "dry_run": False,
        })()
        with mock.patch.object(
            sapper,
            "run_claude",
            return_value={"result": '{"accepted": true, "data": {"verified": false, "issues": "mismatch"}}'},
        ):
            rc = sapper._dispatch_verify(
                self.store, ROOT / "templates", "sonnet", 10, False, ROOT, args
            )
        self.assertEqual(rc, 1)

    def test_completed_graph_validates_and_duplicate_completion_is_rejected(self):
        fact = self.store.add_fact("evidence", "test", None)
        self.store.complete_project([fact["id"]], "finish", "test")
        self.assertEqual(self.store.load_project()["status"], "completed")
        self.assertEqual(sapper.validate_graph(self.store), [])
        with self.assertRaisesRegex(sapper.FGError, "already completed"):
            self.store.complete_project([fact["id"]], "finish again", "test")

    def test_brainstorm_batches_are_unique(self):
        seed = self.store.add_fact("seed", "test", None)
        payload = {
            "accepted": True,
            "data": {
                "options": [
                    {"from": [seed["id"]], "title": "one", "description": "first"}
                ]
            },
        }
        sapper._apply_brainstorm(self.store, payload)
        sapper._apply_brainstorm(self.store, payload)
        docs = [i["doc"] for i in self.store.list_intents()]
        self.assertEqual(len(docs), 2)
        tags = [line for doc in docs for line in doc.splitlines() if line.startswith("[brainstorm batch:")]
        self.assertEqual(len(set(tags)), 2)

    def test_brainstorm_pick_preserves_abandoned_edges(self):
        seed = self.store.add_fact("seed", "test", None)
        payload = {
            "accepted": True,
            "data": {"options": [
                {"from": [seed["id"]], "title": "one", "description": "first"},
                {"from": [seed["id"]], "title": "two", "description": "second"},
            ]},
        }
        sapper._apply_brainstorm(self.store, payload)
        picked = self.store.list_intents()[0]
        args = type("Args", (), {"store": str(self.store.root), "id": picked["id"], "purge": False})()
        sapper.cmd_brainstorm_pick(args)
        intents = self.store.list_intents()
        self.assertEqual(sum(i["to"] is None for i in intents), 1)
        self.assertEqual(sum(i["to"] is not None for i in intents), 1)
        self.assertEqual(sapper.validate_graph(self.store), [])


class SettingsPatchTests(unittest.TestCase):
    def test_settings_patch_is_atomic_and_preserves_unrelated_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(
                json.dumps({"hooks": {"SessionStart": [{"matcher": "x", "hooks": [
                    {"type": "command", "command": "echo keep"
                    }
                ]}]}}),
                encoding="utf-8",
            )
            changed, _ = settings_patch.add(path, "/tmp/sapper-hook.py")
            self.assertTrue(changed)
            data = json.loads(path.read_text(encoding="utf-8"))
            commands = [
                h["command"]
                for groups in data["hooks"].values()
                for group in groups
                for h in group.get("hooks", [])
            ]
            self.assertIn("echo keep", commands)
            self.assertEqual(sum("sapper-hook.py" in command for command in commands), 2)
            settings_patch.remove(path, "/tmp/sapper-hook.py")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["hooks"]["SessionStart"][0]["hooks"][0]["command"], "echo keep")

    def test_settings_patch_rejects_non_object_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                settings_patch.add(path, "/tmp/sapper-hook.py")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                settings_patch.remove(path, "/tmp/sapper-hook.py")

    def test_settings_validation_catches_malformed_event_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"hooks": {"SessionStart": {}}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hooks.SessionStart"):
                settings_patch.validate(path)


class PeerConfigTests(unittest.TestCase):
    def test_malformed_peer_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = sapper.Store(Path(tmp) / ".sapper")
            store.init("", "peers")
            (store.root / sapper.PEERS_FILE).write_text(
                json.dumps({"version": 1, "channel": "x", "peers": {"bad name": {"target": "x"}}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(sapper.FGError, "invalid peer name"):
                sapper._load_peers(store)

    def test_setup_preflights_invalid_settings_before_creating_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".claude").mkdir()
            (project / ".claude" / "settings.json").write_text("[]", encoding="utf-8")
            old_cwd = Path.cwd()
            try:
                import os
                os.chdir(project)
                args = type("Args", (), {
                    "store": ".sapper", "goal": "should not initialize",
                    "origin": "", "agents": False, "force": False,
                })()
                with self.assertRaisesRegex(sapper.FGError, "JSON object"):
                    sapper.cmd_setup(args)
            finally:
                os.chdir(old_cwd)
            self.assertFalse((project / ".sapper" / "project.json").exists())


if __name__ == "__main__":
    unittest.main()


class TypedFactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = sapper.Store(Path(self.tmp.name) / ".sapper")
        self.store.init("test", "capture the flag")

    def tearDown(self):
        self.tmp.cleanup()

    def test_typed_fact_roundtrip_and_render(self):
        fact = self.store.add_fact(
            "nginx 1.18 at 10.0.2.15:80", "user",
            kind="service", data={"host": "10.0.2.15", "port": 80},
        )
        self.assertEqual(fact["kind"], "service")
        self.assertEqual(fact["data"]["port"], "80")
        text = sapper.render_graph_text(self.store)
        self.assertIn("<service>", text)
        self.assertIn("port=80", text)
        yaml = sapper._to_yaml_like(sapper.build_graph_view(self.store))
        self.assertIn("<service>", yaml)
        # prompt rendering scrubs braces so flag{...} cannot eat placeholders
        self.assertIn("port=80", yaml)

    def test_unknown_kind_and_bad_data_rejected(self):
        with self.assertRaises(sapper.FGError):
            self.store.add_fact("x", "user", kind="exploit")
        with self.assertRaises(sapper.FGError):
            self.store.add_fact("x", "user", kind="cred", data={"bad key!": "1"})

    def test_validate_rejects_invalid_kind_on_disk(self):
        fact = self.store.add_fact("x", "user", kind="cred")
        p = self.store.facts_dir / f"{fact['id']}.json"
        node = json.loads(p.read_text(encoding="utf-8"))
        node["kind"] = "exploit"
        p.write_text(json.dumps(node), encoding="utf-8")
        errors = sapper.validate_graph(self.store)
        self.assertTrue(any("kind" in e for e in errors))

    def test_explore_payload_carries_kind_and_data(self):
        payload = {"accepted": True, "data": {
            "description": "RCE confirmed",
            "kind": "vuln",
            "data": {"cve": "CVE-2021-41773"},
        }}
        title, desc, doc, kind, data = sapper._parse_explore_payload(payload)
        self.assertEqual(kind, "vuln")
        self.assertEqual(data, {"cve": "CVE-2021-41773"})


class FlagLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = sapper.Store(Path(self.tmp.name) / ".sapper")
        self.store.init("test", "capture the flag")

    def tearDown(self):
        self.tmp.cleanup()

    def test_flag_record_and_dedupe(self):
        fact = self.store.add_fact(
            "flag captured: flag{a}", "agent",
            kind="flag", data={"value": "flag{a}", "challenge": "c01", "status": "recorded"},
        )
        dup = sapper._find_flag_fact(self.store, "flag{a}")
        self.assertEqual(dup["id"], fact["id"])
        self.assertIsNone(sapper._find_flag_fact(self.store, "flag{b}"))

    def test_flag_status_lifecycle_and_type_guard(self):
        fact = self.store.add_fact(
            "flag captured: flag{a}", "agent",
            kind="flag", data={"value": "flag{a}", "status": "recorded"},
        )
        updated = self.store.update_flag_fact(fact["id"], "submitted", '{"ok":true}')
        self.assertEqual(updated["data"]["status"], "submitted")
        self.assertEqual(updated["data"]["result"], '{"ok":true}')
        plain = self.store.add_fact("just text", "agent")
        with self.assertRaises(sapper.FGError):
            self.store.update_flag_fact(plain["id"], "submitted", None)
        with self.assertRaises(sapper.FGError):
            self.store.update_flag_fact(fact["id"], "hacked-status", None)

    def test_status_shows_flag_ledger(self):
        self.store.add_fact(
            "flag captured: flag{z}", "agent",
            kind="flag", data={"value": "flag{z}", "challenge": "c09", "status": "submitted"},
        )
        text = sapper.render_status(self.store)
        self.assertIn("flag(s)", text)
        self.assertIn("status=submitted", text)
        self.assertIn("challenge=c09", text)


class DeadlineTests(unittest.TestCase):
    def test_minutes_and_iso_and_conflict(self):
        import time as _t
        a = type("A", (), {"deadline": None, "minutes": 5})()
        d = sapper.deadline_from_args(a)
        self.assertGreater(d, _t.time())
        b = type("B", (), {"deadline": "2026-10-17T13:00:00", "minutes": None})()
        self.assertIsNotNone(sapper.deadline_from_args(b))
        c = type("C", (), {"deadline": "2026-10-17T13:00:00+08:00", "minutes": 5})()
        with self.assertRaises(sapper.FGError):
            sapper.deadline_from_args(c)
        bad = type("D", (), {"deadline": "not-a-date", "minutes": None})()
        with self.assertRaises(sapper.FGError):
            sapper.deadline_from_args(bad)
        self.assertEqual(sapper.deadline_from_args(type("E", (), {"deadline": None, "minutes": None})()), None)

    def test_format_time_budget(self):
        self.assertEqual(sapper.format_time_budget(None), "")
        self.assertIn("TIME IS UP",
                      sapper.format_time_budget(0))  # 0 -> epoch 1970, always past
        self.assertIn("remaining", sapper.format_time_budget(_t.time() + 600))


import time as _t  # noqa: E402  (DeadlineTests)


class ArenaClientTests(unittest.TestCase):
    """Qiangwang adapter keeps ArenaError/load shape; TSecBench HTTP contract retired."""

    def _arena(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("_arena_test", ROOT / "lib" / "arena.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_stub_board_contract(self):
        arena = self._arena()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {
                "PLATFORM_STUB": "1",
                "SAPPER_ROOT": tmp,
                "QW_STUB_ANCHOR": "1700000000",
                "QW_STUB_NOW": "1700000000",
                "QW_ROUND_SECONDS": "600",
            }):
                client = arena.ArenaClient.load(None)
                board = client.board()
                self.assertEqual(board["round_id"], 1)
                self.assertEqual(board["time_left_sec"], 600)
                self.assertTrue((Path(tmp) / "round-state.json").exists())

    def test_config_resolution_env_and_file(self):
        arena = self._arena()
        with mock.patch.dict("os.environ", {"PLATFORM_STUB": "0",
                                            "BENCHMARK_BASE_URL": "http://env-base",
                                            "BENCHMARK_TOKEN": "env-tok"}):
            c = arena.ArenaClient.load(None)
            self.assertEqual(c.base_url, "http://env-base")
            self.assertEqual(c.token, "env-tok")
            self.assertFalse(c.stub)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "arena.json").write_text(json.dumps(
                {"base_url": "http://file-base", "token": "ENV:QW_TOKEN"}), encoding="utf-8")
            with mock.patch.dict("os.environ", {"PLATFORM_STUB": "0", "QW_TOKEN": "env-tok"}):
                c = arena.ArenaClient.load(Path(tmp))
                self.assertEqual(c.base_url, "http://file-base")
                self.assertEqual(c.token, "env-tok")
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(arena.ArenaError):
                arena.ArenaClient.load(None)
        with self.assertRaises(arena.ArenaError):
            arena.ArenaClient("http://x", "", stub=False)

    def test_live_start_is_not_wired(self):
        arena = self._arena()
        client = arena.ArenaClient("http://x", "t", stub=False)
        with self.assertRaises(arena.ArenaError) as ctx:
            client.start_challenge("c1")
        self.assertEqual(ctx.exception.code, "not_wired")


class ArenaErrorBridgeTests(unittest.TestCase):
    """Modules loaded through sapper._load_arena_module() must raise ArenaError
    catchable as FGError (keeps commander/bench except FGError guards working)."""

    def test_loaded_arena_error_subclasses_fgerror(self):
        arena = sapper._load_arena_module()
        self.assertTrue(issubclass(arena.ArenaError, sapper.FGError))

    def test_not_wired_caught_as_fgerror_via_client_shape(self):
        arena = sapper._load_arena_module()
        client = arena.ArenaClient("http://x", "t", stub=False)
        with self.assertRaises(sapper.FGError):
            # After rebind, ArenaError is an FGError subclass.
            raise arena.ArenaError("not wired", code="not_wired")
        with self.assertRaises(arena.ArenaError) as ctx:
            client.board()
        self.assertEqual(ctx.exception.code, "not_wired")
        self.assertIsInstance(ctx.exception, sapper.FGError)


class CommanderTests(unittest.TestCase):
    def test_parse_command_payload(self):
        ok = {"accepted": True, "data": {"action": "work", "target": "c03", "minutes": 25, "note": "close"}}
        self.assertEqual(sapper._parse_command_payload(ok), ("work", "c03", 25, "close"))
        stop = {"accepted": True, "data": {"action": "stop", "reason": "all done"}}
        self.assertEqual(sapper._parse_command_payload(stop), ("stop", "", 0, "all done"))
        rej = {"accepted": False, "reason": "bad board"}
        self.assertEqual(sapper._parse_command_payload(rej)[0], "reject")
        with self.assertRaises(sapper.FGError):
            sapper._parse_command_payload({"accepted": True, "data": {"action": "hack"}})
        with self.assertRaises(sapper.FGError):
            sapper._parse_command_payload({"accepted": True, "data": {"action": "work", "minutes": 10}})
        with self.assertRaises(sapper.FGError):
            sapper._parse_command_payload({"accepted": True, "data": {"action": "work", "target": "c1", "minutes": 0}})

    def test_board_summary_and_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("c01", "c02"):
                store = sapper.Store(root / name / ".sapper")
                store.init("t", f"flag {name}")
            sapper.Store(root / "c01" / ".sapper").add_fact(
                "flag captured: flag{a}", "agent",
                kind="flag", data={"value": "flag{a}", "challenge": "c01", "status": "submitted"})
            rows = sapper._board_summary(root)
            self.assertEqual([r["name"] for r in rows], ["c01", "c02"])
            by_name = {r["name"]: r for r in rows}
            self.assertEqual(by_name["c01"]["flags_submitted"], 1)
            self.assertEqual(by_name["c02"]["flags_submitted"], 0)
            board = sapper.render_board(rows)
            self.assertIn("c01", board)
            self.assertIn("flags(sub/rec)", board)


class FleetTests(unittest.TestCase):
    def test_fleet_pane_commands(self):
        cfg = {"session": "s", "root": "runs", "deadline": "2026-10-17T13:00:00",
               "panes": []}
        engine = sapper._sapper_engine_prefix()
        self.assertEqual(
            sapper._fleet_pane_command(cfg, {"type": "command"}),
            f"{engine} command --root runs --deadline 2026-10-17T13:00:00")
        self.assertEqual(
            sapper._fleet_pane_command(cfg, {"type": "auto:c02", "extra_args": "--verify"}),
            f"{engine} --store runs/c02/.sapper auto --deadline 2026-10-17T13:00:00 --verify")
        self.assertEqual(
            sapper._fleet_pane_command(cfg, {"type": "auto:c02"}, base_dir=Path("/w")),
            f"cd /w && {engine} --store runs/c02/.sapper auto --deadline 2026-10-17T13:00:00")
        self.assertEqual(
            sapper._fleet_pane_command({"root": "runs", "panes": []}, {"type": "shell", "command": "top"}),
            "top")
        with self.assertRaises(sapper.FGError):
            sapper._fleet_pane_command(cfg, {"type": "warp"})
        with self.assertRaises(sapper.FGError):
            sapper._fleet_pane_command(cfg, {"type": "shell"})

    def test_fleet_init_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                import os
                os.chdir(tmp)
                args = type("A", (), {"action": "init", "file": "fleet.json"})()
                self.assertEqual(sapper.cmd_fleet(args), 0)
                cfg = sapper._fleet_load(Path("fleet.json"))
                self.assertTrue(cfg["panes"])
                args2 = type("A", (), {"action": "init", "file": "fleet.json"})()
                with self.assertRaises(sapper.FGError):
                    sapper.cmd_fleet(args2)
            finally:
                os.chdir(old)


class HireFireTests(unittest.TestCase):
    def test_parse_command_payload_hire_fire(self):
        hire = {"accepted": True, "data": {"action": "hire", "target": "c07", "minutes": 30}}
        self.assertEqual(sapper._parse_command_payload(hire), ("hire", "c07", 30, ""))
        fire = {"accepted": True, "data": {"action": "fire", "target": "c07", "note": "stuck"}}
        self.assertEqual(sapper._parse_command_payload(fire), ("fire", "c07", 0, "stuck"))
        with self.assertRaises(sapper.FGError):
            sapper._parse_command_payload({"accepted": True, "data": {"action": "warp", "target": "c"}})
        with self.assertRaises(sapper.FGError):
            sapper._parse_command_payload({"accepted": True, "data": {"action": "hire", "minutes": 10}})
        with self.assertRaises(sapper.FGError):
            sapper._parse_command_payload({"accepted": True, "data": {"action": "hire", "target": "c", "minutes": -1}})

    def test_active_workers_parsing(self):
        with mock.patch.object(sapper, "_tmux") as tm:
            tm.return_value = mock.Mock(returncode=1)
            self.assertEqual(sapper._active_workers("nope"), set())
            tm.return_value = mock.Mock(returncode=0, stdout="commander\nw-c01\nw-c07\nboard\n")
            self.assertEqual(sapper._active_workers("s"), {"c01", "c07"})

    def test_board_render_worker_column(self):
        rows = [
            {"name": "c01", "status": "active", "flags_submitted": 1, "flags_recorded": 0,
             "kinds": "1 flag", "open_intents": 2, "last_activity": "t", "goal": "g", "worker": "yes"},
            {"name": "c02", "status": "active", "flags_submitted": 0, "flags_recorded": 0,
             "kinds": "-", "open_intents": 0, "last_activity": "t", "goal": "g", "worker": "-"},
        ]
        board = sapper.render_board(rows)
        self.assertIn("worker", board)
        self.assertIn("worker=yes means", board)


import os  # noqa: E402  (KeyRotationTests)


class KeyRotationTests(unittest.TestCase):
    def test_quota_matcher(self):
        self.assertTrue(sapper._is_quota_error("insufficient balance (quota exceeded)"))
        self.assertTrue(sapper._is_quota_error("Error 401: Unauthorized"))
        self.assertTrue(sapper._is_quota_error("账户欠费，请充值"))
        self.assertFalse(sapper._is_quota_error("connection timed out"))
        self.assertFalse(sapper._is_quota_error("claude exited 2: bad args"))

    def test_pool_load_and_rotate(self):
        pool_json = json.dumps([
            {"base_url": "http://a", "token": "t1", "model": "m1"},
            {"base_url": "http://b", "token": "t2", "model": "m2"},
        ])
        with mock.patch.dict("os.environ", {
            "SAPPER_KEY_POOL": pool_json, "ANTHROPIC_AUTH_TOKEN": "t1",
            "ANTHROPIC_BASE_URL": "http://a", "SAPPER_MODEL": "m1"}):
            self.assertTrue(sapper._rotate_key())
            self.assertEqual(os.environ["ANTHROPIC_AUTH_TOKEN"], "t2")
            self.assertEqual(os.environ["ANTHROPIC_BASE_URL"], "http://b")
            self.assertEqual(os.environ["SAPPER_MODEL"], "m2")
        with mock.patch.dict("os.environ", {"SAPPER_KEY_POOL": "not json"}):
            self.assertEqual(sapper._load_key_pool(), [])
        with mock.patch.dict("os.environ", {"SAPPER_KEY_POOL": ""}):
            self.assertFalse(sapper._rotate_key())
        # single-entry pool: rotate returns True but keeps the key
        with mock.patch.dict("os.environ", {
            "SAPPER_KEY_POOL": json.dumps([{"base_url": "http://a", "token": "t1"}]),
            "ANTHROPIC_AUTH_TOKEN": "t1"}):
            self.assertTrue(sapper._rotate_key())
            self.assertEqual(os.environ["ANTHROPIC_AUTH_TOKEN"], "t1")


class HookExecutionTests(unittest.TestCase):
    """Regression for #2: the hook script itself must run and emit valid JSON.

    SettingsPatchTests only asserted the command string was written; the hook
    binary was never executed (its fg->sapper rename import bug shipped silently
    because Claude Code swallows hook stderr).
    """

    def _run_hook(self, event, cwd, stdin=None):
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(ROOT / "lib" / "sapper-hook.py"), event],
            capture_output=True, text=True, timeout=30,
            cwd=cwd, input=stdin,
        )
        return proc

    def test_sessionstart_and_userpromptsubmit_execute_and_emit_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = sapper.Store(Path(tmp) / ".sapper")
            store.init("hooktest", "verify hook executes")
            for event, stdin in (("sessionstart", None),
                                 ("userpromptsubmit", '{"prompt": "hi"}')):
                proc = self._run_hook(event, tmp, stdin)
                self.assertEqual(proc.returncode, 0, msg=proc.stderr)
                payload = json.loads(proc.stdout)
                outer = payload["hookSpecificOutput"]
                self.assertEqual(outer["hookEventName"].lower(), event)
                ctx = outer.get("additionalContext") or outer.get("contextForClaude")
                self.assertIn("fact-graph", ctx)
