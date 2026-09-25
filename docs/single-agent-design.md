# Momcozy 单智能体架构

CozyMate（`cozymate`）是唯一的 Agent，使用单条模型—工具推理链。
当前启用唯一的 `lactation` 泌乳与喂养不适评估 Skill 和 `load_service_skill` 工具。
没有设备 Skill、业务工具、工具搜索 namespace 或产品 Action。

## 组织与加载

- `app/agent/definition.py` 定义名称与指令，`system_prompt.md` 保存基础规则。
- `app/agent/skills/lactation/SKILL.md` 只维护高频问题路由、共用工作方式与安全边界；`references/*.md` 分别维护五类问题的具体解决方案。
- `app/agent/skill_registry.py` 校验 Skill 与 reference，并提供渐进加载工具。
- `app/capability_catalog.py` 仅显式注册 loader，`app/bootstrap/` 注入 Runtime。
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

loader 的 `momcozy.service_skill.v3` 回执包含加载状态、Skill 身份、资源类型与身份、简介及 SHA-256，按实际顺序进入账本，正文不在工具结果中。兼容字段 `version` 自动取所选文档的内容 SHA-256，无需维护数字版本。
加载成功时，所选路由或 reference 以独立 `developer` 消息紧随回执持久化到当前 Run。正文不拼入开头的稳定指令；相同内容指纹的对应文档仍可见时不重复加载。
恢复与后续 Run 保留当时的正文快照；它们是会话记录，不是另一份维护版本。修改源文件并重启应用后加载新内容，不改写旧 Run。该 Run 被压缩时，文档一起参与摘要；压缩后不再自动恢复完整正文。普通工具结果、用户文字和历史摘要不能激活文档。仅回执记录须与当前资源身份、内容指纹及兼容字段匹配才能补出正文。v1/v2 回执只兼容旧 Skill 文档，专题 reference 从 v3 开始。
完整请求仍经过上下文预算检查，必要时执行 durable compaction。

## 执行边界

SDK Runner 负责模型—工具循环，自研 AgentLoop 负责租约、账本、恢复、消息和安全门。
只暴露通过 Run 权限过滤后的 eager loader；不存在 deferred 工具，因此不提供 ToolSearchTool。
工具串行执行并通过统一 ToolExecutor 校验和持久化。历史中未完成的已删除工具无法重新执行。

当前咨询使用用户提供的信息与当前 Run 的基础资料上下文，不查询或写入业务记录。
账号验证、基础资料快照及附件解析仍通过 Product Backend 服务身份调用，属于 Runtime 基础设施。
产品 Action 目录为空。通用确认、幂等、审计和恢复结构仍可读取历史账本，但不会授权已删除业务操作。

紧急风险先走安全分流；未命中时由同一个 CozyMate 整合上下文并回复。
普通健康信息与情绪支持不需要加载 Skill。所有具体限制与契约维护流程见 [tools.md](tools.md)。

泌乳 Skill 先按五类高频问题分流，再只加载相关专题方案。奶量综合判断分别核对生长发育、宝宝摄入、妈妈泌乳供需；疼痛主诉先澄清发生情境、风险与变化，再给下一步及必要复评。无需为单纯疼痛完成奶量问卷。设计与来源见 [lactation-assessment.md](lactation-assessment.md)。

## 验证

测试覆盖当前工具与技能目录、已删除技能和未知 reference 拒绝、权限过滤、路由与专题渐进加载、上下文预算、工具调用配对、恢复与流式消息。
通用 Runtime 的业务工具和 Action 测试使用测试专用契约，不把它们注册到产品目录。
行为评估覆盖咨询、能力边界、安全分流及多轮跟进；多轮场景逐轮使用同一测试 thread 的真实 Run，不能只映射最后一轮。真实模型评审另行执行，见 [对话与跟进评测](conversation-followup-evals.md)。
