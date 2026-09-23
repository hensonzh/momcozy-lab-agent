# Tool/Action 方案

<!-- tooling-document: canonical -->
<!-- runtime-baseline: momcozy-agent-v1 -->
<!-- runtime-contract-catalog-sha256: 4cbf030e850836ce035907db22c714653374a21a2ef755cf99707b48d7874c3d -->
<!-- tool-count: 1 -->
<!-- action-count: 0 -->

当前 CozyMate 仅提供泌乳咨询 Skill 加载工具，不查询或写入产品业务记录。

## 工具与技能

`load_service_skill` 为 eager FunctionTool，权限为 `agent:run`，operation 为 `runtime_internal`。
输入必填 `skill_id=lactation`，可选 `reference_id` 只能取注册表公开的专题标识。首次进入泌乳专业咨询时省略 `reference_id`，加载精简 `SKILL.md` 分流规则；确定主诉后按路由再次调用，只加载当前问题对应的 reference。
`momcozy.service_skill.v3` 工具结果返回 `status=loaded`、`skill_id`、`resource_type`、`resource_id`、简介与内容 SHA-256，不包含正文。加载 `SKILL.md` 记录 `skill.loaded`，加载专题记录 `skill.reference.loaded`；兼容字段 `version` 自动取所选文档的内容 SHA-256，不手工维护内容版本号。
加载成功时，handler 从应用内注册表生成所选 `SKILL.md` 或 reference 的独立 `developer` 消息。ToolExecutor 将工具回执和文档正文在同一事务中紧邻写入当前 Run 的上下文账本；正文不进入开头的稳定提示词，也不重复放进工具结果。完整请求仍经过上下文预算检查。
每份加载文档属于加载它的 Run：原始历史保留时可见，该 Run 进入压缩范围时，文档与调用、回执一起交给压缩模型。压缩后只保留摘要，不根据摘要恢复完整路由或专题正文，后续需要时须重新加载。历史快照保留当时原文，不受之后修改源文件影响。
对于没有正文快照的旧加载回执，普通模型请求仅在回执的资源身份、内容指纹及兼容字段与当前注册表一致时补出正文；v1/v2 回执只兼容原有 Skill 文档，reference 只接受 v3 回执。不会用新正文替换旧回执，也不会改写历史账本。历史 v1 工具结果的正文在普通模型投影中移除，不将任意工具返回文字直接提升为指令。

`search_rednote_posts` 实现与 App 卡片保留，但未注册到当前 Runtime；其工具定义不进入模型请求，主提示词与泌乳 Skill 不再包含检索指引。仅填写数据源配置不会启用该工具；详见 [RedNote 接入与验收](rednote-retrieval.md)。

只有一个泌乳咨询 Skill；设备 Skill、17 个业务工具及其 handler、Schema、资料和业务 Action 实现已删除。
`app/agent/skills/lactation/SKILL.md` 只维护五类高频问题路由、共用工作方式和安全边界；具体解决方案维护在 `app/agent/skills/lactation/references/*.md`。当前五个专题分别覆盖奶量与摄入、泌乳建立与产出变化、含乳与乳头疼痛、吸奶舒适度与泵量、胀满硬块与炎症风险。目录不分版本；每个文档由独立内容指纹识别，工具回执的 Schema 版本不代表内容版本。
没有 deferred namespace，不向模型提供 ToolSearchTool。工具调用保持串行。

## 事实与能力边界

泌乳咨询使用用户提供的信息与当前 Run 的基础资料上下文，不查询或修改记录、计划或日程，不创建咨询卡片或工单。
日记、产前采集、孕期计划、待产包及购物车均无运行时能力。
基础资料上下文、账号验证、附件解析属于 Runtime 基础设施，不是模型可调用的工具。

## 执行与权限

工具通过统一 ToolExecutor 执行，按 Run 冻结权限和当前 owner 校验参数与结果，记录 tool.started/completed/failed。
模型不能传入用户身份或 Runtime 可信字段。结果经持久化后进入模型上下文。
当前 Action 目录和 applicator 目录均为空；通用 Action、确认、幂等与恢复内核保留，用于既有账本结构及运行时测试，不授权任何产品写入。
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
