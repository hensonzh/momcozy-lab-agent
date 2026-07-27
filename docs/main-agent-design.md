# Momcozy 多智能体架构

## 架构

一个主智能体和产前、泌乳、设备三个专业子智能体，共享 Agent Runtime、追加式上下文账本和 Tool Executor，通过内部 API 调用 Product Backend。

当前 Runtime Pattern 为 `legacy_adapter`：由自研 durable `AgentLoop` 管理恢复、Tool/Action 与账本，并通过 OpenAI Responses API 适配器调用模型；它不是 OpenAI Agents SDK runtime。

## 代码组织

- `app/agents/<name>_agent/` 是智能体纵向切片，主智能体目录为 `app/agents/main_agent/`。每个智能体拥有自己的 `definition.py`、`toolset.py`、`system_prompt.md`；专业智能体还拥有版本化的 `skills/<version>/SKILL.md`。
- `app/agents/shared/` 保存所有智能体共享的稳定安全与工具规则。`app/agents/main_agent/` 还拥有三个专业智能体 Tool 的契约；项目中不存在独立的语义分类模型。
- `app/agents/registry.py` 是智能体定义的唯一聚合入口。`app/bootstrap/` 将定义与委派 Tool 组装成 `AgentCatalog` 后注入 Runtime；Runtime 不导入 `app/agents`，也不感知 Prompt 文件位置。
- `app/capabilities/<capability>/` 保存可复用 Tool contract、handler、reference、registry，以及该领域的 Action applicator 和策略声明。智能体只通过 `toolset.py` 声明 Tool 白名单，不复制 Tool 实现。
- `app/agent_runtime/` 只负责运行、持久账本、通用 Tool/Action 执行协议、权限、事件、恢复和 Provider 适配，不拥有专业业务 Prompt、具体业务 Action 或应用装配。
- `app/bootstrap/` 是 Agent、Capability、基础设施适配器与 Runtime 的唯一装配根；`app/api/agent_runtime/` 是 HTTP 交付层。

模型指令按“共享基础规则 + 智能体角色 Prompt + 版本化专业 Skill”组合。共享 Tool 可以同时被多个智能体引用，但 contract 和 handler 始终只有一份实现。

运行时智能体标识与包目录统一为 `main_agent`、`prenatal_agent`、`lactation_agent`、`device_agent`。委派、事件和恢复流程只接受这些名称。

## 回复

- 通用问题：主智能体直接回复。
- 每轮请求的第一条模型调用固定为 `main_agent`。只有需要专业服务时，它才调用对应的 `prenatal_agent`、`lactation_agent` 或 `device_agent` Tool，并通过 `request` 参数传递完整请求。
- Runtime 将专业智能体视为 agent-as-tool：Tool call/output 和专业分支都写入追加式账本，但 Tool 执行完成后不再调用 `main_agent`，由专业智能体直接形成最终用户回复。
- 确有多个专业目标时，`main_agent` 在同一 Tool 调用阶段按依赖顺序调用不同的专业智能体；前序结果作为不可信数据交给后序智能体，由最后执行的专业智能体形成整合回复。
- 首个待确认 Action 立即暂停，恢复后沿原委派调用和专业智能体分支继续执行。
- 全局账本按真实发生顺序追加，前序智能体结果作为不可信数据提供给后序智能体。

## Tool

- 模型可见名称统一为 canonical `snake_case`，不保留旧名或兼容别名。
- `read` 只读，`mutate` 通过 `operation` 统一 create、update、delete，`manage` 管理多阶段流程。
- 每个 Tool 只有一份 canonical output，经过输出 schema 校验后完整追加给模型；事件仅记录 `output_summary`。
- 小结果内联保存，大结果写入 Runtime 独立对象存储并在账本保存 `output_ref`。
- 删除日记或整份计划必须生成待确认 Action，由用户确认后执行；模型不接收或生成确认凭据。

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
