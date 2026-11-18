#!/usr/bin/env bash
# Phase 3 smoke: stub targets → exp_*.py → harvester submit → unlock → gated patch.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWD="$(cd "$HERE/.." && pwd)"
ROOT="${SAPPER_ROOT:-$(mktemp -d /tmp/awd-attack.XXXXXX)}"
mkdir -p "$ROOT/web-1/exploits"

export PLATFORM="${PLATFORM:-qiangwang}"
export PLATFORM_STUB=1
export SAPPER_ROOT="$ROOT"
export SAPPER_ARENA_LIB="$AWD/lib/arena.py"
export PYTHONPATH="$AWD/lib${PYTHONPATH:+:$PYTHONPATH}"
export QW_STUB_ANCHOR=1700000000
export QW_STUB_NOW=1700000300
export QW_ROUND_SECONDS=600

# Challenge-local exploit matching the Phase 3 contract.
cat > "$ROOT/web-1/exploits/exp_v1.py" <<'PY'
#!/usr/bin/env python3
import sys
ip, port = sys.argv[1], sys.argv[2]
print(f"flag{{stub-{ip}-{port}}}")
PY

echo "== targets (stub) =="
python3 - <<'PY'
from arena import ArenaClient
ts = ArenaClient.load(None).list_targets("web-1")
assert ts and "host" in ts[0] and "port" in ts[0]
print("targets=%d first=%s:%s" % (len(ts), ts[0]["host"], ts[0]["port"]))
PY

echo "== harvester once =="
python3 "$AWD/lib/harvester.py" --root "$ROOT" -c web-1 --json > /tmp/harvest-summary.json
python3 - <<'PY'
import json
s = json.load(open("/tmp/harvest-summary.json"))
assert s["round_id"] == 1
assert s["submitted"] >= 1
assert "web-1" in s["unlocks"]
print("submitted=%d unlocks=%s" % (s["submitted"], s["unlocks"]))
PY

echo "== unlock gate =="
python3 - <<'PY'
from unlock import UnlockGate
g = UnlockGate()
f = g.flags_for("web-1")
assert f["FIRST_BLOOD"] == "yes"
print(f)
PY

echo "== patcher before sandbox (gated stub) =="
echo stub-patch > "$ROOT/web-1/patch.bin"
python3 "$AWD/lib/patcher.py" web-1 "$ROOT/web-1/patch.bin" --root "$ROOT" --json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["gate"]["PATCH_QUOTA"]=="used"; print("patch ok, quota used")'

echo "== patcher refuses second submit same round =="
if python3 "$AWD/lib/patcher.py" web-1 "$ROOT/web-1/patch.bin" --root "$ROOT" 2>/tmp/patch-deny.txt; then
  echo "expected gate refusal" >&2
  exit 1
fi
grep -q PATCH_QUOTA /tmp/patch-deny.txt

echo "== second harvest is deduped =="
python3 "$AWD/lib/harvester.py" --root "$ROOT" -c web-1 --json \
  | python3 -c 'import json,sys; s=json.load(sys.stdin); assert s["submitted"]==0 and s["duplicates"]>=1; print("dupes=%d" % s["duplicates"])'

echo "OK attack-harvester-smoke ROOT=$ROOT"
echo "ledger: $ROOT/harvest-ledger.json"
echo "unlock: $ROOT/unlock-state.json"
