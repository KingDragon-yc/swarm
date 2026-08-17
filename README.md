# IMF — Instant Message Force

IMF 把四个 AI 特工放在同一篇飞书文档上写 board。飞书是实时 IM 通道；任务暂停或结束后，把文档存成每个目标 workplace 大区里的 `board.md`。

Instant Message Force：善用飞书等 IM 做实时协同。特工行事风格下一轮再写。

## 四名特工

| id | 型号 | 接入 |
|---|---|---|
| **flash** | DeepSeek V4 Flash · high | DeepSeek API |
| **pro** | DeepSeek V4 Pro · max | DeepSeek API |
| **grok** | Cursor Grok 4.6 high | Cursor CLI，或 `CURSOR_API_BASE` HTTP |
| **luna** | GPT Luna max | Cursor CLI，或 `CURSOR_API_BASE` HTTP |

人在 Web UI 里派任务；四人一起往飞书 board 上写。

## 快速开始

Python 3.11+。

```bash
cp .env.example .env
python -m imf doctor
python -m imf demo
python -m imf web
```

浏览器打开 `http://127.0.0.1:3080`。界面按 DeepSeek Harness Web UI 的结构收成三栏：任务列表、飞书 board、右侧 Creator 选插件。

Windows 也可双击 `START_IMF.cmd`。

## Workplace

每个授权目标一个大区，四个特工各占一个小区：

```
workplaces/<mission>/
  board.md
  mission.md
  notes.md
  attachments/
  flash/AGENTS.md
  pro/AGENTS.md
  grok/AGENTS.md
  luna/AGENTS.md
```

`AGENTS.md` 里的 Feature 本轮只标角色名。`## Plugins` 是该特工自己的工具清单，对应 `plugins/catalog.json`，做法对齐 DeepSeek Harness 的创造模式：能力全部是插件，小区自行勾选。

## 飞书 board

配置 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 后，创建任务会新开一篇 Docx。四人并发追加同一文档（`document_revision_id=-1`）。人也在飞书里共同修改。Pause / Finish 会把文档拉回 `board.md`。飞书失败只记警告，不删本地 workplace。

## 命令

```bash
python -m imf start "auth-lab" "只审计当前授权目录" --attachments ./samples/src-lab
python -m imf dispatch latest "第一轮覆盖" --mock
python -m imf snapshot latest
python -m imf finish latest --summary "本轮结束"
```

真实派工去掉 `--mock`，并在 `.env` 里放 DeepSeek Key；Grok / Luna 需要本机已登录的 Cursor `agent`，或设置 `CURSOR_API_BASE`。

## 安全

只用于已授权的 SRC、靶场和本地实验。密钥只放在被忽略的 `.env`。Board、事件和提示词会脱敏。插件 `http-observe` 只观察授权范围内的请求形态，不生成利用载荷。
