# AWD — Qiangwang AI-AWD sapper skeleton (in swarm)

Phase **1–2** adaptation of [sapper-v2](https://github.com/N1nEmAn/sapper-v2) `@7b75e6f` for the
10th Qiangwang Cup **AI-AWD** track. Lives under **`awd/`** so the existing IMF tree at the
swarm repo root is untouched.

Plan: project store `docs/awd-adaptation-plan.md`.

## Layout choice

| Path | Role |
|---|---|
| `/` (repo root) | Existing **IMF** Instant Message Force harness — left intact |
| `awd/` | Vendored/adapted sapper-v2 skeleton for Qiangwang AI-AWD |

Do **not** PR changes upstream to `N1nEmAn/sapper-v2`.

## Phase 1–2 contents

1. **`PLATFORM=qiangwang`** profile (`lib/arena.py` replaces TSecBench client shape).
2. Stub capabilities: `board`, `list_targets`, `submit_flag`, `submit_patch`, `reset`.
3. **`PLATFORM_STUB=1`** — deterministic fake board (no invented live API paths).
4. **`round-state.json`** written under `SAPPER_ROOT` on every `board()`.
5. Commander/worker `board` surfaces **round id / time left / challenges**.
6. ENV docs mark QW auth/URL fields as **Unknown**.

## Phase 3 contents

1. Attack path: stub targets (`host`/`port`) → `exp_*.py <ip> <port>` → flag submit stub → round-tagged fact ledger.
2. **`lib/harvester.py`** — zero-token round-aware batch replay + `harvest-ledger.json` dedupe.
3. **`lib/unlock.py`** — `FIRST_BLOOD` / `PATCH_QUOTA` / `SLA_STATUS` gate (board-enriched).
4. **`lib/patcher.py`** — gated `submit_patch` stub; sandbox red-line hook (`sandbox_ok=False` refuses). Full sandbox pipeline deferred to Phase 4.

## Offline stub board smoke

```bash
cd awd
chmod +x scripts/stub-board-smoke.sh
bash scripts/stub-board-smoke.sh
```

Or manually:

```bash
export PLATFORM=qiangwang PLATFORM_STUB=1
export SAPPER_ROOT=/tmp/awd-runs
mkdir -p "$SAPPER_ROOT"
export PYTHONPATH=lib SAPPER_ARENA_LIB=$PWD/lib/arena.py
python3 -c "from arena import ArenaClient; import json; print(json.dumps(ArenaClient.load(None).board(), indent=2))"
cat "$SAPPER_ROOT/round-state.json"
bash docker/commander/worker.sh board --json
```

Local **model** smoke still uses `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` when you boot
the commander container; with `PLATFORM_STUB=1` the entrypoint skips live platform probes.

## Attack / harvester smoke (Phase 3)

```bash
cd awd
chmod +x scripts/attack-harvester-smoke.sh
bash scripts/attack-harvester-smoke.sh
```

Manual one-liner after dropping `exp_v1.py` under `$SAPPER_ROOT/<chall>/exploits/`:

```bash
export PLATFORM=qiangwang PLATFORM_STUB=1 SAPPER_ROOT=/tmp/awd-runs PYTHONPATH=lib
python3 lib/harvester.py --root "$SAPPER_ROOT" -c web-1 --json
python3 lib/patcher.py web-1 /path/to/artifact --root "$SAPPER_ROOT" --json
```

## Key paths

| Path | Notes |
|---|---|
| `lib/arena.py` | Qiangwang stub adapter |
| `lib/harvester.py` | Round-aware batch exploit replay |
| `lib/unlock.py` | FIRST_BLOOD / PATCH_QUOTA gate |
| `lib/patcher.py` | Gated patch submit stub |
| `lib/sapper.py` | Fact graph + `sapper arena board|…` |
| `exploits/exp_stub_template.py` | Exploit argv contract docs |
| `docker/ENV.md` | QW + model env (Unknown marked) |
| `docker/commander/worker.sh` | `board` reads round clock + unlock flags |
| `docker/commander/entrypoint.sh` | Stub-aware preflight |
| `scripts/stub-board-smoke.sh` | Phase 1–2 offline smoke |
| `scripts/attack-harvester-smoke.sh` | Phase 3 attack/unlock smoke |
| `tests/test_qiangwang_arena.py` | Stub + round-state unit tests |
| `tests/test_phase3_attack.py` | Harvester / unlock / patcher tests |

Upstream LICENSE (MIT) retained under `awd/LICENSE`.
