# 当前协作架构

```text
飞书 / Codex Desktop / CLI / CI
              │ 任务入口、展示、人工确认
              ▼
       Codex / GPT Orchestrator
              │ 先检查证据和委派必要性
              ├── 简单任务：独立完成
              └── 有界专家委派（默认一个，最多两个）
                    ├── Cursor CLI：Claude / Gemini / Grok
                    └── API：DeepSeek / Kimi / GLM / Qwen
              │
              ├── 授权工作区 + 有界 CTF 工具
              ├── .collab/events.jsonl 追加式事实账本
              └── 飞书 Docx 投影 / 群完成通知
```

## 编排原则

Codex/GPT 是唯一 orchestrator。简单、低歧义、单路径任务由它独立完成；仅当专长缺口、证据缺口或高风险独立复核具有明确收益时，才调用一名专家。第二名专家用于反例检查，模型数量本身不构成可信度。

专家接受有边界的子问题，返回 Findings、Evidence、Uncertainty 和 Suggested next action；它们不能继续分派、向飞书发消息或接管最终结论。Codex 回到文件、流量、字节码和复现环境核验报告。

## UI 和入站频道的位置

公开发行版不捆绑特定 Web UI。AgentScope、自有前端或飞书入站频道都可以作为任务入口和 Credential 外壳，但不能成为第二总控。入站消息应转成有边界的任务交给 `scripts/orchestrate.ps1`，最终事件再由 Docx 投影回飞书。

## 工作区与数据边界

工作区路径必须显式绑定到当前 run。Cursor Worker 使用 Ask 只读模式并绑定授权目录；API Worker只能看到事件摘要和显式 context file。SRC 默认对外发内容做最小化，CTF 样例可以按授权范围放宽。

## 多机边界

当前 demo 已有跨设备可见的飞书文档，但执行仍是单机。多机版本需要 Redis Streams 或数据库任务队列，至少实现 lease、心跳、幂等键、workspace/artifact 引用和失败接管。远端 Worker 只能领取 Codex 已批准的子任务。
