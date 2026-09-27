# Tool/Action 方案

<!-- tooling-document: canonical -->
<!-- runtime-baseline: momcozy-agent-v1 -->
<!-- runtime-contract-catalog-sha256: 91ca1ff7dfdd4f3cc09c3f8e6a3edf4695e2e3f917294925cd64a07c6641a101 -->
<!-- tool-count: 5 -->
<!-- action-count: 2 -->

Momcozy AI registers a lactation Skill loader, bounded records and personal schedule readers, and two atomic batch-write tools. Write tools require prior explicit agreement in the conversation; the backend does not interpret that agreement.

## 工具与技能

`load_service_skill` 为 eager FunctionTool，权限为 `agent:run`，operation 为 `runtime_internal`。
输入必填 `skill_id=lactation`，可选 `reference_id` 只能取注册表公开的专题标识。首次进入泌乳专业咨询时省略 `reference_id`，加载精简 `SKILL.md` 分流规则；确定主诉后按路由再次调用，只加载当前问题对应的 reference。
`momcozy.service_skill.v1` 工具结果返回 `status=loaded`、`skill_id`、`resource_type`、`resource_id`、简介与内容 SHA-256，不包含正文。加载 `SKILL.md` 记录 `skill.loaded`，加载专题记录 `skill.reference.loaded`。加载事件仍沿用 `version` 字段（值为内容 SHA-256），回执不再重复返回。
加载成功时，handler 从应用内注册表生成所选 `SKILL.md` 或 reference 的独立 `developer` 消息。ToolExecutor 将工具回执和文档正文在同一事务中紧邻写入当前 Run 的上下文账本；正文不进入开头的稳定提示词，也不重复放进工具结果。完整请求仍经过上下文预算检查。
每份加载文档属于加载它的 Run：原始历史保留时可见，该 Run 进入压缩范围时，文档与调用、回执一起交给压缩模型。压缩后只保留摘要，不根据摘要恢复完整路由或专题正文，后续需要时须重新加载。历史快照保留当时原文，不受之后修改源文件影响。
对于没有正文快照的加载回执，普通模型请求仅在回执符合当前 v1 格式且资源身份和内容指纹与当前注册表一致时补出正文。旧结构回执不再恢复完整正文，也不会改写历史账本；历史工具结果中如果含有正文，在普通模型投影中移除，不将工具返回文字直接提升为指令。

`read_topical_records` 为 eager FunctionTool，权限为 `records:read`，operation 为 `read`。仅在需要日常／历史证据时读取 `feeding`、`pumping`、`diaper`、`pain`、`growth`、`latch` 或 `after_feeding_mood` 专题。模型传 `queries`（1～3 项）；每项指定一个专题、独立的起止日期（连续且最多 30 个自然日）、可选的条数上限（1～20）。模型可见 Schema 将查询项分为两种结构：宝宝侧喂养、尿便、生长和宝宝喂后心情须提供当前分娩宝宝的 `infant_id`；妈妈侧吸奶、疼痛与含乳没有 `infant_id` 字段，传入（包括 `null`）会被拒绝。参数错误回执只给安全的字段路径和规则类型，供模型修正后重试，不回显记录数据；“最近”的日期范围由模型结合请求自行判断，不添加固定天数。一次工具调用按顺序复用 Backend 单专题只读接口，结果以 `results` 分组，不跨主题合并或推断全量历史；任一查询失败则整个工具调用失败，不发布部分结果。owner 来自 Run 冻结的授权快照，时区来自本轮已验证的 client context（缺失时 UTC），不能由模型伪造。Backend 再校验宝宝归属与本次分娩关系，按来源最多读取各项的 `limit+1` 条并合并排序。每组分别返回查询主题、宝宝与日期范围、`has_more` 和 `coverage=recorded_entries_only`：前者表示 Backend 截断或工具输出预算截断（24 KiB），后者表示无记录不等于没有发生。为避免某个主题占满预算，按各组顺序交替选取完整记录，超出预算时只略去整条并将对应组标为 `has_more`；不适用的记录字段在工具结果中省略。尿便专题同时返回单次尿布事件与 `daily_status` 的尿便汇总条目，用 `record_type` 区分，不能直接相加避免重复计数；同一 `daily_status` 中的喂后心情仅在 `after_feeding_mood` 专题返回日期与 `mental_state`。含乳专题仅返回记录时间与受控选项 `latch_status`。宝宝睡眠、发育，妈妈精力、睡眠、情绪、储奶、奶瓶等其它记录均不开放查询。返回字段白名单，不包含自由文本、原始照片或完整历史；单条生长测量不能推断趋势，旧版与新版记录保留来源，不跨日期拼接指标。

只有一个泌乳咨询 Skill；设备 Skill、历史的其它业务写工具和 Action 未注册。
`app/agent/skills/lactation/SKILL.md` 只维护高频问题路由、共用工作方式和安全边界；具体方案维护在 `app/agent/skills/lactation/references/*.md`。当前八个专题覆盖奶量评估、奶量管理、宝宝摄入、宝宝生长、含乳与乳头疼痛、吸奶支持、乳房症状和返工喂养。文件名、frontmatter `name`、`reference_id` 与交叉引用统一使用小写连接线；注册表按文件名稳定排序，不使用无业务语义的 `order` 参数。目录不分版本；每个文档由独立内容指纹识别，工具回执的 Schema 版本不代表内容版本。
没有 deferred namespace，不向模型提供 ToolSearchTool。工具调用保持串行。

工具状态文案由模型在本次工具调用的 `user_facing_status` 参数中现场写出 `running`、`success`、`failure` 三句话，不使用固定候选词库，也不检查语言。Runtime 在执行前发出 `running`，执行后按真实结果选 `success` 或 `failure`；单个阶段的文案缺失、格式不合适或触发现有输出安全规则时仅省略该阶段，不影响其余阶段或工具执行；没有任何有效文案时不显示工具状态，不回退到固定英文文案。状态文案不作长度拦截。模型文案还会经过保守的状态专用检查：省略可识别的用户指令、凭证索取或高危内容；`success` 在工具执行前生成，因此不允许声称记录数量、发现、诊断或其他具体结果，未通过时只省略 `success`，最终回答仍可在看到工具回执后陈述事实。状态字段从业务参数中剥离，不传给工具 handler。

## 记录与日程的查询、批量变更

`read_topical_records` 仍只开放七个 App 专题；每条回执新增 `record_id` 和 `revision`，更新时还需 `source` 和对应宝宝 ID。`read_schedule` 限个人日程，日期区间为本地起日含、终日不含（最多 62 天）；返回 `id`、`updated_at` 供定向更新，时区由 Run 注入。

`change_records`、`change_schedule` 接收 1～20 项 `create`/`update` 操作，支持不同日期、不同类型和同一日多条记录；更新仅修改明确给出的字段、保留原记录其余字段。吸奶创建需有实测奶量和侧别；更新吸奶侧别时，查询回执保留镜像中的侧别；关联的妈妈侧吸奶记录必须继续有实测奶量，不得清空后留下无效镜像。记录 `diaper` 分单次事件和每日汇总，`after_feeding_mood` 与每日汇总若同属一条物理记录须基于相同原始版本在一批内合并，不重复增加版本。每个工具调用形成一个 Action，Backend 在同一事务中执行相应批次并持久化应用回执；任何项失败则该领域整批回滚。不同领域两次调用没有跨服务事务，不能称为全局原子操作。

模型可见的创建项按记录主题（喂养方式及尿布事件／每日汇总另分支）区分，必填时间、日期、数值与受控选项不再藏在通用可选字段里；更新项必须复制查询回执的目标身份与版本，并按来源和主题检查可修改字段。日程创建须包含非空标题、日期和本地 `HH:MM` 时间；更新须包含目标 ID 与版本。Agent 在提议 Action 前预检整个批次，静态无效输入只返回安全的字段路径与规则类型，不创建 Action；未来记录时间也在此阶段拦截。Backend 仍负责归属、真实版本、事务和最终业务校验。后端拒绝已提交的 Action 时，工具回执可附带受控的 `failure`（从零开始的批次位置、字段路径、原因码），不回显原始记录和后端自由文本；没有安全细节时只给错误码。补齐或更正字段后，必须重新复述完整批次并等待用户新一轮自然语言确认，不悄悄重试写入。

用户在普通聊天中确认，不需要操作卡片或按钮。写工具输出 `ok`、`action_status` 与实际回执；Backend 明确拒绝时说明整批未保存；超时或回执异常时写入结果可能未知，先重新查询再决定是否重试，不能直接说未保存或已保存。个人日程不等于提醒通知。

## 新增工具设计准则

以下用于评审今后新增或修改的模型可调用工具，不要求机械改造现有工具。

1. **一个明确职责，服务一次决策**：先写清“工具解决什么问题、什么时候调用、什么时候不要调用”，再定名称与边界。不同权限或副作用原则上分开；数据来源不同但服务同一决策时可合并，须保留来源信息。不要为了追求“一工具一字段”把同一个任务拆成大量调用。工具描述统一按 *what it does / when to use it* 组织，说明可用条件和限制；模型据当前任务决定是否调用，服务端负责授权和实际执行。
2. **输入 Schema 帮助正确调用，同时可执行校验**：模型需要提供的字段应说明含义、类型、是否必填，并在适用时说明允许值／格式、合理范围和有条件依赖（如宝宝主题才需要 `infant_id`）。按 JSON Schema 规范把 `required` 写在对象层级，`enum`、`format`、`minimum`／`maximum` 等写在对应字段上；不是每个字段内部放一个 `required: true`。范围应有真实业务依据，不为符合示例随意设置；字段间关系、身份与权限必须由服务端再次验证，不能只靠 description。用户身份、可信时区等应由 Runtime 注入，不交给模型填写。
3. **输出提供事实与边界，而不是重复说明书**：返回本次任务所需的事实、资源身份／时间范围、来源和重要限制（例如截断、只覆盖已记录数据）；空结果、缺失值和失败要能区分。不要默认给每条结果附上静态 `field_description` 或整段 `usage_notes`；固定字段解释放在工具／字段描述或 Skill 中。仅当解释随本次数据变化、确实会改变判断时，才返回简短、可核实的说明。`next_step_hint` 不能把推测性建议伪装成数据；专业判断由 Agent 结合 Skill 和用户情况完成。
4. **区分不同输出通道**：业务 API 响应、Runtime 持久化结果、模型可见的 `model_output`、审计事件摘要和面向用户的状态文案不是同一份契约。可在不丢失有用信息的前提下精简模型输出（如省略不适用的空字段），但不能让模型看不到重要来源、范围和限制；不能把不可信的工具文本提升为 `developer` 指令。输出要受大小、隐私与安全边界约束，不因为输出字段有解释就绕过校验。
5. **先定义权限与验收**：逐工具声明只读／写入、owner 范围、权限、超时与重试；写操作须明确确认、幂等和审计。新增工具至少覆盖“该调用／不该调用”、缺参或错误范围、无权限、空结果／截断、异常与恢复的测试或行为评测。输入 Schema、handler、输出契约、文档和生成目录保持一致。

图片示例中的 `gestational_age`、`delivery_method` 体现了“参数描述应帮助模型判断用途”，但它们不是所有工具的必备字段，也不应照搬其中的孕周限制。输出示例里的 `field_description`、`usage_notes`、`next_step_hint` 是按需选择的表达形式，不是每次调用都要返回的模板。

## 事实与能力边界

泌乳咨询使用用户提供的信息与当前 Run 的基础资料上下文；确有需要时可按主题限量读取当前用户记录。记录与个人日程可在自然语言明确确认后分别批量新增或更新；不开放删除、计划任务、提醒或其它业务写入。
日记、产前采集、孕期计划、待产包及购物车均无运行时能力。
基础资料上下文、账号验证、附件解析属于 Runtime 基础设施，不是模型可调用的工具。

## 执行与权限

工具通过统一 ToolExecutor 执行，按 Run 冻结权限和当前 owner 校验参数与结果，记录 tool.started/completed/failed。
模型不能传入用户身份或 Runtime 可信字段。结果经持久化后进入模型上下文。
现有两个 Action `records.batch.change` 与 `schedule.batch.change` 在写工具调用后立即执行（`must_wait`），不进入 `waiting_for_confirmation`。模型需先在前一轮以自然语言逐项说明批次，获得用户后续明确同意后再调用；服务端不解析或验证该对话确认。Backend 对用户身份、归属、字段、版本与批次事务继续校验；Action ID 用作持久幂等身份。
历史工具调用无法通过当前注册表重新执行。

## 维护与验证

`app/capability_catalog.py` 是显式组合清单，`app/bootstrap/agent_runtime.py` 校验工具、handler 与 Action 覆盖关系。
修改目录、Skill 或契约后同步本文档，并运行：

```bash
python scripts/export_runtime_contract_catalog.py --check
python -m pytest
python scripts/run_runtime_v1_harness.py
python -m ruff check app tests scripts
python -m mypy app tests
python scripts/run_behavior_eval.py --validate-only
```

真实模型行为评估需要单独生成 Run 与评审结果；validate-only 只验证评估目录。
多轮场景要求同一隔离测试会话的有序 Run 列表，逐轮检查行为与历史，再审查完整回复；运行方式见 [对话与跟进评测](conversation-followup-evals.md)。
