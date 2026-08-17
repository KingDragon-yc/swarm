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

每轮先并发签到四席，全部在线后才进入行动。默认串行，后一人读到前一人已经写上 board 的内容；`--parallel` 才四人同时写，但仍保留签到闸门。

## 上下文

每人按自己的窗口截取 board。压缩只切已经落盘的 board，不丢未发布的话。任务结束把 `## Lessons` 写入该小区 `AGENTS.md`，然后新开对话（等同 `/clear`）。Cursor 使用完整行动模式，通过 `--workspace`、`--trust` 和平台支持的 sandbox/allowlist 模式绑定当前 workplace，不带 `--resume`。macOS/Linux 使用 `--sandbox enabled`；Windows CLI 不支持 OS sandbox，使用其 `--sandbox disabled` allowlist 模式，Windows workplace 应视为本地操作边界而非主机级隔离。

## 飞书

工作时以飞书文档为 live board；本地 `board.md` 是收工快照。每个 mission 首次同步通过 `FEISHU_FOLDER_TOKEN` 创建自己的文档，生成的 `document_id` 写回 workplace；旧 harness 的 `FEISHU_DOCUMENT_ID` 不会被覆盖。追加使用 `document_revision_id=-1`。同步失败时保留本地未同步条目，避免旧远端内容覆盖本地证据。

## 长轮次与心跳

dispatch 默认每席允许 1800 秒空闲时间，硬上限 7200 秒；Cursor 使用流式 JSON 输出，每 600 秒检查一次是否有正常输出，有输出就刷新空闲窗口。超出硬上限或长期无输出会终止该席并把失败写入事件账本。

## Creator / 插件

`plugins/catalog.json` 是全部能力。每个小区 `AGENTS.md` 的 `## Plugins` 是该特工的装备。默认插件按 OODA 配：侦察偏 search，行动偏 shell / http-observe。模型只能提出插件建议，实际变更由本地操作者确认。

## Web UI

`python -m imf web` 默认 `:3080`。标准库 HTTP。三栏：会话列表、board、创造模式。mission 只允许是 `workplaces/` 的直接子目录，附件复制后留在该 workplace 内。
