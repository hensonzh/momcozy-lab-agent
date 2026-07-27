from __future__ import annotations

from collections.abc import Collection, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import AGENT_DEFINITIONS
from app.agents.diary import (
    DiaryMutateHandler,
    DiaryReadHandler,
    diary_tool_registry,
)
from app.agents.lactation import (
    MilkAnalysisToolHandler,
    lactation_tool_registry,
)
from app.agents.plans import (
    PlanMutateToolHandler,
    PlanReadToolHandler,
    ScheduleTimelineMutateToolHandler,
    ScheduleTimelineReadToolHandler,
    plans_tool_registry,
)
from app.agents.profile import (
    ProfileReadToolHandler,
    ProfileUpdateToolHandler,
    profile_tool_registry,
)
from app.agents.runtime_native import (
    HOSPITAL_BAG_CART_UPDATE_ACTION,
    HospitalBagCartActionApplicator,
    runtime_native_tool_handlers,
    runtime_native_tool_registry,
)
from app.infrastructure.product_backend import ProductBackendClient

from .actions import (
    ACTION_POLICY_RULES,
    DIARY_ACTION_TYPES,
    LACTATION_RECORD_ACTION_TYPES,
    PLANS_ACTION_TYPES,
    PROFILE_CURRENT_INFANTS_REPLACE_ACTION,
    PROFILE_UPDATE_ACTION,
    ActionExecutor,
    DiaryActionApplicator,
    LactationRecordActionApplicator,
    PlansActionApplicator,
    ProfileUpdateActionApplicator,
    RuntimeActionService,
)
from .actions.executor import ActionApplicator
from .actions.service import RunAdmissionReleaser, RunNotifier
from .ledger.repository import RuntimeLedgerRepository
from .tools import ToolContractRegistry
from .tools.handlers import ToolHandler


def build_product_action_applicators(
    client: ProductBackendClient,
) -> Mapping[str, ActionApplicator]:
    profile = ProfileUpdateActionApplicator(client=client)
    diary = DiaryActionApplicator(client=client)
    plans = PlansActionApplicator(client=client)
    lactation = LactationRecordActionApplicator(client=client)
    return {
        PROFILE_UPDATE_ACTION: profile,
        PROFILE_CURRENT_INFANTS_REPLACE_ACTION: profile,
        **{action_type: diary for action_type in DIARY_ACTION_TYPES},
        **{action_type: plans for action_type in PLANS_ACTION_TYPES},
        **{
            action_type: lactation
            for action_type in LACTATION_RECORD_ACTION_TYPES
        },
    }


def build_action_service(
    *,
    session: AsyncSession,
    client: ProductBackendClient,
    additional_applicators: Mapping[str, ActionApplicator] | None = None,
    run_notifier: RunNotifier | None = None,
    run_admission: RunAdmissionReleaser | None = None,
) -> RuntimeActionService:
    repository = RuntimeLedgerRepository(session)
    applicators = dict(build_product_action_applicators(client))
    applicators[HOSPITAL_BAG_CART_UPDATE_ACTION] = (
        HospitalBagCartActionApplicator(repository=repository)
    )
    if additional_applicators:
        overlap = applicators.keys() & additional_applicators.keys()
        if overlap:
            raise ValueError(
                f"duplicate action applicators: {sorted(overlap)}"
            )
        applicators.update(additional_applicators)
    return RuntimeActionService(
        repository=repository,
        executor=ActionExecutor(
            repository=repository,
            applicators=applicators,
        ),
        run_notifier=run_notifier,
        run_admission=run_admission,
    )


def build_product_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    for source in (
        profile_tool_registry(),
        diary_tool_registry(),
        plans_tool_registry(),
        lactation_tool_registry(),
    ):
        for contract in source.list():
            registry.register(contract)
    return registry


def build_runtime_tool_registry() -> ToolContractRegistry:
    registry = build_product_tool_registry()
    for contract in runtime_native_tool_registry().list():
        registry.register(contract)
    _validate_agent_tool_allowlists(registry)
    return registry


def build_product_tool_handlers(
    *,
    client: ProductBackendClient,
    action_service: RuntimeActionService,
    repository: RuntimeLedgerRepository | None = None,
) -> dict[str, ToolHandler]:
    return {
        "profile_read": ProfileReadToolHandler(client=client),
        "profile_update": ProfileUpdateToolHandler(
            action_proposer=action_service
        ),
        "diary_read": DiaryReadHandler(client=client),
        "diary_mutate": DiaryMutateHandler(
            action_proposer=action_service
        ),
        "plan_read": PlanReadToolHandler(client=client),
        "plan_mutate": PlanMutateToolHandler(
            action_proposer=action_service
        ),
        "schedule_timeline_read": ScheduleTimelineReadToolHandler(
            client=client
        ),
        "schedule_timeline_mutate": ScheduleTimelineMutateToolHandler(
            action_proposer=action_service,
            client=client,
        ),
        "milk_analysis_manage": MilkAnalysisToolHandler(
            client=client,
            repository=repository,
        ),
    }


def build_runtime_tool_handlers(
    *,
    repository: RuntimeLedgerRepository,
    client: ProductBackendClient,
    action_service: RuntimeActionService,
) -> dict[str, ToolHandler]:
    handlers = build_product_tool_handlers(
        client=client,
        action_service=action_service,
        repository=repository,
    )
    native_handlers = runtime_native_tool_handlers(
        repository=repository,
        action_proposer=action_service,
    )
    overlap = handlers.keys() & native_handlers.keys()
    if overlap:
        raise ValueError(f"duplicate tool handlers: {sorted(overlap)}")
    handlers.update(native_handlers)
    return handlers


def validate_runtime_composition(
    *,
    registry: ToolContractRegistry,
    handlers: Mapping[str, ToolHandler],
    action_types: Collection[str],
) -> None:
    registry.validate_action_bindings(
        policy_action_types=ACTION_POLICY_RULES,
        handler_action_types=action_types,
    )
    registered = set(registry.names_for_sdk())
    handled = set(handlers)
    if registered != handled:
        raise ValueError(
            "tool registry/handler mismatch: "
            f"missing_handlers={sorted(registered - handled)}, "
            f"unknown_handlers={sorted(handled - registered)}"
        )
    expected = {
        tool_name
        for definition in AGENT_DEFINITIONS.values()
        for tool_name in definition.tool_names
    }
    if registered != expected:
        raise ValueError(
            "runtime tool set/agent allowlist mismatch: "
            f"missing_tools={sorted(expected - registered)}, "
            f"unused_tools={sorted(registered - expected)}"
        )
    _validate_agent_tool_allowlists(registry)


def _validate_agent_tool_allowlists(
    registry: ToolContractRegistry,
) -> None:
    registered = set(registry.names_for_sdk())
    missing = {
        definition.name: sorted(set(definition.tool_names) - registered)
        for definition in AGENT_DEFINITIONS.values()
        if set(definition.tool_names) - registered
    }
    if missing:
        raise ValueError(
            f"agent allowlists reference unknown tools: {missing}"
        )


__all__ = [
    "build_action_service",
    "build_product_action_applicators",
    "build_product_tool_handlers",
    "build_product_tool_registry",
    "build_runtime_tool_handlers",
    "build_runtime_tool_registry",
    "validate_runtime_composition",
]
