#!/usr/bin/env python3
"""
patcher — gated patch submit stub (Phase 3).

Red lines (proposal):
  - Never call submit_patch before FIRST_BLOOD.
  - Never submit when PATCH_QUOTA=used for this round.
  - Full sandbox-before-patch pipeline is Phase 4; this module only enforces
    the unlock gate and forwards to ArenaClient.submit_patch (stub/live).

Stdlib only. Does not invent Qiangwang patch artifact schemas.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from arena import ArenaClient, ArenaError  # noqa: E402
from unlock import UnlockError, UnlockGate  # noqa: E402


def submit_patch_gated(
    challenge_id: str,
    artifact_path: str | Path,
    *,
    runs_root: Path | None = None,
    client: ArenaClient | None = None,
    gate: UnlockGate | None = None,
    sandbox_ok: bool | None = None,
) -> dict:
    """Submit patch only if unlock gate allows.

    sandbox_ok: Phase 4 hook. If explicitly False, refuse even when unlocked.
    If None, skip sandbox check (Phase 3 stub — document that real verify is later).
    """
    client = client or ArenaClient.load(None)
    gate = gate or UnlockGate(runs_root)
    board = client.board()
    round_id = board.get("round_id")
    if round_id is not None:
        gate.sync_round(int(round_id))
    gate.require_patch(challenge_id)
    if sandbox_ok is False:
        raise UnlockError(
            "sandbox precheck failed — refuse patch submit "
            "(Phase 4: local service check + exp regression)",
            code="sandbox_failed",
        )
    artifact = Path(artifact_path)
    reply = client.submit_patch(challenge_id, str(artifact))
    ok = bool(reply.get("ok")) or reply.get("check") == "pass"
    if ok:
        gate.record_patch_success(challenge_id, round_id=int(round_id) if round_id is not None else None)
    return {
        "challenge": challenge_id,
        "round_id": round_id,
        "reply": reply,
        "gate": gate.flags_for(challenge_id),
        "sandbox_ok": sandbox_ok,
        "_note": "Phase 3 gated stub; full sandbox pipeline is Phase 4",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Gated AWD patch submit (stub)")
    p.add_argument("challenge_id")
    p.add_argument("artifact", help="path to patch artifact (format Unknown)")
    p.add_argument("--root", default=None)
    p.add_argument(
        "--sandbox-ok",
        choices=("skip", "yes", "no"),
        default="skip",
        help="Phase 4 hook: skip (default), yes, or no",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    sandbox = None if args.sandbox_ok == "skip" else (args.sandbox_ok == "yes")
    root = Path(args.root) if args.root else None
    try:
        result = submit_patch_gated(
            args.challenge_id,
            args.artifact,
            runs_root=root,
            sandbox_ok=sandbox,
        )
    except (UnlockError, ArenaError) as exc:
        print(f"patcher: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    return 0 if (result.get("reply") or {}).get("check") == "pass" or (result.get("reply") or {}).get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
