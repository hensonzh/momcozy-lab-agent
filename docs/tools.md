# Tool/Action 方案

<!-- tooling-document: canonical -->
<!-- runtime-baseline: momcozy-agent-v1 -->
<!-- runtime-contract-catalog-sha256: 3f137c74d10c948b39ae6fff44c3f0f651239fc23699bf9fea73e80e6c9f32e7 -->
<!-- tool-count: 18 -->
<!-- action-count: 18 -->

本文档是 Momcozy Agent 工具方案唯一的人类可读维护入口，描述当前已经实现的
Tool 发现、契约、执行、权限、输出、Action 副作用、持久化、测试和演进规则。
以后任何影响工具方案的修改都必须在同一个变更中更新本文档。

代码和生成物仍是可执行事实来源：

- Tool 契约、handler、模型输入 Schema、Action policy/applicator 由各
  `app/capabilities/<capability>/` 领域包维护。
- 每个领域通过 `module.py` 暴露一个显式 `CapabilityModule`；
  `app/capability_catalog.py` 的 `CAPABILITY_MODULES` 是唯一组合清单，并由它生成
  namespace 与 eager/deferred Tool 目录。
- 运行时组合与一致性校验位于 `app/bootstrap/agent_runtime.py`。
- 与产品领域无关的 Tool/Action 执行内核位于 `app/agent_runtime/`，不依赖
  `app/agent`、`app/bootstrap` 或 `app/capabilities`。
- 跨模块版本统一位于 `app/agent_runtime/runtime_metadata.py`。
- 完整 JSON Schema、策略与内容哈希快照位于
  `docs/runtime-contract-catalog.generated.json`。

本文档顶部记录生成目录的 SHA-256。测试会同时核对该哈希、Tool/Action 数量和
全部名称，因此契约目录发生变化但本文档没有同步时，CI 会失败。

## 1. 当前架构结论

当前形态是一个带受保护副作用边界的单智能体 Tool Agent：

- 对外只有一个 `cozymate` Agent，不使用 handoff 或多 Agent 路由。
- OpenAI Agents SDK 负责单次模型—工具循环；自研 durable `AgentLoop` 负责
  Run 租约、账本、恢复、Action 暂停与继续。
- Tool 负责读取事实、推进 Runtime 内部流程或提出 Action；模型不能直接执行
  Product Backend 的受保护写操作。
- 所有业务 Tool 最终都经过统一 `ToolExecutor`，SDK callback 不包含业务逻辑。
- 当前 Tool 和 Action contract 都使用 v1。

```text
Cozymate
  ├─ eager FunctionTool: load_service_skill
  └─ server ToolSearchTool
       └─ 5 个 deferred namespace / 17 个业务 FunctionTool
            ↓
       OpenAI Agents SDK callback
            ↓
       durable AgentLoop execution port
            ↓
       ToolExecutor
         ├─ read handler → Product Backend / Runtime repository
         ├─ runtime_internal handler → Runtime workflow/artifact
         └─ action_proposal handler → RuntimeActionService
                                      ├─ 同步 apply
                                      └─ 暂停 Run 等待用户确认
```

`ToolSearchTool` 是 SDK/Provider 的服务端工具搜索能力，不是 Momcozy 业务 Tool，
不计入 18 个 Tool，也不经过 `ToolExecutor`；它是否启用会写入执行清单。

### 1.1 代码组织与依赖方向

当前工具实现采用“独立 Runtime 内核 + 领域能力模块 + 显式 composition root”：

```text
app/bootstrap/agent_runtime.py
  └─ app/capability_catalog.py                  # 显式 CAPABILITY_MODULES
       ├─ app/capability_module.py              # 通用组合协议
       └─ app/capabilities/<domain>/module.py
            ├─ contract / handler / model_schemas / actions
            └─ app/agent_runtime/               # 领域无关执行内核
```

每个 `CapabilityModule` 必须同时声明自己的 Tool registry、handler factory、
namespace/eager 属性，以及可选的 Action policy 与 applicator factory。模块构造时
先校验 Tool—Action 绑定；bootstrap 再统一校验 registry、handler、目录、policy、
applicator 和权限覆盖，任一侧缺失都 fail closed。

不会通过文件扫描、import 副作用或 decorator 自动注册 Tool。新增能力必须显式加入
`CAPABILITY_MODULES`，使代码审查能直接看出运行时暴露面。多个能力可以共享同一个
模型 namespace，但仍分别拥有自己的契约和依赖。当前 `plans` 只负责计划资源，
`timeline` 独立负责日程及实际喂养/吸奶/生长记录，避免聚合 Tool 继续挤在 plans 包中。

## 2. ToolContract v1

每个 Tool 都必须注册一个 `agent.tool_contract.v1` 契约。核心字段如下：

| 字段 | 当前含义 |
| --- | --- |
| `name` / `domain` / `description` | 稳定工具身份、业务域和给模型看的用途说明 |
| `operation` | `read`、`action_proposal` 或 `runtime_internal` |
| `required_permissions` | 非空的 `domain:operation` 权限集合 |
| `owner_scope` | 当前只支持 `actor`，不能由模型选择其他用户 |
| `input_schema` | 暴露给模型的 JSON Schema Draft 2020-12 输入契约 |
| `internal_input_schema` | 合并 Runtime 可信参数后的内部输入契约，不暴露给模型 |
| `output_schema` | handler `canonical_output` 的权威输出契约 |
| `action_types` | `action_proposal` Tool 可提出的 Action 类型 |
| `safe_arg_fields` / `safe_output_fields` | 允许进入事件、日志和指标摘要的字段白名单 |
| `retry_policy` | 重试语义声明：`safe_read`、`idempotent_write` 或 `none` |
| `model_output_max_bytes` | 写回模型上下文的输出上限，默认 16 KiB |
| `timeout_seconds` | 单次 handler 墙钟超时，当前工具为 5–20 秒 |

三种 operation 的组合约束是固定的：

| Operation | Action 绑定 | Retry policy | 允许的副作用 |
| --- | --- | --- | --- |
| `read` | 必须为空 | `safe_read` | 只读权威事实 |
| `action_proposal` | 必须非空 | `idempotent_write` | 只创建/复用 Action，由 applicator 执行写入 |
| `runtime_internal` | 必须为空 | `none` | 仅 Runtime 自有流程、草稿、卡片、artifact 或 Skill 加载 |

`retry_policy` 目前是契约分类和审查约束，不代表 `ToolExecutor` 会自动重试。
Action 的失败复用只发生在相同幂等键且错误码被 policy 标记为可重试时。

## 3. Tool 发现与加载

### 3.1 Eager Tool 与 Service Skill

初始模型请求唯一常驻的 FunctionTool 是 `load_service_skill`。它需要
`agent:run`，可加载两个版本化 Service Skill：`lactation`、`device`，当前均为
v1。

Skill 的完整 `SKILL.md` 作为普通 ToolResult 写回上下文，并记录内容 SHA-256
和 `skill.loaded` 事件。该 Tool 的 `model_output_max_bytes=None`，不单独截断
Skill；完整请求仍必须通过 Context Pipeline 的 token budget gate。

### 3.2 Deferred namespace

除 loader 外的 17 个 Tool 全部延迟加载。Runtime 先按 Run 权限过滤 Tool，
再向模型提供 `ToolSearchTool` 和非空 namespace：

| Namespace | 数量 | Tool |
| --- | ---: | --- |
| `profile` | 2 | `profile_read`、`profile_update` |
| `planning` | 4 | `plan_read`、`plan_mutate`、`schedule_timeline_read`、`schedule_timeline_mutate` |
| `attachments` | 1 | `conversation_history_image_read` |
| `lactation` | 7 | `get_lactation_summary`、`get_lactation_records`、`get_feeding_summary`、`get_feeding_records`、`get_growth_summary`、`get_growth_records`、`ibclc_consult_card_create` |
| `device` | 3 | `devices_guidance_manage`、`pump_models_read`、`support_ticket_draft_create` |

模型设置固定为 `parallel_tool_calls=false`，SDK 的
`max_function_tool_concurrency=1`。因此每轮模型响应最多执行一个 FunctionTool，
Skill 或业务 Tool 的结果进入上下文后，模型必须在下一轮重新决策。

## 4. 当前 Tool 清单

当前 18 个 Tool 包含 11 个 `read`、3 个 `action_proposal` 和 4 个
`runtime_internal`。下表是人类可读摘要；输入/输出完整 JSON Schema 以生成
目录为准。

| Namespace | Tool | Operation | 权限 | 用途 | 绑定 Action |
| --- | --- | --- | --- | --- | --- |
| eager | `load_service_skill` | `runtime_internal` | `agent:run` | 加载完整的版本化 Service Skill | — |
| attachments | `conversation_history_image_read` | `read` | `files:read` | 将当前对话中已向用户展示过的历史图片重新载入模型上下文 | — |
| device | `devices_guidance_manage` | `runtime_internal` | `device:read` | 读取官方设备指导并推进开箱/排障流程 | — |
| device | `pump_models_read` | `read` | `device:read` | 查询官方吸奶器型号与产品事实 | — |
| device | `support_ticket_draft_create` | `runtime_internal` | `support:write` | 创建可编辑售后工单草稿，不提交正式工单 | — |
| lactation | `get_lactation_summary` | `read` | `records:read` | 汇总吸奶次数、实测产出和逐日覆盖 | — |
| lactation | `get_lactation_records` | `read` | `records:read` | 查询逐次吸奶和亲喂记录 | — |
| lactation | `get_feeding_summary` | `read` | `records:read` | 汇总宝宝喂养次数、实测体积和方式 | — |
| lactation | `get_feeding_records` | `read` | `records:read` | 查询逐次喂养事实 | — |
| lactation | `get_growth_summary` | `read` | `records:read` | 读取最近两次原始生长测量及变化 | — |
| lactation | `get_growth_records` | `read` | `records:read` | 查询原始生长测量记录 | — |
| lactation | `ibclc_consult_card_create` | `runtime_internal` | `support:write` | 创建 IBCLC 咨询入口卡片 | — |
| planning | `plan_read` | `read` | `plans:read` | 读取计划列表或计划详情 | — |
| planning | `plan_mutate` | `action_proposal` | `plans:write` | 更新计划元数据或删除计划 | `plans.plan.update`、`plans.plan.delete` |
| planning | `schedule_timeline_read` | `read` | `plans:read`、`records:read` | 读取跨领域日程及关联的喂养、吸奶和生长记录 | — |
| planning | `schedule_timeline_mutate` | `action_proposal` | `plans:write`、`records:write` | 修改任务/日程及喂养、吸奶、生长记录 | 14 个 timeline/record Action，见第 7 节 |
| profile | `profile_read` | `read` | `profile:read` | 读取当前用户与宝宝基础资料 | — |
| profile | `profile_update` | `action_proposal` | `profile:write` | 更新妈妈/宝宝基础资料和当前分娩关联 | `profile.update`、`profile.current_infants.replace` |

当前 `schedule_timeline_read` 和 `schedule_timeline_mutate` 是粗粒度聚合 Tool：
它们分别同时要求 plans 与 records 的读/写权限。缺少其中任一权限时，整个聚合
Tool 都不会暴露给模型；未来若要支持部分权限，应拆分 Tool 或重新设计权限契约，
不能只放宽 executor 校验。

## 5. 权限、owner scope 与可信参数

权限执行不是 prompt 约定，而是多层 fail-closed 校验：

1. Run 创建时保存 `agent.authorization_context.v1`，包含 owner、角色、权限和
   token 身份；它是本次 Run 的冻结 admission authority。
2. 构建 SDK Agent 时，仅暴露 `required_permissions` 是 Run 权限子集的 Tool。
3. `ToolExecutor` 再按 `run_id + actor_user_id` 读取 Run，重新构造冻结 principal，
   并重复权限检查。
4. 模型参数禁止出现 `actor_user_id`、`owner_user_id` 或 `user_id`；所有 Tool
   当前都只能作用于 `owner_scope=actor`。
5. applicator 再校验 `action_type`、`target_type`、`target_id` 和 payload 的一致性。
6. 需要确认的 Action 在确认或拒绝时使用新的 JWT principal 重新检查权限；
   权限在 proposal 后被撤销时，旧 Run snapshot 不能授权确认。

`TrustedToolArgumentsProvider` 从 durable user message、client context、Product
profile 和历史 ToolResult 中生成可信参数。模型只能提供 `input_schema` 中的公共参数；Runtime 参数独立生成，字段
冲突会直接失败，合并后再使用 `internal_input_schema` 校验。

## 6. Tool 执行与输出契约

每次调用按以下顺序执行：

1. 校验 Tool 存在、Run owner 和冻结 authorization context。
2. 先按白名单生成 `safe_args`，权限拒绝也只记录安全参数。
3. 检查权限并在拒绝时持久化 `tool.blocked`。
4. 拒绝模型提供 actor 字段；校验公共输入 JSON Schema。
5. 合并 Runtime 可信参数并校验内部输入 Schema。
6. 持久化 ToolCall 与 `tool.started`。
7. 在 Tool 声明的墙钟超时内执行 handler。
8. 要求 handler 返回 `ToolResult`，并校验 `canonical_output` Schema。
9. 持久化 canonical output、模型 output、`tool.completed` 和 deferred events。
10. 将配对的 `function_call_output` 写入 append-only Context，再进入下一轮模型。

`ToolResult` 明确分成两份输出：

- `canonical_output` 是权威完整结果。默认不超过 32 KiB 时写 PostgreSQL；更大时
  写对象存储，PostgreSQL 保存 externalized 摘要和 `output_ref`。
- `model_output` 是 handler 明确选择、直接写回模型上下文的有界结果，默认上限
  16 KiB。它可以与 canonical output 不同，但不能隐式截断；超限会失败。
- `supplemental_content` 可向模型返回 image/file block；内部 `asset_id` 必须在
  provider 调用前解析，不能进入 provider 请求或 Replay 内容。
- `safe_arg_fields` 和 `safe_output_fields` 只控制事件/日志摘要，不控制模型能看到
  的 `model_output`。

失败会持久化 `tool.failed`；权限失败持久化 `tool.blocked`。普通非致命 Tool
错误会作为结构化 `function_call_output` 返回模型继续推理；输出无法安全持久化或
模型输出违反大小契约时属于 fatal error，不允许继续生成看似成功的答复。

虽然 SDK FunctionTool 当前设置 `strict_json_schema=false`，Runtime 仍在执行边界
使用 JSON Schema Draft 2020-12 做权威输入和输出校验。

## 7. Action 方案与清单

### 7.1 当前执行模型

所有 Action policy 都使用 `agent.action_policy.v1`，并强制：

- 非空 permission；owner 与 target 校验；
- `idempotency_required=true`；
- `audit_required=true`；
- medium/high 且不确认时必须写明 exemption；
- `requires_confirmation` 与 `blocking_policy` 必须一致；
- 当前全部 `allows_payload_edit=false`。

当前只实现两种 blocking policy：

- `must_wait`：创建/复用 Action 后同步调用 applicator，得到 `applied` 或
  `failed` 后 Tool 才返回。
- `wait_for_confirmation`：Action 进入 `confirmation_required`，Run 进入
  `waiting_for_confirmation`。用户确认后同步 apply 并将 Run 重新排队；拒绝会取消
  Run，30 分钟未确认会使 Action 和 Run 过期。

当前没有 `enqueue_and_continue`、durable outbox 或后台 Action apply worker。
因此不能把“已创建 Action”描述成异步排队成功。无确认 Action 仍会等待实际写入
结果；需要确认的 Action 则明确暂停整个 Run。

外部 Product 写入使用稳定幂等键 `agent-action:{action_id}`。无确认 Action 在
第一次 HTTP apply 前先提交 Action identity，进程重试时复用同一业务幂等身份。
当前 18 个 Action 都通过 Product Backend internal API apply。

Action policy 声明所有 Action 必须审计。当前实际审计链是 durable AgentAction、
`action.*` ledger events、Product apply 结果和幂等身份；`RuntimeActionService`
尚未单独写入通用 `audit_logs` 表。若以后要求独立合规审计记录，需要显式接入
`AuditService`，不能仅依赖 policy 字段。

### 7.2 当前 18 个 Action

| Proposal Tool | Action | Target | 风险 | 确认 | 权限 / 说明 |
| --- | --- | --- | --- | --- | --- |
| `plan_mutate` | `plans.plan.delete` | `plan` | medium | 是 | `plans:write` |
| `plan_mutate` | `plans.plan.update` | `plan` | medium | 否 | `plans:write`，显式意图 exemption |
| `profile_update` | `profile.current_infants.replace` | `profile` | medium | 是 | `profile:write` |
| `profile_update` | `profile.update` | `profile` | low | 否 | `profile:write` |
| `schedule_timeline_mutate` | `plans.milk_schedule.reschedule` | `plan` | medium | 是 | `plans:write` |
| `schedule_timeline_mutate` | `plans.task.complete` | `plan_task` | medium | 否 | `plans:write`，显式意图 exemption |
| `schedule_timeline_mutate` | `plans.task.create` | `plan_task` | medium | 否 | `plans:write`，显式意图 exemption |
| `schedule_timeline_mutate` | `plans.task.delete` | `plan_task` | medium | 否 | `plans:write`，显式意图 exemption |
| `schedule_timeline_mutate` | `plans.task.update` | `plan_task` | medium | 否 | `plans:write`，显式意图 exemption |
| `schedule_timeline_mutate` | `records.feeding_record.create` | `feeding_record` | low | 否 | `records:write` |
| `schedule_timeline_mutate` | `records.feeding_record.delete` | `feeding_record` | medium | 否 | `records:write`，显式意图 exemption |
| `schedule_timeline_mutate` | `records.feeding_record.update` | `feeding_record` | medium | 否 | `records:write`，显式意图 exemption |
| `schedule_timeline_mutate` | `records.growth_record.create` | `growth_record` | low | 否 | `records:write` |
| `schedule_timeline_mutate` | `records.growth_record.delete` | `growth_record` | medium | 否 | `records:write`，显式意图 exemption |
| `schedule_timeline_mutate` | `records.growth_record.update` | `growth_record` | medium | 否 | `records:write`，显式意图 exemption |
| `schedule_timeline_mutate` | `records.pumping_record.create` | `pumping_record` | low | 否 | `records:write` |
| `schedule_timeline_mutate` | `records.pumping_record.delete` | `pumping_record` | medium | 否 | `records:write`，显式意图 exemption |
| `schedule_timeline_mutate` | `records.pumping_record.update` | `pumping_record` | medium | 否 | `records:write`，显式意图 exemption |

当前风险分布为 4 个 low、14 个 medium、0 个 high；3 个 medium Action 要求确认，
其余 11 个 medium Action 依赖已登记的显式用户意图 exemption。

## 8. 持久化、恢复和可观测性

- ToolCall、ToolOutput、Action、Context item 和 application event 都写 PostgreSQL；
  Redis 不是权威工具状态。
- 未完成的 provider function call 会从 append-only Context 中恢复；已有配对 output
  的调用不会重复执行。
- Action 通过 action ID、Run、actor、target 和 idempotency key 关联，已 applied
  或 failed 的 Action 可确定性重放结果。
- 每次模型调用的 `agent_model_execution.v1` 清单记录实际可见 Tool schema、
  namespace、deferred 标记、Tool/Action catalog 版本与 SHA-256。
- Replay 导出 Run 的 Tool/Action/manifest 信息；内容默认脱敏。
- 日志和指标只允许 bounded tool name、run/request/trace identity 与安全摘要，
  不把原始用户文本、Tool 参数、Tool 输出或 token 作为维度。

## 9. 当前明确限制

这些是现状，不应在文档或 UI 中描述成已经实现：

- 没有并行 FunctionTool 调用；模型和 Tool 执行均串行。
- 没有异步 Action outbox/effect lane，也没有 Action apply worker。
- 没有 high-risk Action；新增 high Action 前必须设计确认、审计和人工/安全门禁。
- 没有可编辑确认 payload；所有 Action 的 `allows_payload_edit=false`。
- Tool `retry_policy` 不会触发 executor 自动重试。
- owner scope 只有当前 actor，不支持共享资源、组织资源或跨用户委托。
- Runtime 内部工具可以写 Runtime workflow/artifact，但不能借此绕过 Product
  business write 的 Action 边界。
- `schedule_timeline_*` 的 plans/records 权限粒度较粗。
- 日记，以及产前资料采集、孕期计划创建、待产包和待产包购物车能力当前已从
  Runtime Tool/Skill 目录下线；Product Backend 的既有数据和外部 OpenAPI 兼容
  快照暂不做破坏性删除。

## 10. 变更维护协议

下列任何修改都属于“工具方案修改”，必须更新本文档：

| 修改类型 | 本文档至少更新 |
| --- | --- |
| 新增、删除、重命名 Tool | namespace、Tool 清单、数量、目录哈希 |
| 修改 description、输入/输出 Schema、timeout、输出上限 | ToolContract 与对应 Tool 说明、目录哈希 |
| 修改 permission、owner scope、trusted args | 权限章节、Tool 清单、目录哈希 |
| 修改 operation、retry policy、Action 绑定 | ToolContract、Tool/Action 清单、执行流程、目录哈希 |
| 新增或修改 Action risk、确认、exemption、幂等、审计 | Action 章节、风险统计、目录哈希 |
| 修改 eager/deferred、namespace、Skill 或 ToolSearch | 发现与加载章节、执行清单说明 |
| 修改 ToolExecutor、输出持久化、错误或隐私规则 | 执行与输出、持久化和限制章节 |
| 引入并行、outbox、Action worker 或新 owner scope | 架构图、生命周期、限制和部署/恢复说明 |
| 修改 CapabilityModule、领域所有权或组合清单 | 代码组织、namespace、Tool/Action 清单与目录哈希 |

每个工具方案变更的 Definition of Done：

1. 修改 capability 内的 contract、handler、`model_schemas.py`、policy 或
   applicator，并同步该领域的 `module.py`。
2. 新增能力时显式加入 `CAPABILITY_MODULES`；通过模块构造校验和
   `validate_runtime_composition`，确保 registry、handler、namespace、Action
   policy 和 applicator 一一对应且权限覆盖完整。
3. 若是破坏性契约变更，只提升受影响版本，并补充数据、Replay 和 rollout 策略。
4. 运行 `python scripts/export_runtime_contract_catalog.py` 重生成机器目录。
5. 更新本文档的相关正文、Tool/Action 清单、数量与顶部 catalog SHA-256。
6. 增加或更新 Tool contract、权限绕过、Action 生命周期、恢复和行为 eval 用例。
7. 运行以下最低验证集：

```bash
python scripts/export_runtime_contract_catalog.py --check
python -m pytest \
  tests/test_runtime_metadata.py \
  tests/test_tool_contracts.py \
  tests/test_tool_observability.py \
  tests/test_runtime_actions.py \
  tests/test_runtime_v1_invariants.py
python scripts/run_runtime_v1_harness.py
python -m ruff check app tests scripts
python -m mypy app tests
```

发布前仍需运行完整 `python -m pytest` 和 behavior eval gate。
