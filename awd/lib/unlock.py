#!/usr/bin/env python3
"""
unlock — AI-AWD first-blood / patch-quota gate (Phase 3).

Rules (from contest + adaptation plan):
  - Patch unlocks only after ≥1 accepted peer flag on that challenge (FIRST_BLOOD).
  - One successful patch per challenge per round (PATCH_QUOTA).
  - Do not call submit_patch until FIRST_BLOOD and quota available.
  - FIRST_BLOOD persists across rounds; PATCH_QUOTA resets each round.

Persists under SAPPER_ROOT/unlock-state.json. Stdlib only.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

UNLOCK_FILE = "unlock-state.json"


class UnlockError(Exception):
    """Gate refusal (not unlocked / quota spent)."""

    def __init__(self, message: str, *, code: str | None = None):
        self.code = code
        super().__init__(message)


def _runs_root(runs_root: Path | None = None) -> Path:
    if runs_root is not None:
        return Path(runs_root)
    return Path(os.environ.get("SAPPER_ROOT") or os.environ.get("AWD_RUNS_ROOT") or ".")


def unlock_path(runs_root: Path | None = None) -> Path:
    return _runs_root(runs_root) / UNLOCK_FILE


def _empty_challenge() -> dict[str, Any]:
    return {
        "FIRST_BLOOD": False,
        "PATCH_QUOTA": "available",  # available | used
        "SLA_STATUS": "unknown",     # unknown | ok | abnormal
        "first_blood_at": None,
        "last_patch_round": None,
    }


class UnlockGate:
    """Per-challenge unlock + patch quota state machine."""

    def __init__(self, runs_root: Path | None = None):
        self.runs_root = _runs_root(runs_root)
        self.path = unlock_path(self.runs_root)
        self._data: dict[str, Any] = {"round_id": None, "challenges": {}}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._data = {"round_id": None, "challenges": {}}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._data = {"round_id": None, "challenges": {}}
            return
        if not isinstance(raw, dict):
            self._data = {"round_id": None, "challenges": {}}
            return
        ch = raw.get("challenges") or {}
        if not isinstance(ch, dict):
            ch = {}
        self._data = {"round_id": raw.get("round_id"), "challenges": ch}

    def save(self) -> Path:
        self.runs_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "round_id": self._data.get("round_id"),
            "challenges": self._data.get("challenges") or {},
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return self.path

    def _chal(self, challenge_id: str) -> dict[str, Any]:
        challenges = self._data.setdefault("challenges", {})
        if challenge_id not in challenges or not isinstance(challenges[challenge_id], dict):
            challenges[challenge_id] = _empty_challenge()
        # Normalize keys for older files.
        row = challenges[challenge_id]
        base = _empty_challenge()
        base.update(row)
        challenges[challenge_id] = base
        return challenges[challenge_id]

    def sync_round(self, round_id: int | None) -> None:
        """On round boundary: keep FIRST_BLOOD, reset PATCH_QUOTA to available."""
        if round_id is None:
            return
        prev = self._data.get("round_id")
        if prev == round_id:
            return
        for cid, row in list((self._data.get("challenges") or {}).items()):
            if not isinstance(row, dict):
                continue
            row = dict(row)
            row["PATCH_QUOTA"] = "available"
            self._data["challenges"][cid] = row
        self._data["round_id"] = round_id
        self.save()

    def mark_first_blood(self, challenge_id: str, *, round_id: int | None = None) -> dict:
        """Record accepted peer flag → unlock patch for challenge."""
        if round_id is not None:
            self.sync_round(round_id)
        row = self._chal(challenge_id)
        if not row["FIRST_BLOOD"]:
            row["FIRST_BLOOD"] = True
            row["first_blood_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.save()
        return dict(row)

    def can_submit_patch(self, challenge_id: str) -> tuple[bool, str]:
        row = self._chal(challenge_id)
        if not row.get("FIRST_BLOOD"):
            return False, "FIRST_BLOOD=no — submit a peer flag before patching"
        if row.get("PATCH_QUOTA") == "used":
            return False, "PATCH_QUOTA=used — one successful patch per challenge per round"
        return True, "ok"

    def require_patch(self, challenge_id: str) -> None:
        ok, reason = self.can_submit_patch(challenge_id)
        if not ok:
            raise UnlockError(reason, code="patch_gated")

    def record_patch_success(self, challenge_id: str, *, round_id: int | None = None) -> dict:
        self.require_patch(challenge_id)
        if round_id is not None:
            self.sync_round(round_id)
        row = self._chal(challenge_id)
        row["PATCH_QUOTA"] = "used"
        row["last_patch_round"] = round_id if round_id is not None else self._data.get("round_id")
        self.save()
        return dict(row)

    def set_sla(self, challenge_id: str, status: str) -> dict:
        if status not in ("unknown", "ok", "abnormal"):
            raise UnlockError(f"invalid SLA_STATUS {status!r}", code="bad_sla")
        row = self._chal(challenge_id)
        row["SLA_STATUS"] = status
        self.save()
        return dict(row)

    def flags_for(self, challenge_id: str) -> dict[str, Any]:
        row = self._chal(challenge_id)
        return {
            "FIRST_BLOOD": "yes" if row.get("FIRST_BLOOD") else "no",
            "PATCH_QUOTA": row.get("PATCH_QUOTA") or "available",
            "SLA_STATUS": row.get("SLA_STATUS") or "unknown",
            "patch_unlocked": bool(row.get("FIRST_BLOOD")),
            "patch_slots_left": 0 if row.get("PATCH_QUOTA") == "used" else 1,
        }

    def enrich_challenges(self, challenges: list[dict], *, round_id: int | None = None) -> list[dict]:
        if round_id is not None:
            self.sync_round(round_id)
        out = []
        for c in challenges:
            if not isinstance(c, dict):
                continue
            row = dict(c)
            cid = row.get("unique_code") or row.get("id")
            if isinstance(cid, str) and cid:
                flags = self.flags_for(cid)
                row.update(flags)
                # Keep boolean patch_unlocked consistent with gate (not stub default).
                row["patch_unlocked"] = flags["patch_unlocked"]
                row["patch_slots_left"] = flags["patch_slots_left"]
            out.append(row)
        return out

    def snapshot(self) -> dict:
        return {
            "round_id": self._data.get("round_id"),
            "challenges": {
                cid: self.flags_for(cid)
                for cid in (self._data.get("challenges") or {})
            },
        }
