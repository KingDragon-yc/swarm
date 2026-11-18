# Qiangwang AI-AWD — environment checklist (Phase 1–2)

Profile: **`PLATFORM=qiangwang`**. Adapted from sapper-v2’s TSecBench hosted-mode list.
Official platform auth/header/path names are **Unknown** until contest docs arrive — use stubs.

## Platform profile

| KEY | Default / value | Notes |
|---|---|---|
| `PLATFORM` | `qiangwang` | Adaptation profile. Do not invent live paths for other values here. |
| `PLATFORM_STUB` | `1` for offline | Deterministic fake board + accept/reject toggles. **No network.** |
| `SAPPER_ROOT` | `/work/runs` (container) | Runs root; `round-state.json` is written here on each `board()`. |
| `SAPPER_ROUND_STATE` | optional | Override path for round-state.json |
| `SAPPER_ARENA_LIB` | `/opt/sapper/lib/arena.py` | Override for host-side worker tests |

## Qiangwang platform vars (names Unknown)

| KEY | Status | Notes |
|---|---|---|
| `QW_BASE_URL` | **Unknown** | Official API base. Alias: `BENCHMARK_BASE_URL` (smoke/compat only). |
| `QW_TOKEN` | **Unknown** | Auth secret. Alias: `BENCHMARK_TOKEN`. |
| `QW_AUTH_HEADER` | **Unknown** | Header name if not Bearer/token-as-is. Not wired yet. |
| `QW_ROUND_SECONDS` | `600` | Stub round length (rules: 10-minute rounds). Alias: `SAPPER_ROUND_SECONDS`. |
| `QW_STUB_NOW` / `QW_STUB_ANCHOR` | test-only | Pin stub clock for deterministic round_id in tests. |

Live calls without `PLATFORM_STUB=1` raise `ArenaError(code=not_wired)` — we deliberately do **not** invent endpoints.

## Model gateway (local smoke still possible)

Unchanged from sapper: configure a real gateway for commander/worker smoke even when the board is stubbed.

| KEY | Example | Notes |
|---|---|---|
| `ANTHROPIC_BASE_URL` | vendor Anthropic-compatible URL | Required for full container entrypoint |
| `ANTHROPIC_AUTH_TOKEN` | API key | Required for full container entrypoint |
| `SAPPER_MODEL` | e.g. `glm-5.3-flash[1m]` | Commander / smart tier |
| `SAPPER_MODEL_FLASH` | flash tier id | Infantry |
| `SAPPER_KEY_POOL` | JSON array | Optional key rotation |
| `SAPPER_TOTAL_MINUTES` | contest length | Watchdog deadline (Unknown exact contest duration) |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` | optional | `--gw ds` workers |

With **`PLATFORM_STUB=1`**, platform reachability checks are skipped so you can smoke the model CLI alone.

## Capability stubs (not real wire formats)

| Capability | Stub behavior | Live |
|---|---|---|
| `board` | round_id, time_left_sec, challenges | **Unknown** |
| `list_targets` | fake `stub://peer/...` peers | **Unknown** discovery |
| `submit_flag` | accept `flag{...}` shape | **Unknown** schema |
| `submit_patch` | pass if artifact path exists | **Unknown** package format |
| `reset` | ok + abnormality=true | **Unknown** endpoint |

## Offline stub board smoke

```bash
cd awd
export PLATFORM=qiangwang PLATFORM_STUB=1
export SAPPER_ROOT=/tmp/awd-runs
mkdir -p "$SAPPER_ROOT"
export PYTHONPATH=lib
python3 -c "from arena import ArenaClient; print(ArenaClient.load(None).board())"
# or:
bash scripts/stub-board-smoke.sh
```

## Phase 3 attack / harvester

```bash
bash scripts/attack-harvester-smoke.sh
# or:
python3 lib/harvester.py --root "$SAPPER_ROOT" -c web-1 --json
python3 lib/patcher.py <challenge> <artifact> --root "$SAPPER_ROOT"
```

State files under `SAPPER_ROOT`: `round-state.json`, `unlock-state.json`, `harvest-ledger.json`.

See `awd/README.md` for layout notes.
