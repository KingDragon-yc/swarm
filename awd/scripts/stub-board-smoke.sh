#!/usr/bin/env bash
# Offline Phase 1–2 smoke: PLATFORM_STUB board → round-state.json → worker board.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWD="$(cd "$HERE/.." && pwd)"
ROOT="${SAPPER_ROOT:-$(mktemp -d /tmp/awd-runs.XXXXXX)}"
mkdir -p "$ROOT"

export PLATFORM="${PLATFORM:-qiangwang}"
export PLATFORM_STUB=1
export SAPPER_ROOT="$ROOT"
export SAPPER_ARENA_LIB="$AWD/lib/arena.py"
export PYTHONPATH="$AWD/lib${PYTHONPATH:+:$PYTHONPATH}"
# Pin clock so round_id is stable in CI/smoke output.
export QW_STUB_ANCHOR=1700000000
export QW_STUB_NOW=1700000300   # 300s into round → round_id=1, ~300s left
export QW_ROUND_SECONDS=600

echo "== stub board via ArenaClient =="
python3 - <<'PY'
from arena import ArenaClient, read_round_state
c = ArenaClient.load(None)
board = c.board()
assert board["stub"] is True
assert board["round_id"] == 1
assert board["time_left_sec"] == 300
assert len(board["challenges"]) >= 1
rs = read_round_state()
assert rs and rs["round_id"] == 1
print("round_id=%s time_left_sec=%s challenges=%d" % (
    board["round_id"], board["time_left_sec"], len(board["challenges"])))
print("round-state:", rs and "ok" or "MISSING")
PY

echo "== sapper arena board =="
python3 "$AWD/lib/sapper.py" arena board | head -n 40

echo "== worker.sh board (text) =="
# collect_panes needs tmux; board still works if panes empty / tmux missing.
bash "$AWD/docker/commander/worker.sh" board --json 2>/dev/null \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("round_id")==1; print("worker json round_id=%s codes=%d" % (d["round_id"], len(d.get("codes") or [])))'

echo "== submit_flag stub =="
python3 - <<'PY'
from arena import ArenaClient
c = ArenaClient.load(None)
ok = c.submit_flag("web-1", "flag{stub}")
bad = c.submit_flag("web-1", "nope")
assert ok["correct"] and not bad["correct"]
print("submit accept/reject ok")
PY

echo "OK stub-board-smoke ROOT=$ROOT"
echo "round-state file: $ROOT/round-state.json"
