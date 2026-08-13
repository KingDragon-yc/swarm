# Swarm

Swarm 是一个由 Codex/GPT 担任唯一 orchestrator 的八模型稀疏协作框架，面向已授权的 CTF、靶场和 SRC 工作。

它遵循一个简单原则：能由总控独立完成的任务不唤醒其他模型；只有出现明确的专长缺口、证据缺口或高风险复核需求时，才委派一名只读专家，必要时再增加一名反例检查者。

## 模型拓扑

- Codex/GPT：唯一 orchestrator，通过交互式 Codex TUI 或无人值守 `codex exec` 运行；
- Claude、Gemini、Grok：通过 Cursor Agent CLI 调用；
- DeepSeek、Kimi、GLM、Qwen：通过厂商或 SiliconFlow 的 OpenAI-compatible API 调用；
- 飞书：把追加式事件账本投影到 Docx 文档，并可选发送群完成通知。

## 快速开始

环境要求：Windows 10/11、PowerShell、Python 3.11+、已安装并登录的 Codex CLI。使用 Cursor 专家时还需安装并登录 Cursor Agent CLI。

```powershell
Copy-Item .\harness\.env.example .\harness\.env
notepad .\harness\.env
```

所有真实凭据只应写入被 Git 忽略的 `harness\.env`。

双击：

```text
START_COLLAB.cmd
```

推荐流程：

1. 选择 `[1]` 完成环境自检；
2. 选择 `[2]` 运行离线零委派 Demo；
3. 选择 `[3]` 启动交互式 Codex 总控；
4. 按提示选择模型、推理强度和权限模式。

命令行示例：

```powershell
.\START_COLLAB.cmd doctor
.\START_COLLAB.cmd demo
.\START_COLLAB.cmd dry-run "分析附件并寻找 flag" "D:\CTF\challenge"
.\START_COLLAB.cmd task "分析附件并寻找 flag" "D:\CTF\challenge" -NoFeishuSync
```

完整操作说明见 [USER_MANUAL.md](USER_MANUAL.md)，协作协议见 [AGENTS.md](AGENTS.md)，架构说明见 [harness/docs/architecture.md](harness/docs/architecture.md)。

## 安全边界

- 仅用于明确授权的 CTF、靶场、实验室和 SRC 范围；
- Cursor Worker 强制使用只读 Ask 模式；
- API Worker 只接收账本摘要和显式提供的上下文文件；
- API Key、App Secret、Cookie、登录态和运行账本不得提交或同步到公开文档；
- 模型结论必须回到文件、流量、字节码、日志或复现环境核验；
- 本地 FullAccess 只影响沙箱和审批，无法绕过模型服务端的内容安全策略。

## 当前状态

已实现单机稀疏协作、八模型名册、Cursor/API 统一适配、模型自动解析、追加式本地账本、飞书 Docx 投影、交互恢复、批处理入口和离线测试。多机租约、心跳、幂等执行与失败接管仍在路线图中。

本公开仓库刻意不包含真实凭据、运行记录、题目附件、登录态、构建产物，以及早期实验中使用的 AgentScope/XuanMu/Buzz Desktop 上游源码副本。
