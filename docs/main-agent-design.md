# Momcozy 多智能体架构

## 架构

一个主智能体和产前、泌乳、设备三个专业子智能体，共享 Agent Runtime、追加式上下文账本和 Tool Executor，通过内部 API 调用 Product Backend。

## 回复

- 通用问题：主智能体直接回复。
- Runtime 先用无 Tool 的结构化路由器选择智能体。
- 单一专业意图：专业智能体直接回复。
- 多意图：按依赖顺序串行执行；首个待确认 Action 立即暂停；全部完成后由无 Tool 的主智能体汇总。
- 全局账本按真实发生顺序追加，前序智能体结果作为不可信数据提供给后序智能体。

## Tool

- 模型可见名称统一为 canonical `snake_case`，不保留旧名或兼容别名。
- `read` 只读，`mutate` 通过 `operation` 统一 create、update、delete，`manage` 管理多阶段流程。
- 每个 Tool 只有一份 canonical output，经过输出 schema 校验后完整追加给模型；事件仅记录 `output_summary`。
- 小结果内联保存，大结果写入 Runtime 独立对象存储并在账本保存 `output_ref`。
- 删除日记或整份计划必须生成待确认 Action，由用户确认后执行；模型不接收或生成确认凭据。

## 主智能体（9）

`profile_read`、`profile_update`、`plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate`、`diary_read`、`diary_mutate`、`conversation_history_image_read`

## 产前智能体（7）

`plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate`、`pregnancy_intake_manage`、`hospital_bag_manage`、`hospital_bag_cart_mutate`

## 泌乳智能体（8）

`profile_read`、`profile_update`、`plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate`、`milk_analysis_manage`、`ibclc_consult_card_create`

## 设备智能体（3）

`devices_guidance_manage`、`pump_models_read`、`support_ticket_draft_create`

## 约束

Runtime 独立仓库、部署、数据库和 CI/CD，不导入 Product Backend 业务模块或访问 Product 业务表。当前测试数据可清理，不兼容旧架构、旧 Tool 名称、旧契约或旧数据。
