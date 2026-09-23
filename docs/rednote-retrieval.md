# RedNote 社区经验检索

## 当前状态：暂停启用

保留数据源适配层、排序、工具实现、App 卡片、测试及历史评测材料，但 `search_rednote_posts` 已从当前 Runtime 能力清单移除。模型请求仅包含 `load_service_skill`；主提示词与泌乳 Skill 中的小红书指引已移除。填写 `REDNOTE_*` 配置不会自动启用检索。

以下是原实现设计及启用期间的历史验收记录，不代表当前运行行为。恢复接入时需重新注册能力、审查提示词并重新验收；历史模型评测结果不适用于当前提示词。专用模型评测脚本在能力停用时拒绝 `--live` 执行，仍支持校验历史场景格式。

## 状态与数据源决策（2026-09-16）

用户确认项目目前没有获授权的数据服务或官方合作渠道。代码提供可替换的数据源边界、工具、排序及 App 卡片，但真实检索尚未开通；未将抓取接口、模拟帖子或普通网页搜索冒充已授权的 RedNote 数据源。

调研证据：

| 官方或服务商公开资料 | 可核实能力 | 尚不能证明的事项 |
| --- | --- | --- |
| [小红书账号开放平台 API](https://openaccount.xiaohongshu.com/docs/api-reference) | OAuth、用户基本信息、授权管理 | 未列出全站笔记搜索接口 |
| [分享开放平台](https://agora.xiaohongshu.com/) | 向小红书分享内容 | 搜索、读取别人笔记、收藏指标及外部展示授权 |
| [电商开放平台](https://open.xiaohongshu.com/) | 店铺授权后的电商业务 | 社区笔记全文与跨区域搜索授权 |
| [新榜有数](https://data.newrank.cn/) | 商业内容数据服务，可作为商务询证候选 | 公开介绍不能证明其允许本产品检索、生成摘要及持久化展示图片和指标 |

结论：优先取得平台直接合作或书面授权的内容服务；在拿到具体授权范围、接口文档、访问凭证和海外地区覆盖证明之前，不指定某家服务商为合规可用的数据源。无登录绕过、Cookie 复用、签名逆向、反爬规避或访问受限页面逻辑。

**发布阻塞项**：数据提供方须允许搜索、派生摘要、图片及作者/收藏元数据展示、原帖跳转、对话引用持久化；确认地域、限流、计费、失效/撤回规则和数据保留期限。仅填一个授权编号不能代替真实授权。确定保留/撤回条款后，必须按该条款完成存量引用的到期或撤回处理再启用。当前未执行真实源联调；已用合成帖子执行 8 个真实模型场景，调用断言通过并完成本次助手的逐条摘要核对，仍不代表医学审核或真实来源质量验收，不能宣称功能整体上线完成。

## 接口与边界

沿用现有单 Agent / SDK Runner + durable AgentLoop；不新增智能体或另一套恢复机制。

```
App -> POST /v1/agent/runs（现有鉴权、限流和并发限制）
    -> CozyMate 判断安全与经验检索价值，生成匿名关键词
    -> search_rednote_posts(query, limit)
    -> RedNoteSearchService -> RedNoteProvider -> 授权数据源
    -> 同一工具结果进入模型上下文，并持久化 artifact.created
    -> 原有 /events、/stream、历史会话恢复 -> App 引用卡片 -> 外部浏览器/原应用
```

本功能的后端入口使用已有对话 Run API 和事件 API，无需另开一个绕过对话鉴权、安全策略和限流的公开搜索接口。后端内部接口为 `RedNoteSearchService.search(SearchRequest) -> SearchResult`。模型可调用 `load_service_skill`、`search_rednote_posts`；不恢复已删除的任何业务写工具或设备 Skill。Product Backend 无需保存社区内容，数据源密钥只在 Agent 后端。

- Tool 输入：`query`（2～120 字符匿名主题词）、`limit`（1～3，默认 3）；模型不得发送身份、联系方式、地址或完整健康档案。常见邮箱/电话/URL 被参数校验拒绝，语义最小化仍由主指令约束。
- 输出：`status: ok | no_results | unavailable`、`source: rednote`、`evidence_type: community_experience`、`posts`。
- 帖子字段：`title / summary / thumbnail / author / favorites / url / published_at / relevance_score`。作者为展示名称字符串；图片、作者、收藏量、发布日期可为 null。收藏缺失不等于零。
- 原帖只接受 HTTPS 的小红书/RedNote canonical explore 或 discovery/item 链接，保留跳转所需 query；拒绝任意站点、账号凭证、JS scheme 或搜索页。短链须由获授权的数据源提供其 canonical 原帖地址，本服务不跟随短链抓取。
- 卡片：`artifact_type=rednote_posts`、`schema_version=v1`，`artifact.payload.card` 符合 `docs/rednote-posts.schema.json`。`scripts/export_rednote_contract.py --check` 校验 schema 和虚构的客户端契约样例，App 固定副本保存在 `docs/backend-contract/`。同一结果进入工具输出及卡片，不让模型重新拼造引用字段。空结果不产生卡片。
- Tool 权限 `agent:run`，只读；无产品 Action。原有执行器负责 owner 校验、超时、输出 schema 校验、日志脱敏、工具结果和事件持久化。一次 Run 最多一次完成的检索，避免重复卡片；重放复用已持久化事件。

## 可替换的数据源

`RedNoteProvider.search(query, limit=20)` 返回候选 `RedNotePost`。更换供应商只需新增 adapter、替换 composition，不修改 Agent loop、提示词中的 tool 名或 App。

`AuthorizedGatewayProvider` 是 **MomCozy 自定义的规范化网关协议**，不是已经存在的小红书官方接口，也不是对某个商业服务的兼容声明。若最终供应商接口不同，应按其正式文档实现专用 adapter，不要求供应商碰巧返回本结构。

当前网关约定：对配置的完整 HTTPS URL POST `{ "query": "吸奶器 使用 经验", "limit": 20 }`；使用服务器端 `Authorization: Bearer ...`；返回 `{ "posts": [ ...统一帖子字段... ] }`。不传用户 ID、Run ID、病历或完整对话。候选必须来自真实授权内容，summary 应为忠实简短转述，不是未经核实的搜索片段；relevance_score 必须是相对于 query 校准到 0～1 的相关度，不得用点赞或收藏归一化代替相关度。

环境变量（默认全空、检索不可用）：

```
REDNOTE_GATEWAY_URL=
REDNOTE_GATEWAY_TOKEN=
REDNOTE_AUTHORIZATION_REFERENCE=
```

授权信息与服务配置齐全后才发出网络请求。该配置是部署注入，不由模型或 App 提供。完整请求预算 7 秒，HTTP socket 超时 6 秒，Tool 超时 10 秒；响应最大 256 KiB、最多 20 个候选，不跟随 HTTP 重定向。401/429/5xx、网络故障、无效格式、总超时均返回不可用且无卡片，仍允许原有回答。单条坏数据跳过，不丢弃其他合法候选。工具日志不记录原始关键词、密钥或供应商响应。

## 选择规则与回答

先剔除相关度小于 0.7、正文摘要过短或必填字段无效、未来时间的帖子；标题、摘要及作者命中现有注入/危险内容/隐私规则时也排除。此规则过滤不能代替医学审核。同一笔记不同签名 URL 只保留一条。按以下顺序稳定排序，最多取 limit 篇：

1. 相关度分档（[0.9,1)、[0.8,0.9)、[0.7,0.8)，1.0 为最高档），先保证主题匹配。
2. 同档收藏量降序，未知收藏排在已知之后。
3. 摘要完整度（最多 300 字）和可用作者/图片信息。
4. 精确相关度、发布时间作为进一步同分排序。

这是透明可测的排序，不声称能凭文本长度判断医学正确性。供应商相关度校准及内容质量仍须用真实母婴问题验收；不得以热门程度替代相关性。

主指令要求“AI 综合回答 → 大家都在怎么做”，卡片展示“参考帖子”。专业建议和社区经验明确分开，摘要只简短转述；单篇不描述为共识，0～2 篇不凑数，不把收藏量视为医学证据。外部文本均属不可信数据。既有紧急安全门优先；既有医学 response_policy 下，在执行器之前进一步拒绝社区工具调用。语义上的适用性、拒绝搜索和多轮风险仍需真实模型评审，规则测试不能证明模型可靠率。

## 验收证据与待完成项

- `tests/test_rednote.py`：排序、去重、降级、URL/输入验证、网络错误、授权配置、权限、真实 ToolExecutor 输出/事件一致性、重复调用、医学门。
- App `agent_rednote_posts_test.dart`：生产 artifact envelope、事件重放去重、3 篇上限、缺字段、图片失败、点击卡片/按钮外链、打开失败提示、320 大字与 390 视觉快照。
- `evals/behavior/v1/scenarios.json`：经验检索、紧急医疗、诊断、拒绝搜索、非母婴问题等场景。现有 runner 保留结构断言 + live judge/人工评分，不将 scripted model 或 validate-only 称为真实模型评审。
- PR 门禁：Python tests + ruff + mypy，Flutter analyze + affected tests，契约快照无漂移。真实授权源接入后还需实际关键词检索、0/1/2/3 篇、图片及收藏缺失、限流故障、原帖在目标海外地区/设备打开、授权撤回及留存验证，再运行真实模型质量门禁。

## 按原始目标核对

| 原始要求 | 当前证据 | 完成判定 |
| --- | --- | --- |
| 合法、稳定、可长期维护的数据源 | 官方能力调研；用户确认无授权渠道；规范化 Provider 可替换 | **未完成**：尚无获授权且验证可用的数据源，不可启用真实检索 |
| 适用问题触发、医疗安全优先 | 主指令、医学策略执行拦截、紧急安全门，行为目录 6 个新增场景 | 工程约束与 8 个合成来源真实模型场景已核对；真实来源与多轮安全 **待验收** |
| 搜索关键词、相关度/收藏/质量排序、Top 3 | 参数约束、20 候选上限、排序/质量门/去重/1～3 篇测试 | 确定性排序已验证；供应商相关度和内容质量 **待真实数据校准** |
| 提炼经验、专业建议与社区观点分开、不复制 | 主指令和 behavior rubric | 已修复笼统摘要下编造共同观点的问题，并核对真实模型抽样回答；真实帖子和独立医学质量 **待验收** |
| 标题/摘要/图/作者/收藏/原帖卡片及跳转 | 后端生成 schema/event 样例、App 消费测试、原帖 action dispatch、截图 | 代码和测试链路已验证；海外设备与真实原帖 **待联调** |
| 空结果、少结果、缺字段、检索异常不阻塞 | 服务、执行器和 Widget 测试；0～3 篇及异常均无虚构结果 | 自动化模拟测试已验证；真实供应商故障行为 **待联调** |
| 必要后端接口、Tool 注册、可替换数据源 | 原有 Run/SSE/replay API + RedNoteSearchService + eager Tool + artifact contract | 本地实现完成；真实供应商 adapter/配置与授权留存/撤回规则 **待确定** |

整体目标仍未完成，也未部署。下一步须先取得正式服务文档、授权条款及服务端凭证配置，再完成具体 adapter、真实检索与模型/海外设备验收；不得仅因本地测试通过将本表改为完成。

最新模型抽样、发现的问题与修正证据见 [2026-09-16 模型复核](evidence/rednote-model-review-20260916.md)。合成来源通过不等于数据源接入完成。
