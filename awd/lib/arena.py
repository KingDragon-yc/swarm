#!/usr/bin/env python3
"""
arena — Qiangwang AI-AWD platform adapter for sapper (Phase 1–2).

Profile: PLATFORM=qiangwang (default in this tree). Keeps the ArenaError /
load-from-env shape that sapper.py and docker/commander/worker.sh expect.

Real Qiangwang API paths, headers, and payloads are UNKNOWN. This module
exposes the capability surface (board / submit_flag / submit_patch / reset /
list_targets) but does **not** invent wire formats. Live HTTP raises
ArenaError("not wired") until official docs land.

Stub mode: PLATFORM_STUB=1 returns a deterministic fake board so commander /
worker loops can run offline. Every successful board() also writes
round-state.json for the commander path.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

ARENA_FILE = "arena.json"
ROUND_STATE_FILE = "round-state.json"
DEFAULT_TIMEOUT = 30
DEFAULT_ROUND_SECONDS = 600  # 10-minute rounds per contest rules

# Deterministic stub challenge set (offline only).
_STUB_CHALLENGES = (
    {
        "id": "web-1",
        "unique_code": "web-1",
        "name": "stub-web-1",
        "patch_unlocked": False,
        "patch_slots_left": 1,
        "service": "unknown",
        "pwned": False,
        "bucket": 2000,
    },
    {
        "id": "pwn-1",
        "unique_code": "pwn-1",
        "name": "stub-pwn-1",
        "patch_unlocked": False,
        "patch_slots_left": 1,
        "service": "unknown",
        "pwned": False,
        "bucket": 2000,
    },
    {
        "id": "misc-1",
        "unique_code": "misc-1",
        "name": "stub-misc-1",
        "patch_unlocked": True,
        "patch_slots_left": 1,
        "service": "unknown",
        "pwned": False,
        "bucket": 1700,
    },
)


class ArenaError(Exception):
    """User-facing arena failure (bad config, stub toggle, unwired live call)."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        detail: dict | None = None,
    ):
        self.code = code
        self.status = status
        self.detail = detail or {}
        super().__init__(message)


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in ("1", "true", "yes", "on")


def platform_name() -> str:
    return (os.environ.get("PLATFORM") or "qiangwang").strip().lower()


def stub_enabled() -> bool:
    return _truthy(os.environ.get("PLATFORM_STUB"))


def round_state_path(runs_root: Path | None = None) -> Path:
    """Where commander/worker expect machine-readable round clock + board."""
    if runs_root is not None:
        return Path(runs_root) / ROUND_STATE_FILE
    override = os.environ.get("SAPPER_ROUND_STATE")
    if override:
        return Path(override)
    root = os.environ.get("SAPPER_ROOT") or os.environ.get("AWD_RUNS_ROOT") or "."
    return Path(root) / ROUND_STATE_FILE


def write_round_state(board: dict, runs_root: Path | None = None) -> Path:
    path = round_state_path(runs_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": board.get("platform") or platform_name(),
        "stub": bool(board.get("stub")),
        "round_id": board.get("round_id"),
        "round_seconds": board.get("round_seconds"),
        "time_left_sec": board.get("time_left_sec"),
        "challenges": board.get("challenges") or [],
        "unlock": board.get("unlock"),
        "score": board.get("score"),
        "raw_board": board,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_round_state(runs_root: Path | None = None) -> dict | None:
    path = round_state_path(runs_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


class ArenaClient:
    """Qiangwang-oriented platform client (stdlib only).

    Construction mirrors the former TSecBench client so sapper._arena_client
    and worker.sh `arena()` keep working. In stub mode, base_url/token may be
    placeholders; live mode still requires env/config but refuses invented
    endpoints.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int = DEFAULT_TIMEOUT,
        *,
        stub: bool | None = None,
        runs_root: Path | None = None,
    ):
        self.stub = stub_enabled() if stub is None else bool(stub)
        if not self.stub:
            if not base_url or not base_url.strip():
                raise ArenaError(
                    "arena base_url is empty — set QW_BASE_URL (Unknown) or "
                    "BENCHMARK_BASE_URL alias, or PLATFORM_STUB=1"
                )
            if not token or not token.strip():
                raise ArenaError(
                    "arena token is empty — set QW_TOKEN (Unknown) or "
                    "BENCHMARK_TOKEN alias, or PLATFORM_STUB=1"
                )
        self.base_url = (base_url or "stub://qiangwang").rstrip("/")
        self.token = (token or "stub-token").strip()
        self.timeout = timeout
        self.runs_root = Path(runs_root) if runs_root is not None else None
        self.platform = platform_name()

    # -- construction ------------------------------------------------------
    @classmethod
    def config_path(cls, store_root: Path | None = None) -> Path:
        if store_root is not None:
            return Path(store_root) / ARENA_FILE
        return Path(ARENA_FILE)

    @classmethod
    def _resolve_runs_root(cls, store_root: Path | None) -> Path | None:
        env = os.environ.get("SAPPER_ROOT") or os.environ.get("AWD_RUNS_ROOT")
        if env:
            return Path(env)
        if store_root is not None:
            # .sapper store lives under runs/<challenge>/.sapper — climb if obvious
            p = Path(store_root)
            if p.name == ".sapper":
                return p.parent.parent
        return None

    @classmethod
    def load(cls, store_root: Path | None = None) -> "ArenaClient":
        """Resolve config: PLATFORM_STUB, then QW_* / BENCHMARK_* env, then arena.json."""
        stub = stub_enabled()
        # Prefer Qiangwang names; keep BENCHMARK_* as smoke/compat aliases.
        base_url = (
            os.environ.get("QW_BASE_URL")
            or os.environ.get("BENCHMARK_BASE_URL")
            or ""
        )
        token = (
            os.environ.get("QW_TOKEN")
            or os.environ.get("BENCHMARK_TOKEN")
            or ""
        )
        timeout = DEFAULT_TIMEOUT
        cfg: dict = {}
        if store_root is not None:
            path = cls.config_path(store_root)
            if path.exists():
                try:
                    cfg = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as exc:
                    raise ArenaError(f"cannot read arena config {path}: {exc}") from exc
                if not isinstance(cfg, dict):
                    raise ArenaError(f"arena config {path} must be a JSON object")
        if isinstance(cfg.get("base_url"), str) and cfg["base_url"].strip():
            base_url = cfg["base_url"].strip()
        if isinstance(cfg.get("token"), str) and cfg["token"].strip():
            raw = cfg["token"].strip()
            if raw.startswith("ENV:"):
                token = os.environ.get(raw[4:].strip(), "") or token
            else:
                token = raw
        if isinstance(cfg.get("timeout"), int) and cfg["timeout"] > 0:
            timeout = cfg["timeout"]
        if isinstance(cfg.get("stub"), bool):
            stub = cfg["stub"]
        runs_root = cls._resolve_runs_root(store_root)
        if stub:
            return cls(
                base_url or "stub://qiangwang",
                token or "stub-token",
                timeout,
                stub=True,
                runs_root=runs_root,
            )
        if not base_url:
            raise ArenaError(
                "no arena base_url — set QW_BASE_URL (Unknown official name) "
                "or BENCHMARK_BASE_URL alias, write .sapper/arena.json, or set PLATFORM_STUB=1"
            )
        return cls(base_url, token, timeout, stub=False, runs_root=runs_root)

    @classmethod
    def write_template(cls, store_root: Path) -> Path:
        path = cls.config_path(store_root)
        if path.exists():
            raise ArenaError(f"arena config already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "base_url": "https://qiangwang.example.invalid",
                    "token": "ENV:QW_TOKEN",
                    "timeout": 30,
                    "stub": False,
                    "_comment": (
                        "Qiangwang AI-AWD adapter. Official base URL / auth header "
                        "names are Unknown. Prefer env QW_BASE_URL + QW_TOKEN "
                        "(BENCHMARK_* aliases still accepted). Use PLATFORM_STUB=1 "
                        "for offline deterministic board."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    # -- stub helpers ------------------------------------------------------
    def _stub_board(self) -> dict[str, Any]:
        """Deterministic fake board: round clock + challenges.

        Round id advances every DEFAULT_ROUND_SECONDS of wall clock so offline
        loops can observe round boundaries without a real API.
        """
        round_seconds = int(
            os.environ.get("QW_ROUND_SECONDS")
            or os.environ.get("SAPPER_ROUND_SECONDS")
            or DEFAULT_ROUND_SECONDS
        )
        # Fixed epoch so tests can pin with QW_STUB_NOW
        now = float(os.environ.get("QW_STUB_NOW") or time.time())
        # Anchor so round_id starts at 1 near typical smoke time; pure math.
        anchor = float(os.environ.get("QW_STUB_ANCHOR") or 1_700_000_000)
        elapsed = max(0.0, now - anchor)
        round_id = int(elapsed // round_seconds) + 1
        into = elapsed % round_seconds
        time_left = int(round_seconds - into)
        challenges = [dict(c) for c in _STUB_CHALLENGES]
        return {
            "platform": "qiangwang",
            "stub": True,
            "round_id": round_id,
            "round_seconds": round_seconds,
            "time_left_sec": time_left,
            "challenges": challenges,
            "score": {
                "rank": None,
                "total": None,
                "_note": "Unknown — scoring visibility not documented yet",
            },
            "_note": "PLATFORM_STUB=1 deterministic board; not a real Qiangwang payload",
        }

    def _not_wired(self, capability: str) -> None:
        raise ArenaError(
            f"qiangwang live {capability} is not wired — official API paths/"
            f"payloads are Unknown. Use PLATFORM_STUB=1 for offline stubs.",
            code="not_wired",
            status=None,
        )

    def _enrich_with_unlock(self, data: dict) -> dict:
        """Merge FIRST_BLOOD / PATCH_QUOTA / SLA_STATUS from unlock-state.json."""
        try:
            from unlock import UnlockGate
        except ImportError:  # pragma: no cover
            return data
        gate = UnlockGate(self.runs_root)
        round_id = data.get("round_id")
        if round_id is not None:
            gate.sync_round(int(round_id))
        challenges = data.get("challenges") or []
        if isinstance(challenges, list):
            data["challenges"] = gate.enrich_challenges(
                challenges, round_id=int(round_id) if round_id is not None else None
            )
        data["unlock"] = gate.snapshot()
        return data

    # -- capabilities (Phase 1–3 surface) ----------------------------------
    def board(self) -> dict:
        """Round id, time left, challenges (+ unlock/service fields when known)."""
        if self.stub:
            data = self._stub_board()
        else:
            self._not_wired("board")
            data = {}  # pragma: no cover
        data = self._enrich_with_unlock(data)
        write_round_state(data, self.runs_root)
        return data

    def list_challenges(self) -> list[dict]:
        """Compat for worker.sh `arena list` / legacy sapper arena challenges."""
        board = self.board()
        challenges = board.get("challenges") or []
        out: list[dict] = []
        for item in challenges:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            # worker board historically keys on unique_code
            if "unique_code" not in row and isinstance(row.get("id"), str):
                row["unique_code"] = row["id"]
            # map stub fields into soft TSecBench-ish counters (unknown live)
            row.setdefault("is_completed", False)
            row.setdefault("flag_count", 0)
            row.setdefault("correct_flag_count", 0)
            row["round_id"] = board.get("round_id")
            row["time_left_sec"] = board.get("time_left_sec")
            out.append(row)
        return out

    def list_targets(self, challenge_id: str | None = None) -> list[dict]:
        """Peer endpoints per challenge×team — discovery method Unknown.

        Stub targets include host/port so exp_*.py <ip> <port> can run offline.
        """
        if self.stub:
            # Avoid recursive board write storms: build from stub board once.
            board = self._stub_board()
            challenges = board.get("challenges") or []
            if challenge_id:
                challenges = [
                    c for c in challenges
                    if isinstance(c, dict)
                    and (c.get("unique_code") == challenge_id or c.get("id") == challenge_id)
                ]
            targets = []
            for idx, c in enumerate(challenges):
                if not isinstance(c, dict):
                    continue
                code = c.get("unique_code") or c.get("id")
                # Deterministic fake peers — not real contest addresses.
                base = 10 + (idx % 200)
                for n, team, octet in (
                    (1, "stub-team-a", 1),
                    (2, "stub-team-b", 2),
                ):
                    host = f"10.0.{base}.{octet}"
                    port = 80
                    targets.append({
                        "challenge": code,
                        "team": team,
                        "host": host,
                        "port": port,
                        "endpoint": f"{host}:{port}",
                        "_note": "stub peer; real discovery Unknown",
                    })
            return targets
        self._not_wired("list_targets")
        return []  # pragma: no cover

    def submit_flag(
        self,
        challenge_id: str,
        flag: str,
        *,
        team: str | None = None,
    ) -> dict:
        if not isinstance(flag, str) or not flag.strip():
            raise ArenaError("cannot submit an empty flag")
        if self.stub:
            # Accept flags that look like flag{...}; reject others — deterministic.
            ok = flag.strip().startswith("flag{") and flag.strip().endswith("}")
            return {
                "stub": True,
                "correct": ok,
                "status": 200 if ok else 400,
                "challenge": challenge_id,
                "team": team,
                "result": {"accepted": ok, "unlock_patch": ok},
                "_note": "stub accept/reject; real schema Unknown",
            }
        self._not_wired("submit_flag")
        return {}  # pragma: no cover

    def submit_patch(self, challenge_id: str, artifact_path: str) -> dict:
        if self.stub:
            path = Path(artifact_path)
            exists = path.exists()
            return {
                "stub": True,
                "ok": exists,
                "challenge": challenge_id,
                "artifact": str(path),
                "check": "pass" if exists else "fail",
                "_note": "stub patch check; artifact format Unknown",
            }
        self._not_wired("submit_patch")
        return {}  # pragma: no cover

    def reset(self, challenge_id: str) -> dict:
        """Reset own challenge — rules: counts as abnormality that round."""
        if self.stub:
            return {
                "stub": True,
                "ok": True,
                "challenge": challenge_id,
                "abnormality": True,
                "_note": "stub reset; real endpoint Unknown",
            }
        self._not_wired("reset")
        return {}  # pragma: no cover

    # -- TSecBench-shaped leftovers (compat; stubbed / unwired) ------------
    def start_challenge(self, unique_code: str) -> dict:
        """No AWD analogue (agent cannot start/own challenge VMs)."""
        if self.stub:
            return {
                "stub": True,
                "unique_code": unique_code,
                "container_addr": [],
                "refused": True,
                "_note": "qiangwang: no own-box start; peers via list_targets",
            }
        self._not_wired("start_challenge")
        return {}  # pragma: no cover

    def close_challenge(self, unique_code: str) -> dict:
        if self.stub:
            return {
                "stub": True,
                "unique_code": unique_code,
                "closed": False,
                "_note": "qiangwang: no close/start instance model",
            }
        self._not_wired("close_challenge")
        return {}  # pragma: no cover

    def get_hint(self, unique_code: str) -> dict:
        if self.stub:
            return {
                "stub": True,
                "unique_code": unique_code,
                "hint": None,
                "_note": "hint API Unknown for qiangwang",
            }
        self._not_wired("get_hint")
        return {}  # pragma: no cover
