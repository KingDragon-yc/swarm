# IMF — Instant Message Force

Four operatives share one Feishu document as the live board, then snapshot it to `workplaces/<mission>/board.md`.

## OODA

Dispatch is sequential unless `--parallel` is set.

| id | OODA | window | style |
|---|---|---|---|
| flash | Observe | 1M | Divergent, restless scout. Dump coverage. Do not decide. |
| pro | Orient | 1M | God/ghost oscillation. Reframe Flash. Do not act. |
| luna | Decide | 500k | Generalist. Pick one path. Write Grok's order. |
| grok | Act | 256k | Sharp and lazy. Execute Luna's order. Stop. |

Durable memory is the Feishu board plus each cell's `AGENTS.md`. The chat is disposable.

- Before context compression, post what must survive to the board.
- After a task, write `## Lessons` into that cell's `AGENTS.md` Notes.
- Then `/clear` or open a new conversation. IMF never `--resume`.

## Workplace

```
workplaces/<mission>/
  board.md          # Feishu snapshot after pause/finish
  mission.md
  notes.md
  attachments/
  events.jsonl
  flash/AGENTS.md   # Observe + plugins + lessons
  pro/AGENTS.md
  luna/AGENTS.md
  grok/AGENTS.md
```

Each cell chooses plugins from `plugins/catalog.json`.

## Commands

```
python -m imf doctor --probe-cursor
python -m imf web
python -m imf start "<title>" "<mission>" --attachments "<dir>"
python -m imf dispatch <mission_id> "<task>"
python -m imf snapshot <mission_id>
python -m imf finish <mission_id>
```

Do not write API keys, App Secrets, cookies, or login state into the board, events, prompts, or cells.
