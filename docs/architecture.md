# IMF 结构

```text
Web UI / CLI
    │
    ▼
Force（派工，不代替特工下结论）
    ├── flash   DeepSeek V4 Flash high     API
    ├── pro     DeepSeek V4 Pro max        API
    ├── grok    Cursor Grok 4.6 high       Cursor CLI / HTTP
    └── luna    GPT Luna max               Cursor CLI / HTTP
    │
    ├── 飞书 Docx = 实时 board（共同修改）
    └── workplaces/<mission>/
          ├── board.md          暂停/结束后的快照
          ├── mission.md / notes.md / attachments/
          └── flash|pro|grok|luna/AGENTS.md
```

## 四名特工的分工

SRC 覆盖面大、要便宜、要管得过来。Flash 做广，Pro 做深，Grok 找反例，Luna 收口。两名走 DeepSeek API，两名走 Cursor。

## 飞书是通道，不是投影附件

旧版把本地账本事后抄到飞书。IMF 反过来：工作时以飞书文档为唯一 live board；本地 `board.md` 是收工快照。追加使用 `document_revision_id=-1`，四人可以同时写。人也可以打开同一篇文档改。

## Creator / 插件

`plugins/catalog.json` 是全部能力。每个小区 `AGENTS.md` 的 `## Plugins` 是该特工的装备。Web UI 右侧 Creator 用描述勾选插件，不必引入 Cordis。

## Web UI

`python -m imf web` 默认 `:3080`。标准库 HTTP，无 Node。三栏对应 Harness：会话列表、主舞台、创造模式。
