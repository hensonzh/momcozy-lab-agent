# 模型服务商适配层

## 目标与边界

Runtime 通过 `agent.model_provider.v1` 对接模型服务商。当前实现支持
OpenAI Responses API 与 Azure OpenAI Responses v1；durable `AgentLoop`、账本、
Tool/Action、上下文构造和恢复语义不依赖具体服务商。

适配层只统一 Runtime 实际使用的语义，不承诺“任意 OpenAI-compatible URL 都能
工作”。新服务商必须显式声明并通过以下能力门禁：

- Responses 请求与流式事件；
- Function Tool 与 Structured Output；
- 当前启用工具需要的能力（`load_service_skill` 和 `read_topical_records`，不要求 Tool Search）；
- Runtime 所需的错误、超时和上下文预算语义。

缺少必需能力时 worker 在启动组合阶段失败，不允许静默降级、移除 Tool 或改变
上下文。只有启用 deferred tools 时，执行引擎才额外要求 server-side Tool Search。

采用**配置切换**：每个 worker 进程只创建一个 Provider，不增加动态路由、热切换
或自动故障转移。修改配置后重建 worker，Skill、业务工具、API 和客户端共用同一套实现。

## 代码结构

- `providers/contracts.py`：非敏感 `ModelProviderProfile`、唯一 provider identity
  快照、能力集合、请求策略和错误映射协议。
- `providers/runtime.py`：构造 `ProviderRuntimeBundle`，统一提供 client、SDK
  model、profile、请求策略、history adapter、token counter、compactor、错误映射器和资源关闭。
- `providers/errors.py`：把 SDK/HTTP 错误归一化为稳定 Runtime `ApiError`。
- `providers/openai_context.py`：Responses 上下文压缩、OpenAI 精确计数和 Azure
  保守估算。
- `orchestration/openai_agents.py`：通用 `ResponsesAgentsExecutionEngine`；旧的
  `OpenAIAgentsExecutionEngine` 名称仅保留为兼容别名。
- `workers/composition.py`：唯一选择服务商并把整个 bundle 注入 Runtime 的组合根。

业务 Tool、Action applicator 和 `AgentLoop` 不得读取 OpenAI/Azure 环境变量，也
不得按服务商分支。

## 接口差异

| 维度 | OpenAI Responses | Azure OpenAI Responses v1 |
| --- | --- | --- |
| Base URL | 默认 `https://api.openai.com/v1`，可显式配置兼容网关 | `https://<resource>.openai.azure.com/openai/v1/` 或对应 Foundry endpoint |
| `model` | OpenAI 模型 ID | Azure 部署名，不是模型 family |
| 身份认证 | API key | Microsoft Entra ID（推荐）或 Azure API key |
| 版本/地域 | 模型 ID 由 OpenAI 管理 | 必须另行记录模型 family、部署模型版本、region 和 deployment type |
| 输入 token 预检 | `/responses/input_tokens` 精确计数 | 当前 adapter 使用本地保守估算 |
| 显式 Prompt Cache | 当前 GPT-5.6 Runtime profile 启用 | 仅 GPT-5.6+ Standard profile 启用；PTU-M 禁止发送对应字段 |
| 错误元数据 | `x-request-id` 等 | `apim-request-id`、HTTP 状态和 `Retry-After` |

执行清单只写非敏感 profile：provider、API、base URL、部署名、模型 family/版本、
region、deployment type、auth mode、能力集合、SDK 版本和请求策略。API key、Bearer
token、Azure tenant/client secret 均不得进入清单、Replay 或日志。

同一份 `agent.model_provider.v1` identity 快照也写入 Context compaction job 和
checkpoint。job 的幂等键与 worker 兼容性检查同时绑定该快照，以及 token counter 的
名称、版本和模型；部署别名、模型版本或 counter 发生变化时，新 worker 不会静默处理
旧 job。完成后的 typed checkpoint 可以继续作为其他 provider 的历史输入，但会保留
生成它的原始 provider identity 供 Replay 和审计使用。

## 配置

staging 与 production 分别在私有 `env/staging.env`、`env/production.env` 中配置 provider；仓库中的同名 `.example` 文件只保存非秘密默认值。两个环境共用 `docker-compose.deploy.yml`。
不要在 `app/core/settings.py` 中硬编码服务商。

通用设置：

```dotenv
AGENT_MODEL_REASONING_EFFORT=low
AGENT_MODEL_TEXT_VERBOSITY=low
AGENT_MODEL_STORE=false
AGENT_MODEL_TIMEOUT_SECONDS=60
AGENT_MODEL_MAX_OUTPUT_TOKENS=8000
```

OpenAI：

```dotenv
AGENT_MODEL_PROVIDER=openai_responses
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-terra
```

自定义 `OPENAI_BASE_URL` 还必须设置
`OPENAI_RESPONSES_COMPATIBLE_BASE_URL=true`。这只是运维人员对兼容性的显式声明，
不能代替 provider contract 测试。

Azure OpenAI（Entra）：

```dotenv
AGENT_MODEL_PROVIDER=azure_openai_responses
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/openai/v1
AZURE_OPENAI_AUTH_MODE=entra
AZURE_OPENAI_DEPLOYMENT=<deployment-name>
AZURE_OPENAI_MODEL_FAMILY=gpt-5.6-terra
AZURE_OPENAI_MODEL_VERSION=<deployed-version>
AZURE_OPENAI_REGION=<region>
AZURE_OPENAI_DEPLOYMENT_TYPE=standard
AZURE_OPENAI_TOKEN_SCOPE=https://ai.azure.com/.default
AZURE_OPENAI_TOKEN_ESTIMATOR_SAFETY_FACTOR=1.25
```

Entra 使用 `DefaultAzureCredential`，生产 workload 必须拥有目标资源的数据面调用
权限。若选择 `AZURE_OPENAI_AUTH_MODE=api_key`，则提供
`AZURE_OPENAI_API_KEY`；Entra 模式禁止同时配置静态 key。
模型 family、精确部署版本、region 和 deployment type 没有代码默认值，选择 Azure
时必须显式提供；缺少任一字段，worker 在能力推导和创建 client 前失败。

## 上下文预算

OpenAI adapter 在每次完整模型请求前调用服务商的精确 input-token counter。
Azure adapter 对 canonical JSON 字节、item、Tool schema
进行保守估算，乘以可配置 safety factor，并为每个 opaque image/file 额外预留固定
预算。估算值用于主动压缩与前置拒绝，不伪装成服务商精确值；counter 名称、版本和
模型会写入 Context job、checkpoint 和状态。

服务端仍是上下文硬限制的最终权威。若 Azure 实际请求返回 context-window error，
现有 durable hard-limit 流程会触发一次 checkpoint 恢复；受保护的最近 5 个完整
Run 或当前附件仍无法放入时，统一返回 `recent_context_exceeds_limit`，不截断输入。

## 稳定错误契约

适配器至少归一化以下错误：

- `model_provider_timeout`：可重试；
- `model_provider_unavailable`：连接或上游 5xx，可重试；
- `model_rate_limited`：429，可重试；`Retry-After` 同时保留为 HTTP header 和有界
  `retry_after` 失败详情；
- `model_auth_failed`：认证/授权失败，不可重试；
- `model_content_filtered`：内容策略拒绝，不可重试；
- `model_context_window_exceeded`：交给 durable context recovery；
- `model_provider_error`：其余被服务商拒绝的请求。

同一 error mapper 用于主 Agent 模型调用、typed compaction 和远程 token counter；
Context 自身的超时、格式与来源校验仍保留 `context_*` 错误码。日志只使用低基数
provider/model 维度。上游 request ID、状态码和 `retry_after` 可以进入错误详情用于
排障，并以有界白名单同时写入失败 Run 与 `run.failed` 事件；不得把响应正文、异常消息
或用户内容写入日志或账本。该提示不自动重试整个 Run，客户端按产品策略决定何时重提。

## 发布门禁与切换

切换服务商前必须在 test 使用与生产相同的 region、deployment type、模型版本和
认证方式完成：

1. provider/settings/error/token-counter 的确定性测试；
2. Agents SDK 请求投影测试，确认 Function Tool、Structured Output、流式 delta、
   encrypted reasoning 和 cache 字段符合 profile；
3. 两端各完成真实短对话、`load_service_skill` round trip 和 typed context compaction；
   同一会话分别验证 OpenAI → Azure、Azure → OpenAI 续聊（含已压缩历史）；
4. 429、无效身份、content filter、超长上下文和 worker 重启恢复演练；
5. behavior eval 与 Replay/manifest 审核，确认没有凭据和正文泄露；
6. 对 Azure 估算值与真实响应 `usage.input_tokens` 做采样比较，必要时只上调 safety
   factor。

### 历史续聊

历史来源是内部账本，不使用 `previous_response_id` 或服务商会话作为持久化来源。
上下文模块提供账本历史和不透明的执行清单，只依赖 `HistoryInputAdapter` 接口。
`ProviderRuntimeBundle` 提供适配器，由 worker 组合根注入；上下文模块不导入具体
Provider 实现，也不解析服务商身份、加密推理或专属响应格式。
`providers/history.py` 的 Responses 适配器根据历史 Run 的执行清单选择请求投影：

- 同一 provider、endpoint、model/deployment 和模型版本：保留原始 reasoning 等输出项。
- 切换上述边界，或旧 Run 缺少可验证清单：不回放加密 reasoning，移除消息和函数调用的
  服务商 item `id` / `status`；保留文本、客户端上下文、Skill developer 消息、函数参数、
  结果和配对用的 `call_id`。assistant output text/refusal 转为可移植的 input text，
  不携带输出注解；消息的角色、顺序及 assistant phase 不变。
- 不支持跨服务商直接迁移的 item reference / 原生 compaction / hosted tool 项明确报
  `model_history_not_portable`，不能悄悄丢弃内容。当前业务只产生消息、reasoning 和函数工具项。
- 投影同样用于历史 token 预检，避免先把外部加密项送到 OpenAI counter 才报错。
- 原始账本、摘要来源哈希、Skill 所属 Run 和压缩范围均不改变；已完成的 typed checkpoint
  可复用，未完成的压缩 job 仍要求原 provider/config 处理。

这是本项目的保守兼容策略，不代表官方保证加密 reasoning 能在两种服务之间互用。
参考：[OpenAI 自行管理会话状态](https://developers.openai.com/api/docs/guides/conversation-state#manually-manage-conversation-state)、
[Azure Responses](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses)。

### 切换操作

1. 在入口网关暂停接收新的 `POST /v1/agent/runs`（返回临时不可用），保留查询、SSE、取消
   和已有确认操作。覆盖所有 API 实例和内部调用入口，并等待已进入创建流程的请求结束。
   worker 继续使用旧配置运行。若不能封闭全部创建入口，则停止 API，保留旧 worker 排空。
2. 执行只读检查（在 agent 仓库根目录，使用目标环境的数据库配置）：

   ```bash
   python -m scripts.check_model_provider_switch
   ```

   Compose 中旧 worker 仍运行时可执行：

   ```bash
   MOMCOZY_AGENT_ENV_FILE=env/staging.env docker compose --env-file env/staging.env -f docker-compose.deploy.yml exec -T worker python -m scripts.check_model_provider_switch
   ```

   退出码 0 表示当前数据库快照已排空；退出码 1 表示仍有阻塞项。输出只有数量，不包含
   对话、凭据或用户资料。检查包括 `queued/running/waiting_for_confirmation` Run、
   `queued/running/retry_wait` 压缩任务，以及 `compacting/blocked` 上下文 head。
   **这个命令不关闭入口、不取消任务，也不能在入口开放时保证之后没有新任务。**
3. 保持旧 worker 工作到检查通过。等待确认的 Run 要完成确认、通过现有取消接口取消，或
   按既有超时策略结束；失败/死信压缩要按 context recovery 流程恢复到 ready。
   不得直接清空队列、改数据库状态或删除旧任务来伪造排空。
4. 优雅停止全部旧 worker，入口保持关闭；再次运行只读检查。旧 worker 已停止时用
   `docker compose ... run --rm --no-deps worker python -m scripts.check_model_provider_switch`
   启动一次性检查容器（`...` 使用上面的 env-file 和 compose 文件参数）。
   若进程被强制终止且检查未通过，先用原配置恢复任务，不得立即启动新 Provider。
5. 修改私有环境文件中的 `AGENT_MODEL_PROVIDER` 和对应配置，再创建新 worker。Compose
   需 `up -d --no-deps --force-recreate worker`；仅 `restart` 不会刷新容器环境变量。
   新旧配置的 worker 不得同时消费同一队列。
6. 检查新 worker 健康、执行清单的 provider/deployment，并使用受控测试入口验证老会话
   续聊及 Skill 加载。通过后恢复新 Run 入口。若失败，保持入口关闭，处理新 Provider
   已产生的 Run/job，再按同样排空流程回滚；不能带着新任务直接换回旧 worker。

### 本地回归与真实环境验收

```bash
.venv/bin/python -m pytest -q tests/test_provider_switch.py tests/test_provider_runtime.py tests/test_openai_agents_execution.py tests/test_context_pipeline_v1.py tests/test_context_completed_run_tail.py
```

上述回归验证真实 SDK 请求构造、双向历史投影、Skill developer 消息、工具配对、旧摘要
复用、原始账本不变和排空检查。模型响应和数据库由测试替身提供，不能替代真实服务验收。
真实环境还须按本节门禁记录两端 provider/deployment、Run ID、压缩 job ID 和验证结果。
未提供有效 Azure 部署、认证和网络环境时，真实 Azure 兼容性应标为“未验证”。

## 接入第三方服务商

第三个服务商需要实现/组装同一个 `ProviderRuntimeBundle`，并新增独立 profile、
认证、错误映射和 token 预算策略。只有协议形状相似不够：实际启用的工具、Structured
Output、流事件、缓存字段、错误分类和模型版本语义都必须通过 contract tests 与真实
test smoke。若服务商缺少 Runtime 必需能力，应新增明确的产品/Runtime 方案，
而不是在 adapter 中偷偷模拟或删除能力。
