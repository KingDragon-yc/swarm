# Buzz 八模型协作系统 · USER MANUAL

版本：Demo 0.1

适用环境：Windows 10/11、PowerShell、Python 3.11+
用途：已授权的 CTF、靶场和 SRC 安全研究

## 1. 三十秒启动

### 图形菜单

双击项目根目录：

```text
START_COLLAB.cmd
```

首次使用依次选择：

1. `环境自检`
2. `离线零委派 Demo`
3. `启动交互式 Codex 总控任务`

菜单 `[3]` 会先选择模型、推理强度和本地权限，再进入 Codex TUI。当前默认推荐 `GPT-5.5 / high / 工作区写入 + 自动审批复核`。运行中可以继续输入消息、纠正路径、补充授权范围并处理审批；意外退出后选择 `[9]` 恢复最近一次会话。需要完全无人值守时才选 `[B]`。

### 命令行

在项目根目录打开 PowerShell：

```powershell
# 检查 Python、Cursor、模型与飞书配置
.\START_COLLAB.cmd doctor

# 无网络、无 API key、无专家调用的离线验收
.\START_COLLAB.cmd demo

# 只检查参数和工作区，不调用 Codex
.\START_COLLAB.cmd dry-run "检查附件类型并给出分析计划" "D:\CTF\challenge"

# 启动可交互、可恢复的 Codex/GPT 总控任务，暂不写飞书
.\START_COLLAB.cmd task "分析附件并寻找 flag" "D:\CTF\challenge" -NoFeishuSync

# 跳过启动问答，直接指定模型、推理强度和权限
.\START_COLLAB.cmd task "分析附件并寻找 flag" "D:\CTF\challenge" `
  -CodexModel gpt-5.5 -ReasoningEffort high -PermissionMode AutoReview `
  -NoFeishuSync

# 恢复最近一次 Buzz 交互会话
.\START_COLLAB.cmd resume

# 无人值守批处理；执行中不能插入补充消息
.\START_COLLAB.cmd batch "分析附件并寻找 flag" "D:\CTF\challenge" -NoFeishuSync

# 查看最近一次运行的完整事件
.\START_COLLAB.cmd show

# 列出机器人可见群及 chat_id（需配置飞书凭据）
.\START_COLLAB.cmd chats

# 创建测试飞书文档并返回 document_id
.\START_COLLAB.cmd new-doc "Buzz 协作测试"

# 打开本手册
.\START_COLLAB.cmd manual
```

路径或任务包含空格时必须加双引号。

菜单模式会尝试识别任务末尾已经存在的 Windows 文件或目录路径，并将其作为“授权工作区”的默认值展示。仍请在回车前核对一次；这个目录决定 Codex 和 Cursor Worker 可以读取、写入和复核哪些题目材料。

## 2. 系统如何工作

```text
用户 / 飞书 / CLI
        ↓
Codex / GPT（唯一 orchestrator）
        ├─ 简单任务：独立处理
        └─ 必要时委派一名专家，最多两名
             ├─ Cursor：Claude / Gemini / Grok
             └─ API：DeepSeek / Kimi / GLM / Qwen
        ↓
.collab 追加式本地账本
        ↓
飞书 Docx 文档投影 + 可选群通知
```

路由规则强调节制：单文件识别、编码解码、明确报错定位由 Codex 完成；大型跨文件审计、专门领域缺口或高风险复核才会唤醒专家。专家报告需要回到文件、流量、字节码或复现环境验证。

## 3. 快捷启动器命令

| 命令 | 是否调用模型 | 是否写飞书 | 用途 |
|---|---:|---:|---|
| `START_COLLAB.cmd` | 视菜单而定 | 视配置而定 | 打开交互菜单 |
| `START_COLLAB.cmd doctor` | 否 | 否 | 环境和模型探针 |
| `START_COLLAB.cmd demo` | 否 | 否 | 离线 Base64 样例 |
| `START_COLLAB.cmd dry-run "任务" "目录"` | 否 | 否 | 检查 Codex、工作区和沙箱参数 |
| `START_COLLAB.cmd task "任务" "目录"` | 是 | 已配置时 | 交互式 Codex 总控，可纠偏、审批和恢复 |
| `START_COLLAB.cmd task ... -NoFeishuSync` | 是 | 否 | 只写本地账本 |
| `START_COLLAB.cmd resume` | 是 | 依原 run | 恢复最近一次 Buzz 交互会话 |
| `START_COLLAB.cmd batch "任务" "目录"` | 是 | 已配置时 | 无人值守 `codex exec` 流程 |
| `START_COLLAB.cmd show` | 否 | 否 | 查看最近一次 run |
| `START_COLLAB.cmd chats` | 否 | 读取飞书 | 列出机器人所在群及 `chat_id` |
| `START_COLLAB.cmd new-doc "标题"` | 否 | 创建文档 | 返回新文档的 `document_id` |
| `START_COLLAB.cmd manual` | 否 | 否 | 用记事本打开本手册 |

底层脚本位于：

- `harness\scripts\quickstart.ps1`：菜单和快捷命令分发；
- `harness\scripts\orchestrate.ps1`：默认启动可恢复的 Codex TUI，也可用 Batch 模式调用 `codex exec`；
- `harness\scripts\collab.ps1`：运行账本、路由和专家委派命令。

## 4. 环境准备

### 最小协作环境

只运行 Codex 总控和离线 Demo，需要：

- Python 3.11 或更高版本；
- Codex CLI 已安装并登录；
- 授权工作区路径存在。

Cursor 专家还需要：

- Cursor Agent CLI 的 `agent` 命令；
- Cursor 已登录，或配置 `CURSOR_API_KEY`；
- 当前账户具有所选 Claude/Gemini/Grok 模型。

本公开仓库的协作核心只使用 Python 标准库，不要求 Node.js、pnpm 或 Redis。早期实验使用的 AgentScope Web UI 与 XuanMu 参考源码未包含在公开发行版中。

## 5. 本机配置文件

协作层自动读取：

```text
harness\.env
```

首次配置：

```powershell
Copy-Item .\harness\.env.example .\harness\.env
notepad .\harness\.env
```

`.env` 已被 Git 忽略。真实 API key、App Secret、Cookie、登录态不能进入源码、任务提示词、飞书文档或聊天记录。进程环境变量的优先级高于 `.env`。

### Cursor 三模型

通常只需登录 Cursor；默认模型已经给出：

```dotenv
CURSOR_CLAUDE_MODEL=auto
CURSOR_GEMINI_MODEL=auto
CURSOR_GROK_MODEL=auto
```

`auto` 会在每次真实委派前读取 `agent --list-models`，按厂商选择当前可用模型，并在事件 metadata 中记录最终 ID。需要固定模型时仍可填精确 ID；若该 ID 下架，自检会给出候选列表。

用以下命令核对当前账号的真实模型列表和自动选择结果：

```powershell
.\START_COLLAB.cmd doctor
```

若 Cursor 更新了模型 ID，在 `.env` 中覆盖相应值。

### 四个 API 模型

DeepSeek 示例：

```dotenv
DEEPSEEK_API_KEY=<local secret>
DEEPSEEK_PROVIDER=direct
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

Kimi、GLM、Qwen 必须同时填写 API key 和模型名。使用硅基流动时，把该模型的 `BASE_URL`、`MODEL`、`API_KEY` 一起切换到对应配置，避免混用厂商模型名和端点。

### 硅基流动共享入口

一个 SiliconFlow Key 可以被多个 API Worker 复用：

```dotenv
SILICONFLOW_API_KEY=<local siliconflow secret>
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1

DEEPSEEK_PROVIDER=siliconflow
DEEPSEEK_MODEL=<SiliconFlow 中的精确模型 ID>

KIMI_PROVIDER=siliconflow
KIMI_MODEL=<SiliconFlow 中的精确模型 ID>

GLM_PROVIDER=siliconflow
GLM_MODEL=<SiliconFlow 中的精确模型 ID>

QWEN_PROVIDER=siliconflow
QWEN_MODEL=<SiliconFlow 中的精确模型 ID>
```

`*_PROVIDER` 可取 `direct` 或 `siliconflow`，因此可以让 DeepSeek 走厂商直连、GLM/Qwen 走硅基流动。选择 `siliconflow` 后，对应的 `*_API_KEY` 和 `*_BASE_URL` 可以留空；实际使用共享的 `SILICONFLOW_*`。

模型 ID 会随平台上架情况变化。登录硅基流动后查看模型广场，或调用：

```powershell
$headers = @{ Authorization = "Bearer $env:SILICONFLOW_API_KEY" }
Invoke-RestMethod https://api.siliconflow.cn/v1/models -Headers $headers
```

## 6. 飞书配置

若凭据曾进入聊天、截图或日志，先在飞书开放平台轮换 App Secret。

在 `harness\.env` 填写：

```dotenv
FEISHU_APP_ID=<local app id>
FEISHU_APP_SECRET=<rotated local secret>

# 留空时每个 run 创建一篇新文档
FEISHU_DOCUMENT_ID=

# 可选：新文档所在目录
FEISHU_FOLDER_TOKEN=

# 可选：任务完成时通知的群 chat_id
FEISHU_CHAT_ID=
```

这三个值都是可选项：

- `FEISHU_DOCUMENT_ID`：指定一篇长期复用的新版文档。普通 Docx URL 形如 `https://example.feishu.cn/docx/<document_id>`，取 `/docx/` 后的部分。留空时每个 run 自动创建新文档，并把生成的 ID 写入该 run 的 `state.json`。知识库 `/wiki/` URL 中的 token 不能直接使用，需要通过 wiki 节点接口取得实际 `obj_token`。
- `FEISHU_FOLDER_TOKEN`：决定自动创建的新文档放在哪个文件夹。文件夹 URL 形如 `https://example.feishu.cn/drive/folder/<folder_token>`，取 `/folder/` 后的部分。留空时不指定目标文件夹。
- `FEISHU_CHAT_ID`：任务完成通知发往哪个群，通常以 `oc_` 开头。留空只会关闭群完成通知，不影响文档同步。

最省事的查看方式：

```powershell
# 机器人必须已经加入目标群
.\START_COLLAB.cmd chats

# 如需固定复用一篇文档，也可以由系统创建
.\START_COLLAB.cmd new-doc "Buzz CTF/SRC 协作记录"
```

`chats` 输出群名和 `chat_id`，把目标群的值复制到 `FEISHU_CHAT_ID`。`new-doc` 输出 `document_id`；需要所有 run 共用一篇文档时才写入 `FEISHU_DOCUMENT_ID`。

飞书应用至少需要：

- 创建/编辑新版文档权限；
- 获取机器人所在群列表/群信息权限；
- 若配置群通知，需要机器人发消息权限；
- 使用已有 `FEISHU_DOCUMENT_ID` 时，应用身份需要该文档编辑权；
- 机器人需要加入目标群，群聊入站通常需要 @ 机器人。

先做不写飞书的演练：

```powershell
.\START_COLLAB.cmd task "读取样例并报告编码" ".\harness\samples\catmisc01" -NoFeishuSync
```

确认本地 run 正常，再去掉 `-NoFeishuSync`。

## 7. 推荐工作流

### 交互式与批处理

`task`/菜单 `[3]` 是默认入口。它先读取本机随 Codex 安装的模型目录，然后让你选择模型、推理强度和权限，再启动持久化 Codex TUI。沙箱越界、路径错误、证据缺失或模型调用异常都可以在原会话中补充信息；退出后用 `resume`/菜单 `[9]` 继续。run 在真正写入最终报告前保持 `active`。

当前模型列表中优先展示 `gpt-5.5`，并默认选择 `high`。其余选项来自本机 Codex 可见目录；当前可见项包括 5.6 Sol/Terra/Luna 和 5.2。目录中被隐藏但账号仍可使用的 5.4 等型号，可以通过 `[C]` 手工输入精确 ID。`ultra` 会启用 Codex 内部子代理，与本项目“GPT 唯一总控、稀疏委派”的协议冲突，因此启动菜单不提供 Ultra。

权限选项对应关系：

| 菜单 | Codex 参数 | 行为 |
|---|---|---|
| 工作区写入 + 自动审批复核 | `workspace-write` + `on-request` + `auto_review` | 推荐；保留工作区边界，符合条件的审批由审批模型处理 |
| 工作区写入 + 人工审批 | `workspace-write` + `on-request` | 越界和网络等操作会停下来询问 |
| 工作区写入 + 永不询问 | `workspace-write` + `never` | 不弹审批；超出边界的操作直接失败 |
| 只读 + 人工审批 | `read-only` + `on-request` | 适合只看材料，不允许直接修改 |
| 完全访问 + 无审批 | `--dangerously-bypass-approvals-and-sandbox` | 关闭沙箱与审批；只用于隔离、可信、可恢复的本地 CTF 环境 |

菜单 `[4]` 也会显示相同选择器，但只输出解析后的启动参数，不创建 run、不调用模型。命令行可用 `-CodexModel`、`-ReasoningEffort` 和 `-PermissionMode` 跳过问答；`PermissionMode` 可选 `AutoReview`、`Auto`、`WorkspaceNever`、`ReadOnly`、`FullAccess`。菜单选择 FullAccess 时必须输入 `FULL` 二次确认；命令行显式传入 `-PermissionMode FullAccess` 本身视为确认。

`batch`/菜单 `[B]` 使用 `codex exec`，适合输入和权限已经稳定的重复任务、CI 或夜间批处理。它会流式显示进度，但运行中无法接收新的用户消息；失败会记录为 `failed`。官方也把 `codex exec` 定位为非交互自动化接口。

### CTF

```powershell
.\START_COLLAB.cmd dry-run `
  "分析附件，识别题型，寻找最短 flag 路径" `
  "D:\CTF\challenge"

.\START_COLLAB.cmd task `
  "分析附件，识别题型，寻找最短 flag 路径；所有操作限于当前授权目录" `
  "D:\CTF\challenge"
```

Codex 会先做题型判断。任务短时独立完成；需要代码审计、长上下文或独立反例时再调用合适专家。

### SRC

SRC 任务需要把范围写清楚：

```powershell
.\START_COLLAB.cmd task `
  "只审计 src/auth 和 src/api；禁止访问生产系统；输出代码证据、利用前置条件、影响和修复建议" `
  "D:\SRC\authorized-project" `
  -NoFeishuSync
```

建议先关闭飞书同步，确认报告已经脱敏，再决定是否投影到在线文档。API Worker 默认只接收本地事件摘要和显式提供的上下文。

## 8. 手工控制专家委派

多数时候让 Codex 自动决策即可。需要调试适配器时，可在 `harness` 目录手工操作：

```powershell
Set-Location .\harness

$run = python -m collab_demo start `
  "审计认证边界" `
  --workspace "D:\SRC\authorized-project" `
  --no-sync | ConvertFrom-Json

python -m collab_demo delegate $run.run_id claude `
  "只读检查认证代码，报告文件、行号、前置条件和不确定性" `
  --no-sync

python -m collab_demo show $run.run_id
```

可用专家名：

```text
claude gemini grok deepseek kimi glm qwen
```

用 `--mock` 可以验证委派生命周期而不调用厂商 API：

```powershell
python -m collab_demo delegate $run.run_id deepseek "验证交付格式" --mock --no-sync
```

## 9. 运行记录

每次任务生成一个 run：

```text
harness\.collab\runs\<run_id>\
├─ state.json    当前状态、工作区和飞书 document_id
├─ events.jsonl  追加式机器账本
└─ brief.md      便于阅读的时间线
```

`.collab` 已被 Git 忽略。查看最近一次运行：

```powershell
.\START_COLLAB.cmd show
```

状态含义：

- `active`：任务仍在执行；
- `completed`：Codex 已写入最终报告；
- `failed`：启动器或模型调用失败，错误已经落账。

## 10. 可选 UI 集成

公开发行版聚焦可审计的命令行协作核心，没有捆绑早期实验使用的 AgentScope Web UI。需要图形界面时，可以把 `collab_demo` 的 start/note/delegate/finish 命令接入自有前端；无论采用何种 UI，Codex/GPT 仍应保持为唯一总控。

## 11. 故障排查

### `Python was not found`

安装 Python 3.11+，确认：

```powershell
python --version
```

### `Codex CLI was not found`

确认 Codex CLI 已加入 PATH 并完成登录：

```powershell
codex --version
```

### Cursor 显示模型不可用

```powershell
agent --list-models
.\START_COLLAB.cmd doctor
```

默认的 `CURSOR_*_MODEL=auto` 会重新读取目录并选择可用模型。如果手动锁定的 ID 已下架，自检会列出同厂商候选；可以改回 `auto`，也可以换成候选中的精确 ID。模型刚更新时偶尔会出现一次目录刷新误判，间隔几秒再次自检即可确认。

### 飞书显示缺少凭据

检查 `harness\.env` 是否存在，变量名是否正确。`doctor` 只报告存在性，不输出秘密值。

### 飞书返回 403 或文档无权编辑

检查飞书应用的 Docx 权限、版本是否已发布、应用身份是否拥有目标文档权限。可以先清空 `FEISHU_DOCUMENT_ID`，让系统创建测试文档。

### `UnknownIssuer`、网络超时

先在普通 PowerShell 中运行 `codex exec` 或 `agent --list-models`，确认代理、企业 CA 和系统时间。受限沙箱可能禁止网络；换到正常本机终端执行快捷入口。

### Windows sandbox helper 报“拒绝访问”

先运行 `codex doctor --summary`，再更新 Codex并重新检查 Windows sandbox 安装状态。交互模式下可以在原会话说明错误、审批安全的替代执行方式，或退出后用 `START_COLLAB.cmd resume` 继续。若题目位于隔离、可信、可恢复的本地目录，可新建会话并在权限菜单选择 `[5] 完全访问`，以绕开本地 Windows 沙箱组件；这不会绕过平台内容安全策略。批处理模式会把无法恢复的任务标记为 `failed`。

### `This content was flagged for possible cybersecurity risk`

这是模型服务端的网络安全分类结果，与本地 `/permissions`、Windows 沙箱或管理员权限分属不同层次。选择 FullAccess 也不会解除内容拦截。在交互会话中补充明确的本地授权范围、题目类型和禁止触碰的外部目标；也可以在新会话选择当前实测更适用的 `gpt-5.5 / high`。若仍被策略拒绝，启动器不能绕过平台安全控制；符合条件的团队需要按错误信息中的链接申请 Trusted Access for Cyber。已有本地 run 可以保留，取得权限或缩小任务范围后继续。

### 冷启动较慢

首次 Codex TUI 或 `codex exec` 可能加载模型目录、插件和技能。简单任务的首次运行可能需要一到三分钟，后续通常更快。

## 12. 当前边界与后续路线

当前版本已完成单机 Codex 总控、稀疏委派、本地账本和飞书文档投影。多机执行队列仍待建设；下一阶段建议使用 Redis Streams 或 PostgreSQL，实现：

- `task_id` 和幂等键；
- Worker lease 与心跳；
- `workspace_ref` / `artifact_ref`；
- 超时回收与失败接管；
- 远端 Worker 只领取 Codex 已批准的子任务。

架构细节见 `harness\docs\architecture.md`。
