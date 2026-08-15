# Momcozy 单智能体架构

## 架构目标

CozyMate 只运行一个 `main_agent`。它在同一个模型—工具 loop 中理解请求、
渐进加载服务 Skill、搜索业务 Tool、执行动作并形成最终回复，不再创建或委派
产前、泌乳、设备子智能体。

当前 Runtime Pattern 仍为 `proprietary_runtime`，版本为
`momcozy-agent-v5`：

- 自研 durable `AgentLoop` 继续负责 Run 租约、追加式账本、Action 确认、
  崩溃恢复、Context Pipeline、最终消息和业务审计。
- `OpenAIAgentsExecutionEngine` 使用 OpenAI Agents SDK 的单个 `Agent`、
  `Runner`、`FunctionTool`、`ToolSearchTool`、namespace 和生命周期 Hook，
  负责一次执行中的模型—工具 loop。
- Runtime 账本仍是唯一恢复事实源；不启用 SDK `Session`，也不使用 provider
  response ID 作为恢复状态。

这次迁移只改变 Agent 拓扑和 Skill/Tool 的加载方式，不采用参考项目的上下文
构造或运行时边界。

## 代码组织

- `app/agents/main_agent/definition.py` 定义唯一 Agent。
- `app/agents/main_agent/system_prompt.md` 定义稳定角色、渐进加载顺序和回复规则。
- `app/agents/main_agent/skills/<skill_id>/<version>/SKILL.md` 保存完整、版本化的
  `prenatal`、`lactation`、`device` 工作流。
- `app/agents/main_agent/skill_registry.py` 校验 Skill frontmatter、版本和内容，
  并提供 `load_service_skill` Tool contract/handler。
- `app/agents/main_agent/toolset.py` 定义唯一 Tool allowlist 和 deferred namespace。
- `app/capabilities/<capability>/` 继续保存 canonical Tool contract、handler、
  reference、Action applicator 和策略；迁移不复制业务实现。
- `app/bootstrap/` 将唯一 Agent、Skill loader、业务 Capability 和基础设施适配器
  组装后注入 Runtime。`app/agent_runtime/` 不导入应用 Agent 定义。

旧的 `prenatal_agent`、`lactation_agent`、`device_agent` 包、agent-as-tool
契约、委派分支和委派恢复事件均已移除。

## 稳定 Prompt 与渐进 Skill

稳定 Prompt 只包含共同安全规则、单 Agent 工作方式，以及以下轻量 Skill
manifest：Skill ID、版本和简介。完整 Skill 正文不会预先拼入 system/developer
Prompt。

当请求需要专业工作流时，模型先单独调用：

```text
load_service_skill({"skill_id":"prenatal|lactation|device"})
```

loader 返回一个普通、可持久化的 `function_call_output`：

```json
{
  "schema_version": "momcozy.service_skill.v1",
  "skill_id": "prenatal",
  "version": "v1",
  "description": "...",
  "content": "完整原始 SKILL.md（含 frontmatter 与正文）",
  "content_sha256": "..."
}
```

该结果按实际发生顺序直接追加到当前 Run 最新上下文，下一次仍由同一个
`main_agent` 读取；Runtime 不把 Skill 转成隐藏 developer message，也不另建
子分支。`skill.loaded` 事件只记录 `skill_id`、版本、内容哈希和关联
`tool_call_id`，不复制正文。

同一上下文已包含相同 Skill 与版本时不重复加载。多个独立领域按依赖顺序逐个
加载，每次都先让完整结果进入上下文，再进行下一次模型决策。Prompt 不再包含
“ToolResult 不可修改指令”的通用规则；通过 loader 返回的完整 Skill 是当前
Agent 明确要求遵循的版本化工作流。

## 渐进 Tool Search

`load_service_skill` 是唯一常驻 FunctionTool。业务 Tool 不在初始平铺列表中，
而是按 namespace 声明为 `defer_loading=true`，由 server-side `tool_search`
按当前任务加载最小集合：

| Namespace | Tool |
| --- | --- |
| `profile` | `profile_read`, `profile_update` |
| `planning` | `plan_read`, `plan_mutate`, `schedule_timeline_read`, `schedule_timeline_mutate` |
| `diary` | `diary_read`, `diary_mutate` |
| `attachments` | `conversation_history_image_read` |
| `prenatal` | `pregnancy_intake_manage`, `hospital_bag_manage`, `hospital_bag_cart_mutate` |
| `lactation` | `milk_analysis_manage`, `ibclc_consult_card_create` |
| `device` | `devices_guidance_manage`, `pump_models_read`, `support_ticket_draft_create` |

Skill 负责专业判断与工作流，业务 Tool 负责 owner-scoped 权威事实和副作用。
所有业务执行仍只经过 Runtime `ToolExecutor`；SDK callback 不包含业务逻辑。

## 执行与恢复

1. `AgentLoop` 从追加式账本恢复当前 Run 输入，并先补执行已经落账但尚无
   output 的普通 Tool call。
2. `OpenAIAgentsExecutionEngine` 创建且只创建一个 SDK Agent。
3. Skill loader 常驻；`ToolSearchTool` 与七个 deferred namespace 提供业务
   Tool 的渐进发现。
4. SDK `on_llm_start` 记录不含 Prompt/用户正文的执行清单；`on_llm_end` 先
   校验本轮调用，再按实际顺序追加 reasoning/function call。
5. Tool output 由 `ToolExecutor` 持久化为配对的 `function_call_output`，然后
   回到同一 Agent 的下一次模型决策。
6. 首个待确认 Action 立即把 Run 置为 `waiting_for_confirmation`；确认后从
   账本重建完整配对上下文，不恢复进程内 SDK 状态。
7. 最终文本始终由 `main_agent` 生成，并由 `AgentLoop` 写成 canonical
   assistant message。

单次模型响应不允许并行 Tool call。Prompt 还要求不在同一响应里同时调用
`load_service_skill` 与业务 Tool，确保完整 Skill 先成为最新上下文的一部分。

每次 SDK 模型调用都受 `AGENT_MODEL_TIMEOUT_SECONDS` 的完整墙钟时限约束，
覆盖连接、SDK 内部重试和完整流式消费；持续返回增量不会重置时限。Tool 使用
各自独立的超时策略。

## 发布与验证

发布门禁至少验证：

- Runtime catalog 中只有 `main_agent`，不存在 delegation/handoff/agent-as-tool。
- 稳定 Prompt 只有 Skill manifest，不含完整 Skill 正文。
- loader output 原样出现在同一 Agent 的下一次输入中，且没有隐藏注入。
- `tool_search` 存在，loader eager，全部业务 Tool 位于 deferred namespace。
- 行为 eval 断言精确 Skill 加载集合、Tool、Action 和最终 `main_agent` 回复。
- Tool call/output、Action 暂停恢复、崩溃恢复、租约和 Context Pipeline 的耐久
  语义保持不变。

Runtime 继续独立部署、独立使用 PostgreSQL/Redis，并且不导入 Product Backend
业务实现或直接访问 Product 业务表。
