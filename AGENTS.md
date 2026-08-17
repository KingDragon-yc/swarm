# IMF — Instant Message Force

Four operatives share one Feishu document as the live board, then snapshot it to `workplaces/<mission>/board.md`.

## Operatives

| id | model | transport |
|---|---|---|
| flash | DeepSeek V4 Flash, reasoning high | DeepSeek API |
| pro | DeepSeek V4 Pro, reasoning max | DeepSeek API |
| grok | Cursor Grok 4.6 high | Cursor CLI, or HTTP if `CURSOR_API_BASE` is set |
| luna | GPT Luna max | Cursor CLI, or HTTP if `CURSOR_API_BASE` is set |

Feature/style for each operative is a stub this round.

## Workplace

```
workplaces/<mission>/
  board.md          # Feishu snapshot after pause/finish
  mission.md
  notes.md
  attachments/      # 题干与附件
  events.jsonl
  flash/AGENTS.md   # feature + plugins
  pro/AGENTS.md
  grok/AGENTS.md
  luna/AGENTS.md
```

Each cell chooses plugins from `plugins/catalog.json`. Edit `## Plugins` or use the Web UI creator panel.

## Commands

```
python -m imf doctor --probe-cursor
python -m imf web
python -m imf start "<title>" "<mission>" --attachments "<dir>"
python -m imf dispatch <mission_id> "<task>"
python -m imf snapshot <mission_id>
python -m imf finish <mission_id>
```

## Board

Feishu Docx is the live IM channel. Concurrent posts use `document_revision_id=-1`. Pause or finish pulls the document into `board.md`. Feishu failure cannot erase the local workplace.

Do not write API keys, App Secrets, cookies, or login state into the board, events, prompts, or cells.
