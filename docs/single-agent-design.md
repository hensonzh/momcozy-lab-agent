# Momcozy 单智能体架构

## 架构目标

CozyMate 是应用中唯一的 Agent，运行时标识为 `cozymate`。它在同一个
模型—工具 loop 中理解请求、渐进加载服务 Skill、搜索业务 Tool、执行动作并
形成最终回复。系统没有 Agent registry、子 Agent、委派、handoff 或
agent-as-tool 路径。

当前 Runtime Pattern 仍为 `proprietary_runtime`，版本为
`momcozy-agent-v1`：

- 自研 durable `AgentLoop` 继续负责 Run 租约、追加式账本、Action 确认、
  崩溃恢复、Context Pipeline、最终消息和业务审计。
- `ResponsesAgentsExecutionEngine` 使用 OpenAI Agents SDK 的单个 `Agent`、
  `Runner`、`FunctionTool`、`ToolSearchTool`、namespace 和生命周期 Hook，
  负责一次执行中的模型—工具 loop。
- Runtime 账本仍是唯一恢复事实源；不启用 SDK `Session`，也不使用 provider
  response ID 作为恢复状态。

这次迁移只改变 Agent 拓扑和 Skill/Tool 的加载方式，不采用参考项目的上下文
构造或运行时边界。

## 代码组织

- `app/agent/definition.py` 直接定义唯一的 `AGENT`，只包含名称和指令。
- `app/agent/system_prompt.md` 是唯一系统提示词；不存在 shared prompt 层或
  多份 Agent prompt 的组合逻辑。
- `app/agent/skills/<skill_id>/<version>/SKILL.md` 保存完整、版本化的
  `lactation`、`device` 工作流。
- `app/agent/skill_registry.py` 校验 Skill frontmatter、版本和内容，并提供
  `load_service_skill` Tool contract/handler。
- `app/agent/tool_catalog.py` 定义全局 eager Tool 与 deferred namespace；工具
  名单不属于 `AGENT`，也不按 Agent 注册。
- `app/capabilities/<capability>/` 保存 canonical Tool contract、handler、
  reference、Action applicator 和策略；迁移不复制业务实现。
- `app/bootstrap/` 把 `AGENT` 与全局 `ToolCatalog` 组成一个
  `RuntimeDefinition`，再连同 Capability 和基础设施适配器注入 Runtime。
  `app/agent_runtime/` 不导入应用 Agent 定义。

旧的复数 `app/agents/` 包、Agent registry、共享提示词、逐 Agent 工具名单、
委派分支和委派恢复事件均已移除。

## 单一 Prompt 与渐进 Skill

系统提示词只包含 CozyMate 的稳定身份、安全规则、单 Agent 工作方式和轻量
Skill manifest（Skill ID、版本、简介）。完整 Skill 正文不会预先拼入
system/developer Prompt。

当请求需要专业工作流时，模型先单独调用：

```text
load_service_skill({"skill_id":"lactation|device"})
```

loader 返回普通、可持久化的 `function_call_output`：

```json
{
  "schema_version": "momcozy.service_skill.v1",
  "skill_id": "lactation",
  "version": "v1",
  "description": "...",
  "content": "完整原始 SKILL.md（含 frontmatter 与正文）",
  "content_sha256": "..."
}
```

该 ToolResult 按实际发生顺序直接追加到当前 Run 的最新上下文。下一次模型调用
从同一上下文读取完整 Skill；Runtime 不把 Skill 改写成隐藏 developer message，
不复制到其他提示词，也不创建新分支。`skill.loaded` 事件只记录 `skill_id`、
版本、内容哈希和关联 `tool_call_id`，不复制正文。

`load_service_skill` 返回的 `ToolResult.model_output` 就是完整 canonical output，
且 contract 将 `model_output_max_bytes` 设为 `None`。所以 Skill 正文作为原始
ToolResult 完整进入模型上下文，不做摘要、裁剪或引用替换；如果完整请求超出
预算，Runtime 会先尝试 durable compaction，仍放不下时显式失败，而不是悄悄
修改 Skill。

同一上下文已包含相同 Skill 与版本时不重复加载。多个独立领域按依赖顺序逐个
加载，每次都先让完整结果进入上下文，再进行下一次模型决策。系统提示词不再
包含“ToolResult 不可修改指令”的通用规则；loader 返回的完整 Skill 是
CozyMate 明确要求加载和遵循的版本化工作流。

## 全局渐进 Tool Catalog

Tool/Action 的完整当前方案和后续维护入口统一为 [tools.md](tools.md)；本节只描述
单 Agent 下的渐进加载决策。

`load_service_skill` 是唯一常驻 FunctionTool。业务 Tool 不在初始平铺列表中，
而是按全局 namespace 声明为 `defer_loading=true`，由 server-side
`tool_search` 按当前任务加载最小集合：

| Namespace | Tool |
| --- | --- |
| `profile` | `profile_read`, `profile_update` |
| `planning` | `plan_read`, `plan_mutate`, `schedule_timeline_read`, `schedule_timeline_mutate` |
| `attachments` | `conversation_history_image_read` |
| `lactation` | `get_lactation_summary`, `get_lactation_records`, `get_feeding_summary`, `get_feeding_records`, `get_growth_summary`, `get_growth_records`, `ibclc_consult_card_create` |
| `device` | `devices_guidance_manage`, `pump_models_read`, `support_ticket_draft_create` |

Skill 负责专业判断与工作流，业务 Tool 负责 owner-scoped 权威事实和副作用。
所有业务执行仍只经过 Runtime `ToolExecutor`；SDK callback 不包含业务逻辑。

泌乳分析不再维护 `milk_analysis_manage` 专属状态机。CozyMate 根据完整
`lactation` Skill 依次读取宝宝资料、生长、喂养和妈妈侧吸奶事实，证据不足时
再读取明细或向用户追问。专用查询 Tool 复用 Product Backend 的时间线读取契约，
不导入旧项目的 Records Service、数据库模型或 Agent Runtime。当前 Product
Backend 没有尿布记录和版本化生长参考契约，因此 Runtime 不注册不可执行的尿布
工具，也不在本地伪造 WHO 派生结果。

## 执行与恢复

1. `AgentLoop` 从追加式账本恢复当前 Run 输入，并先补执行已经落账但尚无
   output 的普通 Tool call。
2. `ResponsesAgentsExecutionEngine` 从 `RuntimeDefinition.agent` 创建且只创建一个
   SDK Agent；执行接口不接受起始 Agent 名称。
3. Skill loader 常驻；`ToolSearchTool` 与五个 deferred namespace 提供业务
   Tool 的渐进发现。
4. SDK `on_llm_start` 记录不含 Prompt/用户正文的执行清单；`on_llm_end` 先
   校验本轮调用，再按实际顺序追加 reasoning/function call。
5. Tool output 由 `ToolExecutor` 持久化为配对的 `function_call_output`，然后
   回到 CozyMate 的下一次模型决策。
6. 首个待确认 Action 立即把 Run 置为 `waiting_for_confirmation`；确认后从
   账本重建完整配对上下文，不恢复进程内 SDK 状态。
7. 最终文本始终由 CozyMate 生成，并由 `AgentLoop` 写成 canonical assistant
   message。

Runtime 在唯一 Agent 的 `ModelSettings` 上统一设置
`parallel_tool_calls=false`，所以每个模型响应最多只能包含一个 Function Tool
call。这个数量约束交给 provider contract 保证，Runtime 不再重复实现多调用
拒绝分支；Skill loader 也不再拥有一套局部“独占轮次”元数据。

原因不是 Tool 的执行速度，而是模型决策的时序：同一模型响应里的所有 Tool
call 都是在任何 ToolResult 返回之前一起规划出来的。即使 Runtime 按顺序执行，
同响应里的后一个业务 Tool call 也不可能读取刚返回的完整 Skill，并据此重新
选择工具或参数。只有把 Skill ToolResult 写回上下文并发起下一轮模型调用，完整
Skill 才真正参与后续决策。

Tool handler 在 `ToolResult` 中明确给出两份有清晰用途的结果：
`canonical_output` 始终完整保存到 Tool output 存储，`model_output` 直接写回模型
账本。两者默认相同；可能产生大结果的 handler 必须提前定义有界且保留决策所需
信息的 `model_output`。Runtime 不再按字段名猜测摘要、自动裁剪或生成引用投影，
只执行 contract 的字节上限；超限会显式失败。Skill loader 的上限为 `None`，
因此完整 Skill 直接写回当前最新上下文。

在首个模型调用前，Runtime 先执行高精度确定性安全门。命中明确的医疗紧急、
胎动显著减少、迫近的自伤或婴儿伤害风险时，它记录版本化
`safety.decision` 事件，返回固定的升级处置文本，并且不调用模型或业务 Tool；
未命中时才进入正常 loop。这个窄门用于保证关键红线，模型中的更广泛安全规则
仍负责语境化回答。

每次 SDK 模型调用都受 `AGENT_MODEL_TIMEOUT_SECONDS` 的完整墙钟时限约束，
覆盖连接、SDK 内部重试和完整流式消费；持续返回增量不会重置时限。Tool 使用
各自独立的超时策略。

## 发布与验证

发布门禁至少验证：

- 应用只存在 `app/agent/` 和一个产品命名的 `AGENT`，没有 Agent registry、
  shared prompt、逐 Agent 工具名单或委派入口。
- 稳定 Prompt 只有 Skill manifest，不含完整 Skill 正文。
- loader output 原样出现在同一 Agent 的下一次输入中，且没有隐藏注入。
- 每个模型响应最多一个 Function Tool call；Skill 加载后的下一轮预算检查覆盖
  完整 Skill、稳定 Prompt、当前输入和实际 Tool schema。
- `tool_search` 存在，loader eager，全部业务 Tool 位于全局 deferred namespace。
- 普通大 ToolResult 由 handler 显式提供有界 `model_output`，canonical output
  仍可完整审计；确定性安全命中时不存在模型或业务 Tool 调用。
- 行为 eval 断言精确 Skill 加载集合、Tool、Action 和最终 `cozymate` 回复。
- Tool call/output、Action 暂停恢复、崩溃恢复、租约和 Context Pipeline 的耐久
  语义保持不变。

Runtime 继续独立部署、独立使用 PostgreSQL/Redis，并且不导入 Product Backend
业务实现或直接访问 Product 业务表。
