# AWD — Qiangwang AI-AWD sapper skeleton (in swarm)

Phase **1–2** adaptation of [sapper-v2](https://github.com/N1nEmAn/sapper-v2) `@7b75e6f` for the
10th Qiangwang Cup **AI-AWD** track. Lives under **`awd/`** so the existing IMF tree at the
swarm repo root is untouched.

Plan: project store `docs/awd-adaptation-plan.md` (phases 1–2 only here).

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

Later phases (attack wiring, unlock+patch doctrine rewrite, real API bind) are out of scope.

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

## Key paths

| Path | Notes |
|---|---|
| `lib/arena.py` | Qiangwang stub adapter |
| `lib/sapper.py` | Fact graph + `sapper arena board|…` |
| `docker/ENV.md` | QW + model env (Unknown marked) |
| `docker/commander/worker.sh` | `board` reads round clock |
| `docker/commander/entrypoint.sh` | Stub-aware preflight |
| `scripts/stub-board-smoke.sh` | Offline smoke |
| `tests/test_qiangwang_arena.py` | Stub + round-state unit tests |

Upstream LICENSE (MIT) retained under `awd/LICENSE`.
