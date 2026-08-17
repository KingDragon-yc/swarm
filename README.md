# IMF — Instant Message Force

IMF 把四个 AI 特工放在同一篇飞书文档上写 board。飞书是实时 IM 通道；任务暂停或结束后，把文档存成每个目标 workplace 大区里的 `board.md`。

Instant Message Force：善用飞书等 IM 做实时协同。四名特工按 **OODA** 串行：Observe → Orient → Decide → Act。

## 四名特工

| id | 型号 | 窗口 | OODA | 行事 |
|---|---|---|---|---|
| **flash** | DeepSeek V4 Flash · high | 1M | Observe | 发散、闲不住，前期侦察，先把观察写上 board |
| **pro** | DeepSeek V4 Pro · max | 1M | Orient | 神鬼二象性，忽上忽下，把 Flash 的观察摆正 |
| **luna** | GPT Luna max | 500k | Decide | 比较全能，拍板并给 Grok 写下有界命令 |
| **grok** | Cursor Grok 4.6 high | 256k | Act | 聪明但懒，指哪打哪，执行 Luna 的命令后停 |

Flash / Pro 走 DeepSeek API；Grok / Luna 走 Cursor CLI，或 `CURSOR_API_BASE` HTTP。
Cursor operative 使用完整行动模式，并以当前 workplace 作为 workspace/sandbox 边界。
macOS/Linux 使用 Cursor OS sandbox；Windows CLI 不支持该模式，会退回其 allowlist 模式，仍由当前 workplace、工作目录和提示约束边界。

## 上下文

board 是长期记忆，对话不是。每人按自己的窗口截取 board 摘录；**压缩之前必须已经把要点写上飞书**。一项任务结束后：

1. 把 `## Lessons` 追加进该小区 `AGENTS.md` 的 Notes；
2. `/clear` 或新开对话（IMF 不 `--resume`）。

## 快速开始

Python 3.11+。

```bash
cp .env.example .env
python -m imf doctor
python -m imf demo
python -m imf web
```

浏览器打开 `http://127.0.0.1:3080`。三栏：任务列表、飞书 board、右侧 Creator 选插件。Windows 也可双击 `START_IMF.cmd`。

## Workplace

```
workplaces/<mission>/
  board.md
  mission.md
  notes.md
  attachments/
  flash/AGENTS.md
  pro/AGENTS.md
  luna/AGENTS.md
  grok/AGENTS.md
```

`## Plugins` 对应 `plugins/catalog.json`，对齐 DeepSeek Harness 创造模式。模型可以提出插件建议，实际变更由本地操作者确认。
每次 dispatch 先让 flash、pro、luna、grok 四席签到；任一席离线，整轮行动会被阻断。
mission 只能是 `workplaces/` 的直接子目录，附件会复制进该 workplace，并拒绝链接、超大目录和自包含复制。

## 飞书 board

配置 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 后，创建任务会新开一篇 Docx。OODA 四人依次追加同一文档。Pause / Finish 把文档拉回 `board.md`。

## 命令

```bash
python -m imf start "auth-lab" "只审计当前授权目录" --attachments ./samples/src-lab
python -m imf checkin latest --mock
python -m imf dispatch latest "第一轮 OODA" --mock
python -m imf snapshot latest
python -m imf finish latest --summary "本轮结束"
```

`--parallel` 会跳过 OODA 顺序、四人同时写，但仍要求四席先签到。默认不要开。

## 安全

只用于已授权的 SRC、靶场和本地实验。密钥只放在被忽略的 `.env`。Board、事件和提示词会脱敏。
