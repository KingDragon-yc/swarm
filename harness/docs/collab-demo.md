# Codex 主导的八模型协作 Demo

面向日常操作的完整手册见项目根目录 [`USER_MANUAL.md`](../../USER_MANUAL.md)；也可双击 `START_COLLAB.cmd` 打开快捷菜单。

## 最小闭环

```text
用户 / 飞书
    ↓
Codex / GPT（唯一 orchestrator）
    ├─ 简单任务：独立完成
    └─ 必要时：一次委派一个只读专家
          ├─ Cursor CLI：Claude / Gemini / Grok
          └─ OpenAI-compatible API：DeepSeek / Kimi / GLM / Qwen
    ↓
本地 events.jsonl（事实账本）
    ↓ 投影
飞书 Docx 文档 + 可选群完成通知
```

公开发行版提供独立的协作协议，不依赖 AgentScope 或其他 UI 框架，也不会把 DeepSeek 提升成第二个 orchestrator。需要图形界面或飞书入站频道时，可以在 start/note/delegate/finish 命令之上增加适配层。

## 1. 环境变量

凭据只放在进程环境或忽略的 `.env`，不要粘贴到命令参数。

```dotenv
FEISHU_APP_ID=
FEISHU_APP_SECRET=

# 可选：复用已有文档；留空时每个 run 创建一篇新文档
FEISHU_DOCUMENT_ID=
FEISHU_FOLDER_TOKEN=
FEISHU_CHAT_ID=

DEEPSEEK_API_KEY=
DEEPSEEK_PROVIDER=direct
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com

# Kimi / GLM / Qwen 必须明确填写模型名。
# 使用硅基流动时，同时替换 BASE_URL、MODEL 和相应 API_KEY。
KIMI_API_KEY=
KIMI_PROVIDER=direct
KIMI_MODEL=
KIMI_BASE_URL=https://api.moonshot.cn/v1

GLM_API_KEY=
GLM_PROVIDER=direct
GLM_MODEL=
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4

QWEN_API_KEY=
QWEN_PROVIDER=direct
QWEN_MODEL=
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

硅基流动已作为正式 provider 支持。先配置共享项：

```dotenv
SILICONFLOW_API_KEY=
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
```

再把需要走硅基流动的 Worker 设置为 `*_PROVIDER=siliconflow`，并填写该平台返回的精确 `*_MODEL`。四个 API Worker 可以混合使用直连和硅基流动。

Cursor CLI 使用当前登录态，也可由 `CURSOR_API_KEY` 提供认证。默认模型可以通过 `CURSOR_CLAUDE_MODEL`、`CURSOR_GEMINI_MODEL`、`CURSOR_GROK_MODEL` 覆盖。

三项默认值为 `auto`：系统会以 Cursor CLI 当前返回的精确模型 ID 为准，避免目录更新后被旧 ID 卡住。

飞书应用至少需要新版文档创建/编辑权限；若只写已有文档，也必须确保应用身份拥有该文档编辑权。群通知还需要发送消息权限。

## 2. 自检

```powershell
Set-Location .\harness
.\scripts\collab.ps1 doctor --probe-cursor
```

输出只显示“存在/缺失”，不会显示凭据值。`--probe-cursor` 会访问 Cursor 服务以核对登录态和模型 ID。

## 3. 简单任务：零委派

```powershell
.\scripts\collab.ps1 demo
```

该命令离线读取 `samples/catmisc01/note.txt`，由 orchestrator 路径完成 Base64 解码，事件中记录 `delegations: 0`。

## 4. 真实协作 run

默认启动可中途补充消息、处理审批并恢复的 Codex TUI：

```powershell
.\scripts\orchestrate.ps1 `
  -Task '审计当前授权项目的认证边界，并判断是否存在可利用链' `
  -Workspace 'D:\authorized-target' `
  -NoFeishuSync
```

先用 `-DryRun` 检查解析后的工作区和参数；确认飞书配置后移除 `-NoFeishuSync`。启动器使用显式 `workspace-write` 沙箱和按需审批。意外退出后运行 `..\START_COLLAB.cmd resume` 恢复最近一次会话。

快捷菜单会在启动前读取 Codex 随附模型目录，并选择模型、推理强度和权限。当前 CTF/SRC 默认为 `gpt-5.5`、`high`、`AutoReview`。命令行可传 `-CodexModel`、`-ReasoningEffort`、`-PermissionMode`；完全访问模式会关闭沙箱和审批，菜单中必须二次确认，命令行显式传参视为确认。

输入和权限都稳定、确实需要无人值守时，增加 `-Mode Batch`。Batch 使用 `codex exec`，结束后由脚本把最终消息记入账本；运行中不能插入新的用户消息。

也可以手动控制每一步：

```powershell
$start = python -m collab_demo start `
  '审计当前授权项目的认证边界，并判断是否存在可利用链' `
  --workspace 'D:\authorized-target' `
  --no-sync | ConvertFrom-Json

python -m collab_demo delegate $start.run_id claude `
  '只读检查认证相关代码；给出文件、行号、利用前置条件和不确定性' `
  --no-sync

python -m collab_demo finish $start.run_id `
  --summary-file 'D:\authorized-target\report.md' `
  --no-sync
```

确认本地闭环后移除 `--no-sync`，事件会投影到飞书文档。可以先用 `--mock` 验证委派日志而不消耗模型额度。

## 5. Orchestrator 的委派闸门

- 单文件识别、编码解码、明确报错定位：Codex 独立完成。
- 大型跨文件审计：优先一名 Claude 或 Gemini。
- 已形成高风险漏洞链，需要独立寻找反例：再加一名 Grok。
- API 模型只拿到事件摘要和 `--context-file` 明确提供的内容，避免整仓无差别外发。
- 专家报告是证据线索。Codex 必须回到文件、流量、字节码、日志或复现环境验证。

## 6. 当前 Demo 边界

这个版本在单机上由 Codex 统一发起 CLI/API 调用。飞书文档已经是跨设备可见的共享记录，但还没有多机任务租约、心跳和故障接管。下一阶段应增加 Redis Streams 或数据库队列，消息至少包含 `task_id`、`lease_owner`、`lease_until`、`workspace_ref`、`input_digest` 和幂等键；远端 Worker 仍只能领取 orchestrator 已批准的子任务。
