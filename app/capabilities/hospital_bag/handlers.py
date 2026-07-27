from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.agent_runtime.actions import (
    ActionProposal,
    ActionProposer,
)
from app.agent_runtime.ledger import AgentWorkflowState
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.capabilities._internal.execution import (
    create_artifact,
    require_thread,
    save_workflow,
    uuid_or_none,
    validate_arguments,
    workflow_projection,
    workflow_state,
)
from app.capabilities.pump_models import PumpModelsReferenceService
from app.core.errors import ApiError

from .actions import HOSPITAL_BAG_CART_UPDATE_ACTION
from .cart import reduce_hospital_bag_cart
from .contracts import (
    HospitalBagCartMutateArguments,
    HospitalBagIntake,
    HospitalBagManageArguments,
)


class HospitalBagManageToolHandler:
    WORKFLOW_TYPE = "hospital_bag"
    WORKFLOW_SCHEMA = "hospital-bag-workflow.v1"

    def __init__(
        self,
        *,
        repository: RuntimeLedgerRepository,
    ) -> None:
        self.repository = repository

    async def __call__(
        self,
        context: ToolHandlerContext,
    ) -> ToolResult:
        arguments = validate_arguments(
            HospitalBagManageArguments,
            context.args,
            "Hospital bag tool arguments are invalid.",
        )
        thread_id = require_thread(context)
        workflow = (
            await self.repository.get_latest_workflow_state_for_owner(
                owner_user_id=context.actor.user_id,
                thread_id=thread_id,
                workflow_type=self.WORKFLOW_TYPE,
            )
        )
        trusted = context.trusted_args or {}
        confirmed_form_data = trusted.get("confirmed_form_data")
        trusted_artifact_id = uuid_or_none(
            trusted.get("form_artifact_id")
        )
        if (
            isinstance(confirmed_form_data, dict)
            and trusted_artifact_id is not None
        ):
            try:
                intake = HospitalBagIntake.model_validate(
                    confirmed_form_data
                )
            except ValidationError as exc:
                raise ApiError(
                    code="runtime_form_invalid",
                    message=(
                        "Verified hospital bag form data is invalid."
                    ),
                    status=500,
                    details={
                        "errors": exc.errors(include_url=False)
                    },
                ) from exc
            return await self._submit(
                context=context,
                thread_id=thread_id,
                workflow=workflow,
                intake_artifact_id=trusted_artifact_id,
                intake=intake,
                requested_generation_mode=(
                    arguments.generation_mode
                    if "generation_mode"
                    in arguments.model_fields_set
                    else None
                ),
            )
        force_new = arguments.restart
        if workflow is not None and not force_new:
            resumed = await self._resume(
                context=context,
                thread_id=thread_id,
                workflow=workflow,
            )
            if resumed is not None:
                return ToolResult.json(resumed)
        return ToolResult.json(
            await self._create_intake(
                context=context,
                thread_id=thread_id,
                generation_mode=arguments.generation_mode,
                previous=workflow,
            )
        )

    async def _resume(
        self,
        *,
        context: ToolHandlerContext,
        thread_id: UUID,
        workflow: AgentWorkflowState,
    ) -> dict[str, Any] | None:
        state = workflow_state(workflow)
        artifact_key = (
            "result_artifact_id"
            if workflow.status == "completed"
            else "intake_artifact_id"
        )
        artifact_id = uuid_or_none(state.get(artifact_key))
        if artifact_id is None:
            return None
        artifact = (
            await self.repository.get_artifact_for_thread_owner(
                artifact_id=artifact_id,
                thread_id=thread_id,
                owner_user_id=context.actor.user_id,
            )
        )
        if artifact is None or artifact.status == "deleted":
            return None
        if workflow.status == "completed":
            return {
                "schema_version": "hospital-bag.result.v1",
                "status": "card_ready",
                "artifact_id": str(artifact.id),
                "artifact_type": artifact.artifact_type,
                "reused": True,
                "workflow": workflow_projection(workflow),
            }
        return {
            "schema_version": "hospital-bag.result.v1",
            "status": "intake_required",
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "reused": True,
            "form": artifact.payload.get("form", {}),
            "workflow": workflow_projection(workflow),
        }

    async def _create_intake(
        self,
        *,
        context: ToolHandlerContext,
        thread_id: UUID,
        generation_mode: str,
        previous: AgentWorkflowState | None,
    ) -> dict[str, Any]:
        form = _hospital_bag_form()
        artifact = await create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="hospital_bag_intake",
            schema_version="v1",
            payload={"form": form},
        )
        workflow = await save_workflow(
            repository=self.repository,
            context=context,
            thread_id=thread_id,
            workflow_type=self.WORKFLOW_TYPE,
            schema_version=self.WORKFLOW_SCHEMA,
            previous=previous,
            status="collecting",
            active_step="intake",
            state={
                "phase": "collecting_intake",
                "generation_mode": generation_mode,
                "intake_artifact_id": str(artifact.id),
            },
            event_type="hospital_bag.intake_created",
        )
        return {
            "schema_version": "hospital-bag.result.v1",
            "status": "intake_required",
            "artifact_id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "reused": False,
            "form": form,
            "workflow": workflow_projection(workflow),
        }

    async def _submit(
        self,
        *,
        context: ToolHandlerContext,
        thread_id: UUID,
        workflow: AgentWorkflowState | None,
        intake_artifact_id: UUID,
        intake: HospitalBagIntake,
        requested_generation_mode: str | None,
    ) -> ToolResult:
        workflow_state_value = (
            workflow_state(workflow)
            if workflow is not None
            else {}
        )
        generation_mode = (
            requested_generation_mode
            if requested_generation_mode is not None
            else str(
                workflow_state_value.get("generation_mode")
                or "standard"
            )
        )
        if (
            workflow is None
            or workflow.status != "collecting"
            or uuid_or_none(
                workflow_state_value.get("intake_artifact_id")
            )
            != intake_artifact_id
        ):
            raise ApiError(
                code="stale_hospital_bag_intake",
                message=(
                    "Hospital bag intake is not the active workflow."
                ),
                status=409,
            )
        intake_artifact = (
            await self.repository.get_artifact_for_thread_owner(
                artifact_id=intake_artifact_id,
                thread_id=thread_id,
                owner_user_id=context.actor.user_id,
            )
        )
        if (
            intake_artifact is None
            or intake_artifact.status == "deleted"
            or intake_artifact.artifact_type
            != "hospital_bag_intake"
        ):
            raise ApiError(
                code="stale_hospital_bag_intake",
                message=(
                    "Hospital bag intake is not visible in this thread."
                ),
                status=409,
            )
        card_payload = _hospital_bag_card(
            intake=intake,
            generation_mode=generation_mode,
        )
        artifact = await create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="hospital_bag_card",
            schema_version="v1",
            payload=card_payload,
        )
        updated = await save_workflow(
            repository=self.repository,
            context=context,
            thread_id=thread_id,
            workflow_type=self.WORKFLOW_TYPE,
            schema_version=self.WORKFLOW_SCHEMA,
            previous=workflow,
            status="completed",
            active_step="",
            state={
                "phase": "completed",
                "generation_mode": generation_mode,
                "intake_artifact_id": str(intake_artifact.id),
                "result_artifact_id": str(artifact.id),
            },
            event_type="hospital_bag.completed",
        )
        return ToolResult.json(
            {
                "schema_version": "hospital-bag.result.v1",
                "status": "card_ready",
                "artifact_id": str(artifact.id),
                "artifact_type": artifact.artifact_type,
                "reused": False,
                "workflow": workflow_projection(updated),
            }
        )


class HospitalBagCartMutateToolHandler:
    def __init__(
        self,
        *,
        action_proposer: ActionProposer,
        reference_service: PumpModelsReferenceService | None = None,
    ) -> None:
        self.action_proposer = action_proposer
        self.reference_service = (
            reference_service or PumpModelsReferenceService()
        )

    async def __call__(
        self,
        context: ToolHandlerContext,
    ) -> ToolResult:
        arguments = validate_arguments(
            HospitalBagCartMutateArguments,
            context.args,
            "Hospital bag cart arguments are invalid.",
        )
        runtime_cart = (context.trusted_args or {}).get(
            "runtime_cart"
        )
        _validate_cart_targets(
            arguments=arguments,
            runtime_cart=runtime_cart,
        )
        payload = arguments.model_dump(
            mode="json",
            exclude_unset=True,
        )
        cart_update = reduce_hospital_bag_cart(
            arguments=payload,
            runtime_cart=runtime_cart,
            pump_products=list(
                self.reference_service.result["products"]
            ),
        )
        payload["cart_update"] = cart_update
        proposed = await self.action_proposer.propose_action(
            ActionProposal(
                actor_user_id=context.actor.user_id,
                run_id=context.run_id,
                action_type=HOSPITAL_BAG_CART_UPDATE_ACTION,
                target_type="hospital_bag_cart",
                target_id="current",
                side_effect_level="low",
                preview_payload={
                    "operation": arguments.operation,
                    "cart_update": cart_update,
                },
                apply_payload=payload,
                idempotency_key=(
                    f"{context.run_id}:{context.call_id}:"
                    "hospital-bag-cart"
                ),
            )
        )
        return ToolResult.json(
            {
                "action_id": str(proposed.id),
                "action_type": proposed.action_type,
                "action_status": proposed.status,
                "requires_confirmation": (
                    proposed.requires_confirmation
                ),
                "confirmation_policy": (
                    "always"
                    if proposed.requires_confirmation
                    else "explicit_intent"
                ),
                "user_visible": proposed.requires_confirmation,
                "write_succeeded": proposed.status == "applied",
                "error_code": proposed.error_code or None,
                "summary": cart_update["message"],
                "cart_update": cart_update,
            }
        )


def _hospital_bag_form() -> dict[str, Any]:
    return {
        "id": "hospital_bag_intake",
        "title": "待产包信息",
        "submit_label": "生成待产包",
        "fields": [
            {
                "id": "due_date",
                "type": "date",
                "label": "预产期",
                "required": True,
            },
            {
                "id": "delivery_method",
                "type": "select",
                "label": "预计分娩方式",
                "required": True,
                "options": [
                    "vaginal",
                    "cesarean",
                    "assisted_vaginal",
                    "unknown",
                ],
            },
            {
                "id": "feeding_plan",
                "type": "select",
                "label": "喂养计划",
                "required": True,
                "options": [
                    "breastfeeding",
                    "mixed",
                    "formula",
                    "unknown",
                ],
            },
            {
                "id": "hospital_stay_days",
                "type": "number",
                "label": "预计住院天数",
                "required": True,
                "minimum": 1,
                "maximum": 14,
            },
            {
                "id": "notes",
                "type": "textarea",
                "label": "医院要求或其他备注",
                "required": False,
            },
        ],
    }


def _hospital_bag_card(
    *,
    intake: HospitalBagIntake,
    generation_mode: str,
) -> dict[str, Any]:
    essentials = [
        {
            "group_id": "documents",
            "title": "证件与资料",
            "items": [
                {
                    "id": "identity-documents",
                    "label": "身份证件",
                },
                {
                    "id": "medical-records",
                    "label": "就诊资料",
                },
                {
                    "id": "insurance-documents",
                    "label": "医保或保险资料",
                },
            ],
        },
        {
            "group_id": "mother",
            "title": "妈妈用品",
            "items": [
                {"id": "mom-clothes", "label": "舒适衣物"},
                {"id": "mom-pad", "label": "产褥垫"},
                {
                    "id": "mom-toiletries",
                    "label": "洗漱用品",
                },
                {
                    "id": "mom-slippers",
                    "label": "防滑拖鞋",
                },
            ],
        },
        {
            "group_id": "baby",
            "title": "宝宝用品",
            "items": [
                {
                    "id": "baby-clothes",
                    "label": "新生儿衣物",
                },
                {"id": "baby-diaper", "label": "纸尿裤"},
                {"id": "baby-blanket", "label": "包被"},
                {
                    "id": "baby-car-seat",
                    "label": "安全座椅",
                },
            ],
        },
    ]
    optional = [
        {
            "group_id": "feeding",
            "title": "喂养用品",
            "items": (
                [
                    {"id": "milk-bra", "label": "哺乳内衣"},
                    {"id": "milk-pad", "label": "防溢乳垫"},
                    {
                        "id": "milk-cream",
                        "label": "乳头护理用品",
                    },
                ]
                if intake.feeding_plan
                in {"breastfeeding", "mixed"}
                else [
                    {"id": "milk-bottle", "label": "奶瓶"},
                    {
                        "id": "formula-supplies",
                        "label": "配方奶喂养用品",
                    },
                ]
            ),
        },
        {
            "group_id": "long_stay",
            "title": "较长住院补充",
            "items": [
                {
                    "id": "long-stay-clothes",
                    "label": "额外换洗衣物",
                },
                {
                    "id": "long-stay-diapers",
                    "label": "额外纸尿裤",
                },
                {
                    "id": "long-stay-charger",
                    "label": "充电器",
                },
            ],
        },
    ]
    groups = list(essentials)
    if generation_mode == "standard":
        groups.append(optional[0])
        if intake.hospital_stay_days >= 4:
            groups.append(optional[1])
    return {
        "card_type": "hospital_bag_card",
        "title": "待产包清单",
        "generation_mode": generation_mode,
        "intake": intake.model_dump(mode="json"),
        "packing_groups": groups,
        "disclaimer": "请按医院提供的清单和个人医疗安排复核。",
    }


def _validate_cart_targets(
    *,
    arguments: HospitalBagCartMutateArguments,
    runtime_cart: object,
) -> None:
    target_ids: set[str] = set()
    if arguments.operation in {
        "remove_items",
        "replace_items",
        "mark_provided",
        "mark_owned",
    }:
        target_ids.update(arguments.item_ids)
    target_ids.update(
        update.item_id for update in arguments.quantity_updates
    )
    target_ids.update(arguments.preserve_item_ids)
    if not target_ids:
        return
    if not isinstance(runtime_cart, dict):
        raise ApiError(
            code="runtime_context_unavailable",
            message="Current hospital bag cart is unavailable.",
            status=503,
        )
    groups = runtime_cart.get("groups")
    visible_ids = (
        {
            str(item.get("id"))
            for group in groups
            if isinstance(group, dict)
            for items in [group.get("items")]
            if isinstance(items, list)
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item.get("id")
        }
        if isinstance(groups, list)
        else set()
    )
    invisible = sorted(target_ids - visible_ids)
    if invisible:
        raise ApiError(
            code="cart_target_not_visible",
            message=(
                "Hospital bag cart targets must come from the "
                "current Runtime-verified cart."
            ),
            status=422,
            details={"item_ids": invisible},
        )

__all__ = [
    "HospitalBagCartMutateToolHandler",
    "HospitalBagManageToolHandler",
]
