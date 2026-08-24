from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from importlib import metadata
from time import monotonic
from typing import Any, cast

from agents import (
    Agent,
    FunctionTool,
    ModelSettings,
    RunConfig,
    RunHooks,
    Runner,
    ToolSearchTool,
    ToolExecutionConfig,
    tool_namespace,
)
from agents.agent_output import AgentOutputSchemaBase
from agents.exceptions import (
    AgentsException,
    MaxTurnsExceeded,
    ModelBehaviorError,
)
from agents.handoffs import Handoff
from agents.items import (
    ModelResponse,
    TResponseInputItem,
    TResponseStreamEvent,
)
from agents.models.interface import Model, ModelTracing
from agents.models.openai_responses import Converter
from agents.retry import ModelRetryAdvice, ModelRetryAdviceRequest
from agents.run_config import CallModelData, ModelInputData
from agents.run_context import RunContextWrapper
from agents.stream_events import RawResponsesStreamEvent
from agents.tool import Tool
from agents.tool_context import ToolContext
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseTextDeltaEvent,
)
from openai.types.responses.response_prompt_param import (
    ResponsePromptParam,
)
from app.agent_runtime.tools import ToolContractRegistry
from app.core.errors import ApiError
from app.agent_runtime.providers.contracts import (
    ModelProviderErrorMapper,
    ModelProviderProfile,
    ModelRequestPolicy,
    REQUIRED_RUNTIME_PROVIDER_CAPABILITIES,
    openai_responses_profile,
)
from app.agent_runtime.providers.errors import OpenAICompatibleErrorMapper
from app.agent_runtime.runtime_metadata import (
    ACTION_POLICY_SCHEMA_VERSION,
    MODEL_CONTEXT_SCHEMA_VERSION,
    MODEL_EXECUTION_MANIFEST_SCHEMA_VERSION,
    MODEL_PROVIDER_CONTRACT_VERSION,
    TOOL_CONTRACT_SCHEMA_VERSION,
    runtime_metadata_snapshot,
    validate_runtime_contract_catalog_snapshot,
)
from app.core.observability import emit_operation_metric

from .contracts import (
    AgentDefinition,
    AgentExecutionPort,
    AgentExecutionResult,
    RuntimeDefinition,
)


PROMPT_CACHE_BREAKPOINT = {"mode": "explicit"}
MODEL_LOGGER = logging.getLogger("agent_runtime.model")


class _ToolInvocationError(AgentsException):
    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


class _ModelCallTimeoutError(AgentsException):
    pass


class _TotalTimeoutModel(Model):
    """Bound one complete SDK model request, including stream consumption."""

    def __init__(
        self,
        *,
        inner_model: Model,
        timeout_seconds: float,
    ) -> None:
        self.inner_model = inner_model
        self.timeout_seconds = timeout_seconds

    async def _cleanup_on_run_end(self, owner: object) -> None:
        await self.inner_model._cleanup_on_run_end(owner)

    async def close(self) -> None:
        await self.inner_model.close()

    def get_retry_advice(
        self,
        request: ModelRetryAdviceRequest,
    ) -> ModelRetryAdvice | None:
        return self.inner_model.get_retry_advice(request)

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self.inner_model.get_response(
                    system_instructions,
                    input,
                    model_settings,
                    tools,
                    output_schema,
                    handoffs,
                    tracing,
                    previous_response_id=previous_response_id,
                    conversation_id=conversation_id,
                    prompt=prompt,
                )
        except TimeoutError as exc:
            raise _ModelCallTimeoutError(
                "Agents SDK model call exceeded its total timeout."
            ) from exc

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async for event in self.inner_model.stream_response(
                    system_instructions,
                    input,
                    model_settings,
                    tools,
                    output_schema,
                    handoffs,
                    tracing,
                    previous_response_id=previous_response_id,
                    conversation_id=conversation_id,
                    prompt=prompt,
                ):
                    yield event
        except TimeoutError as exc:
            raise _ModelCallTimeoutError(
                "Agents SDK streamed model call exceeded its total timeout."
            ) from exc


@dataclass
class _ExecutionState:
    port: AgentExecutionPort
    runtime: RuntimeDefinition
    model_name: str
    runtime_context: dict[str, Any]
    observation_context: dict[str, str]
    prompt_cache_breakpoints: bool


class ResponsesAgentsExecutionEngine:
    """Agents SDK execution engine behind the durable Runtime boundary."""

    def __init__(
        self,
        *,
        model: Model,
        model_name: str,
        tool_registry: ToolContractRegistry,
        runtime: RuntimeDefinition,
        runtime_contract_catalog: Mapping[str, Any],
        max_turns: int = 10,
        max_output_tokens: int = 8_000,
        reasoning_effort: str = "low",
        text_verbosity: str = "low",
        store: bool = False,
        base_url: str = "",
        timeout_seconds: float = 60,
        provider_profile: ModelProviderProfile | None = None,
        request_policy: ModelRequestPolicy | None = None,
        provider_error_mapper: ModelProviderErrorMapper | None = None,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.model = model
        self.model_name = model_name
        self.tool_registry = tool_registry
        self.runtime = runtime
        self.runtime_contract_catalog = (
            validate_runtime_contract_catalog_snapshot(
                runtime_contract_catalog
            )
        )
        catalog_tool_items = self.runtime_contract_catalog["tools"]["items"]
        registered_tool_items = sorted(
            (
                contract.catalog_item()
                for contract in tool_registry.list()
            ),
            key=lambda item: str(item["name"]),
        )
        if catalog_tool_items != registered_tool_items:
            raise ValueError(
                "runtime contract catalog does not match Tool registry"
            )
        self.max_turns = max_turns
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort
        self.text_verbosity = text_verbosity
        self.store = store
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.provider_profile = provider_profile or openai_responses_profile(
            model=model_name,
            base_url=base_url,
        )
        self.provider_profile.require_capabilities(
            REQUIRED_RUNTIME_PROVIDER_CAPABILITIES
        )
        if self.provider_profile.model != model_name:
            raise ValueError("provider profile model does not match engine model")
        if self.provider_profile.api != "responses":
            raise ValueError(
                "Agents execution requires a Responses API provider"
            )
        if self.provider_profile.base_url.rstrip("/") != base_url.rstrip("/"):
            raise ValueError(
                "provider profile base URL does not match engine base URL"
            )
        self.request_policy = request_policy or ModelRequestPolicy.for_profile(
            self.provider_profile
        )
        self.provider_error_mapper = (
            provider_error_mapper
            or OpenAICompatibleErrorMapper(self.provider_profile)
        )

    async def execute(
        self,
        *,
        input_items: tuple[dict[str, Any], ...],
        port: AgentExecutionPort,
        authorization_permissions: frozenset[str],
        runtime_context: dict[str, Any] | None = None,
        observation_context: dict[str, str] | None = None,
    ) -> AgentExecutionResult:
        _validate_stateless_function_context(input_items)
        state = _ExecutionState(
            port=port,
            runtime=self.runtime,
            model_name=self.model_name,
            runtime_context=deepcopy(runtime_context or {}),
            observation_context=dict(observation_context or {}),
            prompt_cache_breakpoints=(
                self.request_policy.prompt_cache_breakpoints
            ),
        )
        hooks = _DurableRunHooks(engine=self, state=state)
        agent = self._build_agent(
            authorization_permissions=authorization_permissions
        )
        run_config = RunConfig(
            tracing_disabled=True,
            trace_include_sensitive_data=False,
            workflow_name="momcozy-agent-runtime",
            call_model_input_filter=self._model_input_filter,
            tool_execution=ToolExecutionConfig(
                max_function_tool_concurrency=1,
            ),
        )
        try:
            result = Runner.run_streamed(
                starting_agent=agent,
                input=cast(
                    list[TResponseInputItem],
                    [deepcopy(item) for item in input_items],
                ),
                context=state,
                max_turns=self.max_turns,
                hooks=hooks,
                run_config=run_config,
            )
            async for event in result.stream_events():
                if (
                    isinstance(event, RawResponsesStreamEvent)
                    and isinstance(
                        event.data,
                        ResponseTextDeltaEvent,
                    )
                    and event.data.delta
                ):
                    await port.publish_text_delta(
                        agent_name=self.runtime.agent.name,
                        delta=event.data.delta,
                    )
            run_loop_error = result.run_loop_exception
            if run_loop_error is not None:
                raise run_loop_error
            text = _final_text(result.final_output)
            if not text:
                raise ApiError(
                    code="model_empty_response",
                    message=(
                        "Model returned no answer or function call."
                    ),
                    status=502,
                )
            return AgentExecutionResult(
                text=text,
                agent=self.runtime.agent.name,
            )
        except ApiError as exc:
            hooks.emit_pending_model_failures(error_code=exc.code)
            raise
        except _ToolInvocationError as exc:
            hooks.emit_pending_model_failures(
                error_code="tool_execution_failed"
            )
            raise exc.cause from exc
        except _ModelCallTimeoutError as exc:
            hooks.emit_pending_model_failures(
                error_code="model_provider_timeout",
                outcome="timeout",
            )
            raise ApiError(
                code="model_provider_timeout",
                message="Model provider request timed out.",
                status=504,
                details={"retryable": True},
            ) from exc
        except MaxTurnsExceeded as exc:
            hooks.emit_pending_model_failures(
                error_code="agent_max_turns_exceeded"
            )
            raise ApiError(
                code="agent_max_turns_exceeded",
                message="Agent exceeded its tool-call turn limit.",
                status=504,
            ) from exc
        except ModelBehaviorError as exc:
            hooks.emit_pending_model_failures(
                error_code="model_provider_malformed_tool_call"
            )
            raise _model_behavior_error(exc) from exc
        except Exception as exc:
            mapped = self.provider_error_mapper.map(exc)
            if mapped is None:
                hooks.emit_pending_model_failures(
                    error_code="model_execution_failed"
                )
                raise
            hooks.emit_pending_model_failures(
                error_code=mapped.code,
                outcome=(
                    "timeout"
                    if mapped.code == "model_provider_timeout"
                    else "error"
                ),
            )
            raise mapped from exc
        except BaseException:
            hooks.emit_pending_model_failures(
                error_code="model_execution_interrupted"
            )
            raise

    def _build_agent(
        self,
        *,
        authorization_permissions: frozenset[str],
    ) -> Agent[_ExecutionState]:
        definition = self.runtime.agent
        return self._agent(
            definition=definition,
            tools=self._agent_tools(
                definition,
                authorization_permissions=authorization_permissions,
            ),
        )

    def _agent(
        self,
        *,
        definition: AgentDefinition,
        tools: list[Tool],
    ) -> Agent[_ExecutionState]:
        return Agent(
            name=definition.name,
            instructions=definition.instructions,
            tools=tools,
            model=_TotalTimeoutModel(
                inner_model=self.model,
                timeout_seconds=self.timeout_seconds,
            ),
            model_settings=ModelSettings(
                parallel_tool_calls=False,
                truncation="disabled",
                max_tokens=self.max_output_tokens,
                reasoning={"effort": self.reasoning_effort},
                verbosity=cast(Any, self.text_verbosity),
                store=self.store,
                response_include=(
                    ["reasoning.encrypted_content"]
                    if (
                        not self.store
                        and self.request_policy.include_encrypted_reasoning
                    )
                    else None
                ),
                prompt_cache_options=cast(
                    Any,
                    (
                        dict(self.request_policy.prompt_cache_options)
                        if self.request_policy.prompt_cache_options
                        else None
                    ),
                ),
            ),
            tool_use_behavior="run_llm_again",
        )

    def _agent_tools(
        self,
        definition: AgentDefinition,
        *,
        authorization_permissions: frozenset[str],
    ) -> list[Tool]:
        contracts = {
            contract.name: contract
            for contract in self.tool_registry.list()
            if contract.name in self.runtime.tools.tool_names
        }
        missing = set(self.runtime.tools.tool_names) - contracts.keys()
        if missing:
            raise ValueError(
                f"tool catalog references unknown tools: {sorted(missing)}"
            )

        authorized_contracts = {
            name: contract
            for name, contract in contracts.items()
            if set(contract.required_permissions)
            <= authorization_permissions
        }

        deferred = self.runtime.tools.deferred_tool_names & set(
            authorized_contracts
        )
        tools: list[Tool] = [
            self._business_tool(
                agent_name=definition.name,
                contract=authorized_contracts[tool_name],
                defer_loading=False,
            )
            for tool_name in self.runtime.tools.eager_tool_names
            if (
                tool_name not in deferred
                and tool_name in authorized_contracts
            )
        ]
        if deferred:
            tools.append(
                ToolSearchTool(
                    description=(
                        "按当前任务搜索并加载最相关的业务工具或工具 namespace。"
                    ),
                    execution="server",
                )
            )
        for namespace in self.runtime.tools.tool_namespaces:
            namespaced = [
                self._business_tool(
                    agent_name=definition.name,
                    contract=authorized_contracts[tool_name],
                    defer_loading=True,
                )
                for tool_name in namespace.tool_names
                if tool_name in authorized_contracts
            ]
            if not namespaced:
                continue
            tools.extend(
                tool_namespace(
                    name=namespace.name,
                    description=namespace.description,
                    tools=namespaced,
                )
            )
        return tools

    @staticmethod
    def _business_tool(
        *,
        agent_name: str,
        contract: Any,
        defer_loading: bool,
    ) -> FunctionTool:
        async def invoke(
            context: ToolContext[_ExecutionState],
            input_json: str,
        ) -> Any:
            try:
                arguments = json.loads(input_json or "{}")
            except json.JSONDecodeError as exc:
                raise ModelBehaviorError(
                    f"Invalid JSON input for tool {contract.name}."
                ) from exc
            if not isinstance(arguments, dict):
                raise ModelBehaviorError(
                    f"Tool {contract.name} input must be an object."
                )
            try:
                return await context.context.port.invoke_tool(
                    agent_name=agent_name,
                    tool_name=contract.name,
                    call_id=context.tool_call_id,
                    arguments=dict(arguments),
                )
            except Exception as exc:
                raise _ToolInvocationError(exc) from exc

        return FunctionTool(
            name=contract.name,
            description=contract.description,
            params_json_schema=deepcopy(contract.input_schema),
            on_invoke_tool=invoke,
            strict_json_schema=False,
            timeout_seconds=None,
            defer_loading=defer_loading,
        )

    @staticmethod
    async def _model_input_filter(
        data: CallModelData[_ExecutionState],
    ) -> ModelInputData:
        raw_items = tuple(
            _json_item(item)
            for item in data.model_data.input
        )
        state = data.context
        if state is None:
            raise RuntimeError(
                "Agents SDK execution context is unavailable."
            )
        resolved = await state.port.resolve_model_input(
            input_items=raw_items,
        )
        if _contains_internal_asset_reference(resolved):
            raise ApiError(
                code="model_asset_unresolved",
                message=(
                    "Internal Agent assets must be resolved before "
                    "model input."
                ),
                status=503,
            )
        instructions = data.model_data.instructions or ""
        input_items = cast(
            list[TResponseInputItem],
            [
                _stable_prefix_item(
                    instructions,
                    prompt_cache_breakpoints=(
                        state.prompt_cache_breakpoints
                    ),
                ),
                *(deepcopy(item) for item in resolved),
            ],
        )
        converted_tools = Converter.convert_tools(
            list(data.agent.tools),
            [],
            model=state.model_name,
            tool_choice=data.agent.model_settings.tool_choice,
        ).tools
        ensure_fits = getattr(
            state.port,
            "ensure_model_request_fits",
            None,
        )
        if callable(ensure_fits):
            await ensure_fits(
                input_items=tuple(
                    _json_item(item) for item in input_items
                ),
                tools=tuple(
                    deepcopy(cast(dict[str, Any], tool))
                    for tool in converted_tools
                ),
            )
        return ModelInputData(
            input=input_items,
            instructions=None,
        )


class _DurableRunHooks(RunHooks[_ExecutionState]):
    def __init__(
        self,
        *,
        engine: ResponsesAgentsExecutionEngine,
        state: _ExecutionState,
    ) -> None:
        self.engine = engine
        self.state = state
        self._model_calls: list[
            tuple[str, float]
        ] = []

    async def on_llm_start(
        self,
        context: RunContextWrapper[_ExecutionState],
        agent: Agent[_ExecutionState],
        system_prompt: str | None,
        input_items: list[TResponseInputItem],
    ) -> None:
        self._model_calls.append(
            (agent.name, monotonic())
        )
        manifest = _execution_manifest(
            agent_name=agent.name,
            instructions=self.state.runtime.agent.instructions,
            input_items=tuple(
                _json_item(item) for item in input_items
            ),
            tools=tuple(agent.tools),
            model_name=self.engine.model_name,
            max_output_tokens=self.engine.max_output_tokens,
            reasoning_effort=self.engine.reasoning_effort,
            text_verbosity=self.engine.text_verbosity,
            parallel_tool_calls=(
                agent.model_settings.parallel_tool_calls is True
            ),
            store=self.engine.store,
            provider_profile=self.engine.provider_profile,
            request_policy=self.engine.request_policy,
            timeout_seconds=self.engine.timeout_seconds,
            runtime_context=self.state.runtime_context,
            runtime_contract_catalog=(
                self.engine.runtime_contract_catalog
            ),
        )
        await self.state.port.record_execution_manifest(
            manifest=manifest
        )

    async def on_llm_end(
        self,
        context: RunContextWrapper[_ExecutionState],
        agent: Agent[_ExecutionState],
        response: ModelResponse,
    ) -> None:
        calls = tuple(
            item
            for item in response.output
            if isinstance(item, ResponseFunctionToolCall)
        )
        self._validate_calls(agent=agent, calls=calls)
        has_calls = bool(calls)
        output_items = tuple(
            _json_item(item)
            for item in response.output
            if (
                getattr(item, "type", "") != "message"
                or has_calls
            )
        )
        await self.state.port.persist_model_output(
            agent_name=agent.name,
            response_id=response.response_id or "",
            output_items=output_items,
        )
        self._emit_model_metric(
            agent_name=agent.name,
            outcome="success",
        )

    def _validate_calls(
        self,
        *,
        agent: Agent[_ExecutionState],
        calls: tuple[ResponseFunctionToolCall, ...],
    ) -> None:
        if not calls:
            return
        available = {
            tool.name
            for tool in agent.tools
            if isinstance(tool, FunctionTool)
        }
        unknown = [
            call.name for call in calls if call.name not in available
        ]
        if unknown:
            raise ApiError(
                code="tool_not_available",
                message="Requested tool is not in the runtime catalog.",
                status=502,
                details={
                    "agent_name": agent.name,
                    "tool_name": unknown[0],
                },
            )
    def emit_pending_model_failures(
        self,
        *,
        error_code: str,
        outcome: str = "error",
    ) -> None:
        while self._model_calls:
            agent_name, started_at = (
                self._model_calls.pop(0)
            )
            self._emit_model_metric_at(
                agent_name=agent_name,
                started_at=started_at,
                outcome=outcome,
                error_code=error_code,
            )

    def _emit_model_metric(
        self,
        *,
        agent_name: str,
        outcome: str,
        error_code: str = "",
    ) -> None:
        match_index = next(
            (
                index
                for index, (candidate_agent, _)
                in enumerate(self._model_calls)
                if candidate_agent == agent_name
            ),
            None,
        )
        if match_index is None:
            return
        _agent, started_at = self._model_calls.pop(
            match_index
        )
        self._emit_model_metric_at(
            agent_name=agent_name,
            started_at=started_at,
            outcome=outcome,
            error_code=error_code,
        )

    def _emit_model_metric_at(
        self,
        *,
        agent_name: str,
        started_at: float,
        outcome: str,
        error_code: str,
    ) -> None:
        dimensions: dict[str, Any] = {
            "provider": self.engine.provider_profile.provider_id,
            "model": self.engine.model_name,
            "agent_name": agent_name,
            **self.state.observation_context,
        }
        emit_operation_metric(
            MODEL_LOGGER,
            metric_name="agent_runtime_model",
            operation="model.respond",
            outcome=outcome,
            started_at=started_at,
            dimensions=dimensions,
            error_code=error_code,
            level=(
                logging.INFO
                if outcome == "success"
                else logging.WARNING
            ),
        )


def _stable_prefix_item(
    instructions: str,
    *,
    prompt_cache_breakpoints: bool,
) -> dict[str, Any]:
    content: dict[str, Any] = {
        "type": "input_text",
        "text": instructions,
    }
    if prompt_cache_breakpoints:
        content["prompt_cache_breakpoint"] = dict(
            PROMPT_CACHE_BREAKPOINT
        )
    return {
        "type": "message",
        "role": "developer",
        "content": [content],
    }


def _execution_manifest(
    *,
    agent_name: str,
    instructions: str,
    input_items: tuple[dict[str, Any], ...],
    tools: tuple[Tool, ...],
    model_name: str,
    max_output_tokens: int,
    reasoning_effort: str,
    text_verbosity: str,
    parallel_tool_calls: bool,
    store: bool,
    provider_profile: ModelProviderProfile,
    request_policy: ModelRequestPolicy,
    timeout_seconds: float,
    runtime_context: dict[str, Any],
    runtime_contract_catalog: Mapping[str, Any],
) -> dict[str, Any]:
    tool_items = [
        {
            "name": tool.name,
            "description_sha256": _sha256_text(
                getattr(tool, "description", "") or ""
            ),
            "input_schema": deepcopy(
                getattr(tool, "params_json_schema", {})
            ),
            "input_schema_sha256": _sha256_json(
                getattr(tool, "params_json_schema", {})
            ),
            "defer_loading": bool(
                getattr(tool, "defer_loading", False)
            ),
            "namespace": getattr(tool, "_tool_namespace", None),
            "namespace_description_sha256": (
                _sha256_text(
                    getattr(
                        tool,
                        "_tool_namespace_description",
                        "",
                    )
                    or ""
                )
                if getattr(tool, "_tool_namespace", None)
                else None
            ),
        }
        for tool in tools
        if isinstance(tool, FunctionTool)
    ]
    has_tool_search = any(
        isinstance(tool, ToolSearchTool) for tool in tools
    )
    request_projection = {
        "instructions_sha256": _sha256_text(instructions),
        "input": input_items,
        "tools": tool_items,
        "tool_search": has_tool_search,
        "model": model_name,
        "max_output_tokens": max_output_tokens,
        "reasoning_effort": reasoning_effort,
        "text_verbosity": text_verbosity,
        "parallel_tool_calls": parallel_tool_calls,
        "store": store,
        "provider": provider_profile.provider_id,
        "include_encrypted_reasoning": (
            request_policy.include_encrypted_reasoning
        ),
        "prompt_cache_options": request_policy.prompt_cache_options,
    }
    manifest: dict[str, Any] = {
        "schema_version": (
            MODEL_EXECUTION_MANIFEST_SCHEMA_VERSION
        ),
        "agent_name": agent_name,
        "prompt": {
            "id": agent_name,
            "sha256": _sha256_text(instructions),
            "utf8_bytes": len(instructions.encode("utf-8")),
        },
        "runtime_metadata": runtime_metadata_snapshot(),
        "contract_catalog": {
            "schema_version": runtime_contract_catalog[
                "schema_version"
            ],
            "sha256": runtime_contract_catalog["catalog_sha256"],
        },
        "tools": {
            "contract_schema_version": TOOL_CONTRACT_SCHEMA_VERSION,
            "catalog_schema_version": runtime_contract_catalog["tools"][
                "schema_version"
            ],
            "catalog_sha256": runtime_contract_catalog["tools"]["sha256"],
            "items": tool_items,
            "tool_search": has_tool_search,
            "sha256": _sha256_json(tool_items),
        },
        "actions": {
            "policy_schema_version": ACTION_POLICY_SCHEMA_VERSION,
            "catalog_schema_version": runtime_contract_catalog["actions"][
                "schema_version"
            ],
            "catalog_sha256": runtime_contract_catalog["actions"][
                "sha256"
            ],
        },
        "model": {
            **provider_profile.manifest_metadata(),
            "contract_version": MODEL_PROVIDER_CONTRACT_VERSION,
            "execution_engine": "openai_agents_sdk",
            "sdk_package": "openai-agents",
            "sdk_version": _package_version("openai-agents"),
            "openai_sdk_version": _package_version("openai"),
            "model": model_name,
            "max_output_tokens": max_output_tokens,
            "reasoning_effort": reasoning_effort,
            "text_verbosity": text_verbosity,
            "parallel_tool_calls": parallel_tool_calls,
            "max_function_tool_concurrency": 1,
            "store": store,
            "truncation": "disabled",
            "include": (
                ["reasoning.encrypted_content"]
                if (
                    not store
                    and request_policy.include_encrypted_reasoning
                )
                else None
            ),
            "prompt_cache": (
                {
                    **dict(request_policy.prompt_cache_options or {}),
                    "breakpoint": (
                        "stable_tools_and_developer_instructions"
                    ),
                }
                if request_policy.prompt_cache_breakpoints
                else None
            ),
            "timeout_seconds": timeout_seconds,
            "timeout_scope": "per_model_call_wall_clock",
        },
        "context": {
            "schema_version": MODEL_CONTEXT_SCHEMA_VERSION,
            "resolved": _context_projection(input_items),
        },
        "request_payload_sha256": _sha256_json(
            request_projection
        ),
    }
    if runtime_context:
        manifest["context"]["runtime"] = deepcopy(
            runtime_context
        )
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return manifest


def _context_projection(
    items: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    item_manifests = [
        {
            "index": index,
            "type": str(item.get("type") or ""),
            "role": str(item.get("role") or ""),
            "sha256": _sha256_json(item),
            "utf8_bytes": len(
                _canonical_json(item).encode("utf-8")
            ),
        }
        for index, item in enumerate(items)
    ]
    return {
        "item_count": len(item_manifests),
        "items": item_manifests,
        "sha256": _sha256_json(items),
    }


def _json_item(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        if isinstance(result, dict):
            return result
    raise TypeError(f"Unsupported model item: {type(value)!r}")


def _final_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    dump = getattr(value, "model_dump_json", None)
    if callable(dump):
        return str(dump()).strip()
    return str(value).strip()


def _contains_internal_asset_reference(
    items: tuple[dict[str, Any], ...],
) -> bool:
    def contains(value: Any) -> bool:
        if isinstance(value, dict):
            if (
                value.get("type")
                in {"input_image", "input_file"}
                and "asset_id" in value
            ):
                return True
            return any(contains(item) for item in value.values())
        if isinstance(value, list | tuple):
            return any(contains(item) for item in value)
        return False

    return any(contains(item) for item in items)


def _validate_stateless_function_context(
    items: tuple[dict[str, Any], ...],
) -> None:
    calls: set[str] = set()
    outputs: set[str] = set()
    for item in items:
        item_type = item.get("type")
        if item_type not in {
            "function_call",
            "function_call_output",
        }:
            continue
        call_id = str(item.get("call_id") or "")
        if not call_id:
            _invalid_function_context("missing_call_id")
        if item_type == "function_call":
            if call_id in calls:
                _invalid_function_context(
                    "duplicate_function_call"
                )
            calls.add(call_id)
            continue
        if call_id not in calls:
            _invalid_function_context(
                "output_without_function_call"
            )
        if call_id in outputs:
            _invalid_function_context(
                "duplicate_function_call_output"
            )
        outputs.add(call_id)
    if calls != outputs:
        _invalid_function_context(
            "function_call_without_output"
        )


def _invalid_function_context(reason: str) -> None:
    raise ApiError(
        code="model_provider_invalid_context",
        message=(
            "Stateless model input contains an unpaired function call."
        ),
        status=500,
        details={"reason": reason},
    )


def _model_behavior_error(exc: ModelBehaviorError) -> ApiError:
    message = str(exc).lower()
    if "tool" in message and (
        "not found" in message
        or "not available" in message
        or "disabled" in message
    ):
        return ApiError(
            code="tool_not_available",
            message="Requested tool is not in the runtime catalog.",
            status=502,
        )
    return ApiError(
        code="model_provider_malformed_tool_call",
        message="Model provider returned an invalid tool call.",
        status=502,
    )


def _package_version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "unknown"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


OpenAIAgentsExecutionEngine = ResponsesAgentsExecutionEngine


__all__ = [
    "AgentExecutionPort",
    "AgentExecutionResult",
    "OpenAIAgentsExecutionEngine",
    "ResponsesAgentsExecutionEngine",
]
