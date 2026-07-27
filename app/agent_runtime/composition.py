from __future__ import annotations

from collections.abc import Collection, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.diary import (
    PregnancyDiaryReadHandler,
    PregnancyDiaryWriteHandler,
    diary_tool_registry,
)
from app.agents.lactation import (
    LactationTimelineReadToolHandler,
    LactationTimelineWriteToolHandler,
    MilkAnalysisToolHandler,
    MilkReminderWriteToolHandler,
    lactation_tool_registry,
)
from app.agents.plans import (
    MilkPlanWriteToolHandler,
    PlansCalendarReadToolHandler,
    PlansCurrentReadToolHandler,
    PlansPlanWriteToolHandler,
    PlansTaskWriteToolHandler,
    PregnancyPlanManageToolHandler,
    plans_tool_registry,
)
from app.agents.profile import (
    ProfileReadToolHandler,
    ProfileWriteToolHandler,
    profile_tool_registry,
)
from app.agents.support import (
    SupportTicketWriteToolHandler,
    support_tool_registry,
)
from app.agents import AGENT_DEFINITIONS
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
    MILK_REMINDER_ACTION_TYPES,
    PLANS_ACTION_TYPES,
    PROFILE_UPDATE_ACTION,
    SUPPORT_TICKET_ACTION,
    ActionExecutor,
    LactationRecordActionApplicator,
    MilkReminderActionApplicator,
    PlansActionApplicator,
    PregnancyDiaryActionApplicator,
    ProfileUpdateActionApplicator,
    RuntimeActionService,
    SupportTicketActionApplicator,
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
    diary = PregnancyDiaryActionApplicator(client=client)
    plans = PlansActionApplicator(client=client)
    lactation = LactationRecordActionApplicator(client=client)
    reminder = MilkReminderActionApplicator(client=client)
    support = SupportTicketActionApplicator(client=client)
    return {
        PROFILE_UPDATE_ACTION: profile,
        **{action_type: diary for action_type in DIARY_ACTION_TYPES},
        **{action_type: plans for action_type in PLANS_ACTION_TYPES},
        **{
            action_type: lactation
            for action_type in LACTATION_RECORD_ACTION_TYPES
        },
        **{
            action_type: reminder
            for action_type in MILK_REMINDER_ACTION_TYPES
        },
        SUPPORT_TICKET_ACTION: support,
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
        support_tool_registry(),
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
) -> dict[str, ToolHandler]:
    return {
        "profile_read": ProfileReadToolHandler(client=client),
        "profile_write": ProfileWriteToolHandler(
            action_proposer=action_service
        ),
        "pregnancy_diary_read": PregnancyDiaryReadHandler(client=client),
        "pregnancy_diary_write": PregnancyDiaryWriteHandler(
            action_proposer=action_service
        ),
        "plans_current_read": PlansCurrentReadToolHandler(client=client),
        "plans_calendar_read": PlansCalendarReadToolHandler(client=client),
        "plans_task_write": PlansTaskWriteToolHandler(
            action_proposer=action_service
        ),
        "plans_plan_write": PlansPlanWriteToolHandler(
            action_proposer=action_service
        ),
        "pregnancy_plan_manage": PregnancyPlanManageToolHandler(
            action_proposer=action_service
        ),
        "plans_milk_plan_write": MilkPlanWriteToolHandler(
            action_proposer=action_service
        ),
        "lactation_timeline_read": LactationTimelineReadToolHandler(
            client=client
        ),
        "lactation_timeline_write": LactationTimelineWriteToolHandler(
            action_proposer=action_service
        ),
        "milk_analysis_manage": MilkAnalysisToolHandler(client=client),
        "notifications_milk_reminder_write": MilkReminderWriteToolHandler(
            action_proposer=action_service
        ),
        "support_ticket_write": SupportTicketWriteToolHandler(
            action_proposer=action_service
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
        raise ValueError(f"agent allowlists reference unknown tools: {missing}")


__all__ = [
    "build_action_service",
    "build_product_action_applicators",
    "build_product_tool_handlers",
    "build_product_tool_registry",
    "build_runtime_tool_handlers",
    "build_runtime_tool_registry",
    "validate_runtime_composition",
]
