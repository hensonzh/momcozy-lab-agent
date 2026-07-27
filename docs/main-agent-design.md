# Momcozy 多智能体架构

## 架构

一个主智能体和产前、泌乳、设备三个专业子智能体，共享 Agent Runtime、追加式上下文账本和 Tool Executor，通过内部 API 调用 Product Backend。

## 回复

- 通用问题：主智能体直接回复。
- 单一专业意图：主智能体路由，专业智能体直接回复。
- 多专业意图：主智能体并行或按依赖顺序调用专业智能体并汇总。
- 全局账本按真实发生顺序追加；并行调用时，每个智能体只续接共同前缀和自身调用结果，主智能体只接收 `delegate_to_specialists` 的最终结果。

## Tool

- 模型可见名称统一为 canonical `snake_case`，不保留旧名或兼容别名。
- `read` 只读，`write` 写业务资源，`manage` 管理多阶段或混合流程。
- create、update、delete 合并到同一 `write` Tool，通过 `operation` 区分。
- 每个智能体只接收静态 allowlist；Tool 调用及原始标准 `ToolResult` 按 Agent Loop 顺序追加到上下文。

## 主智能体（9）

`profile_read`、`profile_write`、`plans_current_read`、`plans_calendar_read`、`plans_task_write`、`plans_plan_write`、`pregnancy_diary_read`、`pregnancy_diary_write`、`conversation_history_image_read`

## 产前智能体（3）

`pregnancy_plan_manage`、`hospital_bag_manage`、`hospital_bag_cart_write`

## 泌乳智能体（8）

`profile_read`、`profile_write`、`lactation_timeline_read`、`lactation_timeline_write`、`milk_analysis_manage`、`plans_milk_plan_write`、`notifications_milk_reminder_write`、`ibclc_consult_card_write`

## 设备智能体（3）

`devices_guidance_manage`、`pump_models_read`、`support_ticket_write`

## 约束

Runtime 独立仓库、部署、数据库和 CI/CD，不导入 Product Backend 业务模块或访问 Product 业务表。当前测试数据可清理，不兼容旧架构、旧 Tool 名称、旧契约或旧数据。
