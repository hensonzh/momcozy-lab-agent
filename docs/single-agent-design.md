# Momcozy 单智能体架构

CozyMate（`cozymate`）是唯一的 Agent，使用单条模型—工具推理链。
当前启用唯一的 `lactation` 泌乳与喂养不适评估 Skill，以及 `load_service_skill`、`read_topical_records`、`read_schedule`、`change_records`、`change_schedule` 五个工具。
记录和个人日程的批量新增／更新通过两个产品 Action 执行；没有设备 Skill、工具搜索 namespace、删除或其它业务写能力。

## 组织与加载

- `app/agent/definition.py` 定义名称与指令，`system_prompt.md` 保存基础规则。
- `app/agent/skills/lactation/SKILL.md` 只维护高频问题路由、共用工作方式与安全边界；`references/*.md` 分别维护八个专题的具体方案。reference 文件名、frontmatter `name`、`reference_id` 与交叉引用统一使用小写连接线。
- `app/agent/skill_registry.py` 校验 Skill 与 reference，并提供渐进加载工具。
- `app/capability_catalog.py` 显式注册 loader、记录／日程查询与批量写工具，`app/bootstrap/` 注入 Runtime。
- `app/agent_runtime/` 保留通用执行、上下文、安全、持久化与恢复内核。

系统提示词只包含 Skill 简介。需要专业泌乳咨询时先加载路由：

```json
{"skill_id": "lactation"}
```

确定主诉后，再按 `SKILL.md` 中的枚举加载一个专题，例如：

```json
{
  "skill_id": "lactation",
  "reference_id": "milk-supply-assessment"
}
```

loader 的 `momcozy.service_skill.v1` 回执包含加载状态、Skill 身份、资源类型与身份、简介及 SHA-256，按实际顺序进入账本，正文不在工具结果中。加载事件仍沿用 `version` 字段（值为内容 SHA-256），回执不重复返回。
加载成功时，所选路由或 reference 以独立 `developer` 消息紧随回执持久化到当前 Run。正文不拼入开头的稳定指令；相同内容指纹的对应文档仍可见时不重复加载。
恢复与后续 Run 保留当时的正文快照；它们是会话记录，不是另一份维护版本。修改源文件并重启应用后加载新内容，不改写旧 Run。该 Run 被压缩时，文档一起参与摘要；压缩后不再自动恢复完整正文。普通工具结果、用户文字和历史摘要不能激活文档。仅当前 v1 格式的回执记录与资源身份和内容指纹匹配时才能补出正文；旧结构回执不再恢复完整正文。
完整请求仍经过上下文预算检查，必要时执行 durable compaction。

## 执行边界

SDK Runner 负责模型—工具循环，自研 AgentLoop 负责租约、账本、恢复、消息和安全门。
只暴露通过 Run 权限过滤后的 eager loader、记录／日程查询和批量写工具；不存在 deferred 工具，因此不提供 ToolSearchTool。
工具串行执行并通过统一 ToolExecutor 校验和持久化。历史中未完成的已删除工具无法重新执行。

当前咨询使用用户提供的信息与当前 Run 的基础资料上下文；必要时按权限及时间窗只读喂养、吸奶、尿便、疼痛、含乳、宝宝喂后心情或连续生长记录。`read_topical_records` 单次可提交 1～3 项独立专题与日期范围，按查询分组返回、分别标记截断，新增或更新需在上一轮明确复述批次并获得用户自然语言确认，写工具调用后通过 Action 执行；不提供按钮确认或删除能力。
账号验证、基础资料快照及附件解析仍通过 Product Backend 服务身份调用，属于 Runtime 基础设施。
产品 Action 包括记录与个人日程两类原子批量写入。通用确认、幂等、审计和恢复结构仍可读取历史账本，但不会授权已删除业务操作。

紧急风险先走安全分流；未命中时由同一个 CozyMate 整合上下文并回复。
普通健康信息与情绪支持不需要加载 Skill。所有具体限制与契约维护流程见 [tools.md](tools.md)。

泌乳 Skill 先按八个专题分流，再只加载与主问题相关的方案；完整判断确实依赖其他维度时才追加 reference。奶量判断、奶量管理、宝宝摄入和生长分别建模；亲喂疼痛、吸奶问题和乳房症状按发生场景分流，返工喂养作为独立管理场景。设计见 [lactation-assessment.md](lactation-assessment.md)。

## 验证

测试覆盖当前工具与技能目录、已删除技能和未知 reference 拒绝、权限过滤、路由与专题渐进加载、上下文预算、工具调用配对、恢复与流式消息。
通用 Runtime 的其它业务工具和 Action 测试使用测试专用契约，不把它们注册到产品目录。
行为评估覆盖咨询、能力边界、安全分流及多轮跟进；多轮场景逐轮使用同一测试 thread 的真实 Run，不能只映射最后一轮。真实模型评审另行执行，见 [对话与跟进评测](conversation-followup-evals.md)。
