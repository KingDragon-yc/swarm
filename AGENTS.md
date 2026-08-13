# Codex / GPT Orchestrator Protocol

本仓库用于已授权的 CTF、靶场和 SRC 工作。Codex/GPT 是唯一 orchestrator，负责理解目标、检查证据、执行本地工作、决定是否委派并形成最终结论。

## 稀疏委派

1. 简单、单路径、低歧义任务全部由 Codex 完成，不调用其他模型。
2. 只有出现明确的专长缺口、证据缺口，或高风险结论需要独立复核时才委派。
3. 默认只调用一名专家；第二名只用于独立反例检查。禁止为了“多模型”而一次唤醒全部七名专家。
4. 专家只提供有边界的报告，不能接管编排、继续分派、修改工作区或向外部系统发消息。
5. Codex 对专家结论做证据核验；模型间投票不等于事实。

## 协作命令

在 `harness` 目录运行：

```powershell
python -m collab_demo doctor --probe-cursor
python -m collab_demo start "<task>" --workspace "<authorized workspace>"
python -m collab_demo note <run_id> "<verified progress>"
python -m collab_demo delegate <run_id> <agent> "<bounded question>"
python -m collab_demo finish <run_id> --summary-file "<final report>"
```

先读取 `route` 的建议，再由 Codex 结合证据决定。委派对象仅限 `claude`、`gemini`、`grok`、`deepseek`、`kimi`、`glm`、`qwen`。

## 状态和飞书

- `.collab/runs/<run_id>/events.jsonl` 是本地追加式事实账本，`brief.md` 是人类可读投影。
- 配置飞书凭据后，命令会把同一事件追加到一篇飞书文档；飞书失败不能抹掉本地记录。
- 任何 API key、App Secret、登录态、Cookie 都不能写入仓库、事件、提示词或飞书文档。
- SRC 材料发往外部模型前先做数据最小化；API Worker 只接收显式上下文文件。

## 写作风格

尽量少使用“不是……而是……”“稳稳接住”等模板化表达。结论以证据、文件、偏移、请求和可复现命令支撑。
