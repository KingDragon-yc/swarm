#!/usr/bin/env python3
"""
harvester — zero-token, round-aware batch exploit replay (Phase 3).

Contract (from AWD proposal):
  - Worker drops standardized payloads at
      <SAPPER_ROOT>/<challenge>/exploits/exp_*.py
  - Each exp takes argv: <ip> <port> and prints a flag to stdout
  - harvester polls peer targets each round, runs exps, submits flags,
    dedupes via harvest-ledger.json, tags facts with round_id

Works with PLATFORM_STUB=1 (no invented live API schemas). Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# Allow `python harvester.py` and `from harvester import …` when lib/ is on path.
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from arena import ArenaClient, ArenaError, read_round_state  # noqa: E402
from unlock import UnlockGate  # noqa: E402

LEDGER_FILE = "harvest-ledger.json"
FLAG_RE = re.compile(r"(flag\{[^\}]+\})", re.IGNORECASE)
ENDPOINT_RE = re.compile(
    r"^(?:stub://peer/[^/]+/[a-z]+|(?P<host>[^:/]+):(?P<port>\d+))$"
)


def _runs_root(runs_root: Path | None = None) -> Path:
    if runs_root is not None:
        return Path(runs_root)
    return Path(os.environ.get("SAPPER_ROOT") or os.environ.get("AWD_RUNS_ROOT") or ".")


def ledger_path(runs_root: Path | None = None) -> Path:
    return _runs_root(runs_root) / LEDGER_FILE


def parse_endpoint(target: dict) -> tuple[str, int]:
    """Resolve host/port from target dict (list_targets stub or live Unknown)."""
    if isinstance(target.get("host"), str) and target.get("port") is not None:
        return target["host"], int(target["port"])
    endpoint = str(target.get("endpoint") or "")
    m = ENDPOINT_RE.match(endpoint)
    if m and m.group("host"):
        return m.group("host"), int(m.group("port"))
    # stub://peer/<chall>/<suffix> → deterministic fake IP/port for offline replay
    if endpoint.startswith("stub://peer/"):
        parts = endpoint.split("/")
        # stub://peer/<chall>/<a|b>
        suffix = parts[-1] if parts else "a"
        chall = parts[-2] if len(parts) >= 2 else "x"
        # Stable pseudo host from challenge name
        host = f"10.0.{abs(hash(chall)) % 200 + 1}.{1 if suffix == 'a' else 2}"
        port = 80
        return host, port
    raise ArenaError(f"cannot parse target endpoint {endpoint!r}")


def discover_exploits(runs_root: Path, challenge_id: str | None = None) -> list[dict]:
    """Find exp_*.py under <root>/<chall>/exploits/."""
    root = _runs_root(runs_root)
    out: list[dict] = []
    pattern_dirs = [root / challenge_id / "exploits"] if challenge_id else sorted(root.glob("*/exploits"))
    for d in pattern_dirs:
        if not d.is_dir():
            continue
        chall = d.parent.name
        for path in sorted(d.glob("exp_*.py")):
            out.append({"challenge": chall, "path": path})
    return out


def extract_flag(stdout: str) -> str | None:
    m = FLAG_RE.search(stdout or "")
    return m.group(1) if m else None


def run_exploit(exp_path: Path, host: str, port: int, *, timeout: float = 15.0) -> dict:
    """Execute exp_*.py <ip> <port>; return {ok, flag, stdout, stderr, rc}."""
    try:
        proc = subprocess.run(
            [sys.executable, str(exp_path), str(host), str(port)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "flag": None,
            "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
            "stderr": "timeout",
            "rc": -1,
        }
    flag = extract_flag(proc.stdout or "")
    return {
        "ok": bool(flag) and proc.returncode == 0,
        "flag": flag,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "rc": proc.returncode,
    }


class HarvestLedger:
    """Dedupe submitted/recorded flags per (round, challenge, team, flag)."""

    def __init__(self, runs_root: Path | None = None):
        self.runs_root = _runs_root(runs_root)
        self.path = ledger_path(self.runs_root)
        self.entries: list[dict] = []
        self._lock = threading.Lock()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.entries = []
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self.entries = []
            return
        self.entries = list(raw.get("entries") or []) if isinstance(raw, dict) else []

    def save(self) -> Path:
        self.runs_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "entries": self.entries,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return self.path

    @staticmethod
    def _key(round_id: int, challenge: str, team: str, flag: str) -> str:
        return f"{round_id}|{challenge}|{team}|{flag}"

    def seen(self, round_id: int, challenge: str, team: str, flag: str) -> bool:
        key = self._key(round_id, challenge, team, flag)
        with self._lock:
            for e in self.entries:
                if self._key(
                    int(e.get("round_id") or 0),
                    str(e.get("challenge") or ""),
                    str(e.get("team") or ""),
                    str(e.get("flag") or ""),
                ) == key:
                    return True
        return False

    def append(self, entry: dict) -> dict:
        with self._lock:
            self.entries.append(entry)
            self.save()
        return entry


def write_flag_fact(
    runs_root: Path,
    *,
    challenge: str,
    flag: str,
    round_id: int,
    team: str | None,
    host: str,
    port: int,
    status: str,
    note: str = "",
) -> Path | None:
    """Append a round-tagged flag fact under <chall>/.sapper/facts/ when possible.

    Lightweight: creates a minimal fact JSON without requiring full sapper init.
    """
    facts_dir = runs_root / challenge / ".sapper" / "facts"
    try:
        facts_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    # Stable-ish id from content so re-runs don't explode file count for dupes.
    digest = abs(hash(f"{round_id}:{challenge}:{team}:{flag}")) % 10_000_000
    path = facts_dir / f"flag-r{round_id}-{digest}.json"
    if path.exists():
        return path
    fact = {
        "id": f"flag-r{round_id}-{digest}",
        "kind": "flag",
        "title": "flag",
        "description": f"flag captured: {flag} (challenge {challenge}, round {round_id})",
        "creator": "harvester",
        "data": {
            "value": flag,
            "challenge": challenge,
            "round_id": round_id,
            "team": team,
            "host": host,
            "port": port,
            "status": status,
            "note": note[:500],
        },
    }
    path.write_text(json.dumps(fact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def harvest_once(
    *,
    runs_root: Path | None = None,
    challenge_id: str | None = None,
    workers: int = 4,
    submit: bool = True,
    exp_timeout: float = 15.0,
    client: ArenaClient | None = None,
    gate: UnlockGate | None = None,
) -> dict[str, Any]:
    """One harvest sweep: board → targets → exps → submit → unlock."""
    root = _runs_root(runs_root)
    root.mkdir(parents=True, exist_ok=True)
    client = client or ArenaClient.load(None)
    gate = gate or UnlockGate(root)
    ledger = HarvestLedger(root)

    board = client.board()
    round_id = int(board.get("round_id") or 0)
    gate.sync_round(round_id)

    exploits = discover_exploits(root, challenge_id)
    summary: dict[str, Any] = {
        "round_id": round_id,
        "time_left_sec": board.get("time_left_sec"),
        "exploits": len(exploits),
        "attempts": 0,
        "flags_found": 0,
        "submitted": 0,
        "rejected": 0,
        "duplicates": 0,
        "errors": 0,
        "unlocks": [],
        "results": [],
    }
    if not exploits:
        summary["note"] = "no exp_*.py under <chall>/exploits/"
        return summary

    # Build work items: (exp, target)
    work: list[tuple[dict, dict]] = []
    for exp in exploits:
        chall = exp["challenge"]
        if challenge_id and chall != challenge_id:
            continue
        try:
            targets = client.list_targets(chall)
        except ArenaError as exc:
            summary["errors"] += 1
            summary["results"].append({"challenge": chall, "error": str(exc)})
            continue
        for t in targets:
            work.append((exp, t))

    def _one(exp: dict, target: dict) -> dict:
        chall = exp["challenge"]
        team = str(target.get("team") or "")
        try:
            host, port = parse_endpoint(target)
        except ArenaError as exc:
            return {"challenge": chall, "team": team, "status": "error", "error": str(exc)}
        run = run_exploit(Path(exp["path"]), host, port, timeout=exp_timeout)
        row = {
            "challenge": chall,
            "team": team,
            "host": host,
            "port": port,
            "exp": Path(exp["path"]).name,
            "flag": run.get("flag"),
            "status": "miss",
        }
        if not run.get("flag"):
            row["status"] = "miss"
            row["stderr"] = (run.get("stderr") or "")[:200]
            return row
        flag = run["flag"]
        if ledger.seen(round_id, chall, team, flag):
            row["status"] = "duplicate"
            return row
        if not submit:
            ledger.append({
                "round_id": round_id,
                "challenge": chall,
                "team": team,
                "host": host,
                "port": port,
                "flag": flag,
                "status": "recorded",
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            write_flag_fact(
                root, challenge=chall, flag=flag, round_id=round_id,
                team=team, host=host, port=port, status="recorded",
            )
            row["status"] = "recorded"
            return row
        try:
            reply = client.submit_flag(chall, flag, team=team or None)
        except ArenaError as exc:
            ledger.append({
                "round_id": round_id, "challenge": chall, "team": team,
                "host": host, "port": port, "flag": flag,
                "status": "error", "error": str(exc),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            row["status"] = "error"
            row["error"] = str(exc)
            return row
        ok = bool(reply.get("correct"))
        status = "submitted" if ok else "rejected"
        ledger.append({
            "round_id": round_id, "challenge": chall, "team": team,
            "host": host, "port": port, "flag": flag, "status": status,
            "reply": {k: reply.get(k) for k in ("correct", "status", "stub")},
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        write_flag_fact(
            root, challenge=chall, flag=flag, round_id=round_id,
            team=team, host=host, port=port, status=status,
            note=json.dumps(reply.get("result") or {}, ensure_ascii=False)[:300],
        )
        row["status"] = status
        if ok:
            unlocked = gate.mark_first_blood(chall, round_id=round_id)
            row["FIRST_BLOOD"] = "yes"
            row["unlock"] = unlocked
        return row

    workers = max(1, int(workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, exp, tgt) for exp, tgt in work]
        for fut in as_completed(futs):
            summary["attempts"] += 1
            try:
                row = fut.result()
            except Exception as exc:  # noqa: BLE001 — keep sweep alive
                summary["errors"] += 1
                summary["results"].append({"status": "error", "error": str(exc)})
                continue
            summary["results"].append(row)
            st = row.get("status")
            if row.get("flag"):
                summary["flags_found"] += 1
            if st == "submitted":
                summary["submitted"] += 1
                if row.get("FIRST_BLOOD") == "yes":
                    summary["unlocks"].append(row.get("challenge"))
            elif st == "rejected":
                summary["rejected"] += 1
            elif st == "duplicate":
                summary["duplicates"] += 1
            elif st == "error":
                summary["errors"] += 1

    # Deduplicate unlock list
    summary["unlocks"] = sorted(set(x for x in summary["unlocks"] if x))
    summary["gate"] = gate.snapshot()
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="AI-AWD zero-token harvester (PLATFORM_STUB-friendly)"
    )
    p.add_argument("--root", default=None, help="SAPPER_ROOT (default env or .)")
    p.add_argument("--challenge", "-c", default=None, help="limit to one challenge id")
    p.add_argument("--workers", "-j", type=int, default=4)
    p.add_argument("--timeout", type=float, default=15.0, help="per-exp timeout seconds")
    p.add_argument("--no-submit", action="store_true", help="record flags only")
    p.add_argument("--once", action="store_true", default=True,
                   help="single sweep (default; loop mode is Phase 4+)")
    p.add_argument("--json", action="store_true", help="print summary JSON only")
    args = p.parse_args(argv)

    root = Path(args.root) if args.root else None
    try:
        summary = harvest_once(
            runs_root=root,
            challenge_id=args.challenge,
            workers=args.workers,
            submit=not args.no_submit,
            exp_timeout=args.timeout,
        )
    except ArenaError as exc:
        print(f"harvester: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"[harvester] round={summary['round_id']} "
            f"attempts={summary['attempts']} found={summary['flags_found']} "
            f"submitted={summary['submitted']} rejected={summary['rejected']} "
            f"dupes={summary['duplicates']} unlocks={summary['unlocks']}"
        )
        rs = read_round_state(root)
        if rs:
            print(f"[harvester] round-state time_left_sec={rs.get('time_left_sec')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
