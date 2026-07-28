# Momcozy 多智能体架构

## 架构

一个主智能体和产前、泌乳、设备三个专业子智能体，共享 Agent Runtime、追加式上下文账本和 Tool Executor，通过内部 API 调用 Product Backend。

当前 Runtime Pattern 为 `proprietary_runtime`，版本为
`momcozy-agent-v4`。它采用混合边界：

- 自研 durable `AgentLoop` 负责 Run 租约、追加式账本、Action
  确认、崩溃恢复、Context Pipeline、最终消息和业务审计。
- `OpenAIAgentsExecutionEngine` 使用 OpenAI Agents SDK 的
  `Runner`、`Agent`、`FunctionTool`、生命周期 Hook 和流事件，负责单次
  执行中的模型—工具循环。
- Runtime 账本仍是唯一恢复事实源，不启用 SDK `Session`，也不使用
  provider response ID 作为恢复状态。

原手写 Responses API 模型循环、`ModelProvider`/`ModelTurn` 协议和
Responses 适配器已移除。SDK 是内层执行引擎，并未接管 durable Runtime
边界，因此 Runtime Pattern 仍命名为 `proprietary_runtime`。

## 代码组织

- `app/agents/<name>_agent/` 是智能体纵向切片，主智能体目录为 `app/agents/main_agent/`。每个智能体拥有自己的 `definition.py`、`toolset.py`、`system_prompt.md`；专业智能体还拥有版本化的 `skills/<version>/SKILL.md`。
- `app/agents/shared/` 保存所有智能体共享的稳定安全与工具规则。`app/agents/main_agent/` 还拥有三个专业智能体 Tool 的契约；项目中不存在独立的语义分类模型。
- `app/agents/registry.py` 是智能体定义的唯一聚合入口。`app/bootstrap/` 将定义与委派 Tool 组装成 `AgentCatalog` 后注入 Runtime；Runtime 不导入 `app/agents`，也不感知 Prompt 文件位置。
- `app/capabilities/<capability>/` 保存可复用 Tool contract、handler、reference、registry，以及该领域的 Action applicator 和策略声明。智能体只通过 `toolset.py` 声明 Tool 白名单，不复制 Tool 实现。
- `app/agent_runtime/` 只负责运行、持久账本、Agents SDK 执行适配、通用 Tool/Action 执行协议、权限、事件和恢复，不拥有专业业务 Prompt、具体业务 Action 或应用装配。`providers/` 只保留 Context token 计数和压缩所需的 OpenAI 辅助组件，不再承载 Agent 模型循环。
- `app/bootstrap/` 是 Agent、Capability、基础设施适配器与 Runtime 的唯一装配根；`app/api/agent_runtime/` 是 HTTP 交付层。

模型指令按“共享基础规则 + 智能体角色 Prompt + 版本化专业 Skill”组合。共享 Tool 可以同时被多个智能体引用，但 contract 和 handler 始终只有一份实现。

运行时智能体标识与包目录统一为 `main_agent`、`prenatal_agent`、`lactation_agent`、`device_agent`。委派、事件和恢复流程只接受这些名称。

## 回复

- 通用问题：主智能体直接回复。
- 每轮请求的第一条模型调用固定为 `main_agent`。只有需要专业服务时，它才调用对应的 `prenatal_agent`、`lactation_agent` 或 `device_agent` Tool，并通过 `request` 参数传递完整请求。
- Runtime 将专业智能体视为 agent-as-tool：Tool call/output 和专业分支都写入追加式账本，但 Tool 执行完成后不再调用 `main_agent`，由专业智能体直接形成最终用户回复。
- 确有多个专业目标时，`main_agent` 在同一 Tool 调用阶段按依赖顺序调用不同的专业智能体；前序结果作为不可信数据交给后序智能体，由最后执行的专业智能体形成整合回复。
- 委派输入保留 `main_agent` 在该次委派前已经完成的业务 Tool call/output，避免专业智能体丢失主智能体刚读取到的因果上下文。
- 首个待确认 Action 立即暂停，恢复后沿原委派调用和专业智能体分支继续执行。
- 全局账本按真实发生顺序追加，前序智能体结果作为不可信数据提供给后序智能体。

这里使用 SDK agent-as-tool，而不是原生 handoff。原生 handoff 在第一次
切换后会结束 `main_agent` 的该段执行，无法表达当前“一轮内按依赖顺序
调用多个不同专业智能体、由最后一个直接回复”的产品契约。SDK
`tool_use_behavior` 只保留一层很薄的协调逻辑：所有调用都是专业智能体
Tool 时直接采用最后一个结果；业务 Tool 则继续标准 SDK 模型循环。

模型侧允许 `main_agent` 在一个响应中给出多个 Tool call，但 SDK 本地
执行并发上限固定为 `1`，因此实际调用、持久化和 Action 暂停顺序始终
确定；专业智能体自身关闭并行 Tool call。委派与业务 Tool 混用、重复
调用同一专业智能体会在任何 Tool 执行前被拒绝。

## Tool

- 模型可见名称统一为 canonical `snake_case`，不保留旧名或兼容别名。
- `read` 只读，`mutate` 通过 `operation` 统一 create、update、delete，`manage` 管理多阶段流程。
- 每个 Tool 只有一份 canonical output，经过输出 schema 校验后完整追加给模型；事件仅记录 `output_summary`。
- `ToolContract` 的 `name`、`description`、`input_schema` 被直接适配为 SDK `FunctionTool`；业务执行仍只经过 Runtime `ToolExecutor`，SDK Tool callback 不包含业务逻辑。
- 小结果内联保存，大结果写入 Runtime 独立对象存储并在账本保存 `output_ref`。
- 删除日记或整份计划必须生成待确认 Action，由用户确认后执行；模型不接收或生成确认凭据。

## 调用与恢复链路

1. `AgentLoop` 从账本恢复输入并先补执行已落账但尚无 output 的 Tool
   call。
2. `OpenAIAgentsExecutionEngine` 建立本轮 SDK Agent 图，并由 `Runner`
   执行模型—工具循环。
3. SDK `on_llm_start` 在调用前记录内容安全的执行清单；
   `on_llm_end` 先校验整组 Tool call，再把 reasoning/function call
   追加到账本。
4. 业务 `FunctionTool` 回调进入 Runtime `ToolExecutor`；专业
   `FunctionTool` 启动对应 SDK Agent。
5. 专业结果逐个追加到账本；整批专业 Tool output、最终专业智能体归属和
   `agent.delegation.completed` 在同一提交边界完成。进程中断时按原始
   调用顺序恢复，只重新运行尚无专业结果的分支。
6. 需要确认的 Tool 已持久化 Action 和 output 后，将 Run 置为
   `waiting_for_confirmation` 并中止当前 SDK 执行。确认后重新从账本
   构造完整配对上下文，不恢复进程内 SDK 状态。
7. 最终文本只由 `AgentLoop` 写成 canonical assistant message，SDK
   最终 message 不重复写入上下文。

每次 SDK 模型调用都受 `AGENT_MODEL_TIMEOUT_SECONDS` 的墙钟时限约束。
时限从模型请求开始，覆盖连接、OpenAI SDK 内部重试以及完整流式消费；
持续返回增量不会重置时限。该约束同样应用于 `main_agent` 和所有专业
智能体，超时统一映射为可重试的 `model_provider_timeout`（HTTP 504）。
`AsyncOpenAI` 的 transport timeout 继续作为单次网络读写的第二层保护，
不代替 Runtime 的完整调用时限。Tool 使用各自的独立超时策略。

## 主智能体（3 个专业智能体 Tool + 9 个业务 Tool）

`prenatal_agent`、`lactation_agent`、`device_agent`、`profile_read`、`profile_update`、`plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate`、`diary_read`、`diary_mutate`、`conversation_history_image_read`

## 产前智能体（7）

`plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate`、`pregnancy_intake_manage`、`hospital_bag_manage`、`hospital_bag_cart_mutate`

## 泌乳智能体（8）

`profile_read`、`profile_update`、`plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate`、`milk_analysis_manage`、`ibclc_consult_card_create`

## 设备智能体（3）

`devices_guidance_manage`、`pump_models_read`、`support_ticket_draft_create`

## 约束

Runtime 独立仓库、部署、数据库和 CI/CD，不导入 Product Backend 业务模块或访问 Product 业务表。当前测试数据可清理，不兼容旧架构、旧 Tool 名称、旧契约或旧数据。
