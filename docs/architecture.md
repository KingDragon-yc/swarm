# IMF 结构

```text
Web UI / CLI
    │
    ▼
Force 按 OODA 派工
    ├── flash   Observe   DeepSeek V4 Flash   1M     API
    ├── pro     Orient    DeepSeek V4 Pro     1M     API
    ├── luna    Decide    GPT Luna max        500k   Cursor CLI / HTTP
    └── grok    Act       Cursor Grok 4.6     256k   Cursor CLI / HTTP
    │
    ├── 飞书 Docx = 实时 board（共同修改）
    └── workplaces/<mission>/
          ├── board.md          暂停/结束后的快照
          ├── mission.md / notes.md / attachments/
          └── flash|pro|luna|grok/AGENTS.md
```

## OODA

Flash 闲不住、1M 窗口，先把授权面铺开。Pro 神鬼二象性，读 Flash 的 log 调整坐标系。Luna 500k、比较全能，写一条给 Grok 的有界命令。Grok 256k、懒，指哪打哪。

默认串行。后一人读到前一人已经写上 board 的内容。`--parallel` 才四人同时写。

## 上下文

每人按自己的窗口截取 board。压缩只切已经落盘的 board，不丢未发布的话。任务结束把 `## Lessons` 写入该小区 `AGENTS.md`，然后新开对话（等同 `/clear`）。Cursor 调用不带 `--resume`。

## 飞书

工作时以飞书文档为 live board；本地 `board.md` 是收工快照。追加使用 `document_revision_id=-1`。

## Creator / 插件

`plugins/catalog.json` 是全部能力。每个小区 `AGENTS.md` 的 `## Plugins` 是该特工的装备。默认插件按 OODA 配：侦察偏 search，行动偏 shell / http-observe。

## Web UI

`python -m imf web` 默认 `:3080`。标准库 HTTP。三栏：会话列表、board、创造模式。
