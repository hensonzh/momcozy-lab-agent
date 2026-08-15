from __future__ import annotations

from collections.abc import Collection, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.actions import (
    ActionExecutor,
    ActionPolicy,
    ActionPolicyRule,
    RuntimeActionService,
)
from app.agent_runtime.actions.executor import ActionApplicator
from app.agent_runtime.actions.service import (
    RunAdmissionReleaser,
    RunNotifier,
)
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.orchestration import (
    AgentCatalog,
)
from app.agent_runtime.tools import ToolContractRegistry
from app.agent_runtime.tools.handlers import ToolHandler
from app.agents import MAIN_AGENT
from app.agents.main_agent import (
    LOAD_SERVICE_SKILL_TOOL_NAME,
    SERVICE_SKILL_REGISTRY,
    TOOL_NAMESPACE_DEFINITIONS,
    LoadServiceSkillToolHandler,
    service_skill_tool_registry,
)
from app.capabilities.diary import (
    DIARY_ACTION_POLICY_RULES,
    DIARY_ACTION_TYPES,
    DiaryActionApplicator,
    DiaryMutateHandler,
    DiaryReadHandler,
    diary_tool_registry,
)
from app.capabilities.hospital_bag import (
    HOSPITAL_BAG_ACTION_POLICY_RULES,
    HOSPITAL_BAG_CART_UPDATE_ACTION,
    HospitalBagCartActionApplicator,
)
from app.capabilities.lactation_analysis import (
    LACTATION_ACTION_POLICY_RULES,
    LACTATION_RECORD_ACTION_TYPES,
    LactationRecordActionApplicator,
    MilkAnalysisToolHandler,
    lactation_analysis_tool_registry,
)
from app.capabilities.plans import (
    PLANS_ACTION_POLICY_RULES,
    PLANS_ACTION_TYPES,
    PlanMutateToolHandler,
    PlanReadToolHandler,
    PlansActionApplicator,
    ScheduleTimelineMutateToolHandler,
    ScheduleTimelineReadToolHandler,
    plans_tool_registry,
)
from app.capabilities.profile import (
    PROFILE_ACTION_POLICY_RULES,
    PROFILE_CURRENT_INFANTS_REPLACE_ACTION,
    PROFILE_UPDATE_ACTION,
    ProfileReadToolHandler,
    ProfileUpdateActionApplicator,
    ProfileUpdateToolHandler,
    profile_tool_registry,
)
from app.capabilities.runtime import (
    runtime_capability_tool_handlers,
    runtime_capability_tool_registry,
)
from app.infrastructure.product_backend import ProductBackendClient


AGENT_CATALOG = AgentCatalog(
    agent=MAIN_AGENT,
    tool_namespaces=TOOL_NAMESPACE_DEFINITIONS,
)


def build_action_policy_rules() -> dict[str, ActionPolicyRule]:
    rules: dict[str, ActionPolicyRule] = {}
    for source in (
        PROFILE_ACTION_POLICY_RULES,
        DIARY_ACTION_POLICY_RULES,
        PLANS_ACTION_POLICY_RULES,
        LACTATION_ACTION_POLICY_RULES,
        HOSPITAL_BAG_ACTION_POLICY_RULES,
    ):
        overlap = rules.keys() & source.keys()
        if overlap:
            raise ValueError(
                f"duplicate action policy rules: {sorted(overlap)}"
            )
        rules.update(source)
    mismatched = {
        action_type: rule.action_type
        for action_type, rule in rules.items()
        if action_type != rule.action_type
    }
    if mismatched:
        raise ValueError(
            f"action policy keys do not match rules: {mismatched}"
        )
    return rules


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
    policy = ActionPolicy(rules=build_action_policy_rules())
    return RuntimeActionService(
        repository=repository,
        executor=ActionExecutor(
            repository=repository,
            applicators=applicators,
            policy=policy,
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
        lactation_analysis_tool_registry(),
    ):
        for contract in source.list():
            registry.register(contract)
    return registry


def build_runtime_tool_registry() -> ToolContractRegistry:
    registry = build_product_tool_registry()
    for contract in runtime_capability_tool_registry().list():
        registry.register(contract)
    for contract in service_skill_tool_registry().list():
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
    native_handlers = runtime_capability_tool_handlers(
        repository=repository,
        action_proposer=action_service,
    )
    overlap = handlers.keys() & native_handlers.keys()
    if overlap:
        raise ValueError(f"duplicate tool handlers: {sorted(overlap)}")
    handlers.update(native_handlers)
    handlers[LOAD_SERVICE_SKILL_TOOL_NAME] = LoadServiceSkillToolHandler(
        registry=SERVICE_SKILL_REGISTRY
    )
    return handlers


def validate_runtime_composition(
    *,
    registry: ToolContractRegistry,
    handlers: Mapping[str, ToolHandler],
    action_types: Collection[str],
) -> None:
    registry.validate_action_bindings(
        policy_action_types=build_action_policy_rules(),
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
    expected = set(MAIN_AGENT.tool_names)
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
    missing = set(MAIN_AGENT.tool_names) - registered
    if missing:
        raise ValueError(
            "agent allowlist references unknown tools: "
            f"{sorted(missing)}"
        )


__all__ = [
    "AGENT_CATALOG",
    "build_action_policy_rules",
    "build_action_service",
    "build_product_action_applicators",
    "build_product_tool_handlers",
    "build_product_tool_registry",
    "build_runtime_tool_handlers",
    "build_runtime_tool_registry",
    "validate_runtime_composition",
]
