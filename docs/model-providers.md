# 模型服务商适配层

## 目标与边界

Runtime 通过 `agent.model_provider.v1` 对接模型服务商。当前实现支持
OpenAI Responses API 与 Azure OpenAI Responses v1；durable `AgentLoop`、账本、
Tool/Action、上下文构造和恢复语义不依赖具体服务商。

适配层只统一 Runtime 实际使用的语义，不承诺“任意 OpenAI-compatible URL 都能
工作”。新服务商必须显式声明并通过以下能力门禁：

- Responses 请求与流式事件；
- Function Tool 与 Structured Output；
- server-side Tool Search；
- Runtime 所需的错误、超时和上下文预算语义。

缺少必需能力时 worker 在启动组合阶段失败，不允许静默降级、移除 Tool 或改变
上下文。

## 代码结构

- `providers/contracts.py`：非敏感 `ModelProviderProfile`、能力集合、请求策略和
  错误映射协议。
- `providers/runtime.py`：构造 `ProviderRuntimeBundle`，统一提供 client、SDK
  model、profile、请求策略、token counter、compactor、错误映射器和资源关闭。
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
| 输入 token 预检 | `/responses/input_tokens` 精确计数 | 当前没有该路由，使用本地保守估算 |
| 显式 Prompt Cache | 当前 GPT-5.6 Runtime profile 启用 | 仅 GPT-5.6+ Standard profile 启用；PTU-M 禁止发送对应字段 |
| 错误元数据 | `x-request-id` 等 | `apim-request-id`、HTTP 状态和 `Retry-After` |

执行清单只写非敏感 profile：provider、API、base URL、部署名、模型 family/版本、
region、deployment type、auth mode、能力集合、SDK 版本和请求策略。API key、Bearer
token、Azure tenant/client secret 均不得进入清单、Replay 或日志。

## 配置

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

## 上下文预算

OpenAI adapter 在每次完整模型请求前调用服务商的精确 input-token counter。
Azure v1 当前没有相同路由，因此 adapter 对 canonical JSON 字节、item、Tool schema
进行保守估算，乘以可配置 safety factor，并为每个 opaque image/file 额外预留固定
预算。估算值用于主动压缩与前置拒绝，不伪装成服务商精确值；counter 名称会写入
Context job 和状态。

服务端仍是上下文硬限制的最终权威。若 Azure 实际请求返回 context-window error，
现有 durable hard-limit 流程会触发一次 checkpoint 恢复；受保护的最近 5 个完整
Run 或当前附件仍无法放入时，统一返回 `recent_context_exceeds_limit`，不截断输入。

## 稳定错误契约

适配器至少归一化以下错误：

- `model_provider_timeout`：可重试；
- `model_provider_unavailable`：连接或上游 5xx，可重试；
- `model_rate_limited`：429，可重试并透传 `Retry-After`；
- `model_auth_failed`：认证/授权失败，不可重试；
- `model_content_filtered`：内容策略拒绝，不可重试；
- `model_context_window_exceeded`：交给 durable context recovery；
- `model_provider_error`：其余被服务商拒绝的请求。

日志只使用低基数 provider/model 维度。上游 request ID 可以进入错误详情用于排障，
并以有界白名单写入失败 Run；不得把响应正文、异常消息或用户内容写入日志或账本。

## 发布门禁与切换

切换服务商前必须在 staging 使用与生产相同的 region、deployment type、模型版本和
认证方式完成：

1. provider/settings/error/token-counter 的确定性测试；
2. Agents SDK 请求投影测试，确认 Tool Search、Structured Output、流式 delta、
   encrypted reasoning 和 cache 字段符合 profile；
3. 一条真实短对话、一次 deferred Tool Search、一次 Function Tool round trip、
   一次 typed context compaction；
4. 429、无效身份、content filter、超长上下文和 worker 重启恢复演练；
5. behavior eval 与 Replay/manifest 审核，确认没有凭据和正文泄露；
6. 对 Azure 估算值与真实响应 `usage.input_tokens` 做采样比较，必要时只上调 safety
   factor。

切换只改变 worker 的 provider 配置；API、数据库 schema 和客户端契约不变。先停止
新 Run、排空运行中 Run 和 Context job，再滚动替换 worker。回滚时使用相同步骤切回
原 provider；不要让两个 provider 同时处理同一个 durable queue。

## 接入第三方服务商

第三个服务商需要实现/组装同一个 `ProviderRuntimeBundle`，并新增独立 profile、
认证、错误映射和 token 预算策略。只有协议形状相似不够：Tool Search、Structured
Output、流事件、缓存字段、错误分类和模型版本语义都必须通过 contract tests 与真实
staging smoke。若服务商缺少 Runtime 必需能力，应新增明确的产品/Runtime 方案，
而不是在 adapter 中偷偷模拟或删除能力。
