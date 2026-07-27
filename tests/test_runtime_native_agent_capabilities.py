from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.agent_runtime.actions import ActionApplyResult, ActionProposal, ActionProposed
from app.agent_runtime.ledger import AgentAction
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import ToolHandlerContext
from app.agents.runtime_native import (
    DEVICE_AGENT_NATIVE_TOOLS,
    LACTATION_AGENT_NATIVE_TOOLS,
    MAIN_AGENT_NATIVE_TOOLS,
    PRENATAL_AGENT_NATIVE_TOOLS,
    ConversationHistoryImageReadArguments,
    ConversationHistoryImageReadToolHandler,
    DeviceGuidanceManageToolHandler,
    HospitalBagCartActionApplicator,
    HospitalBagCartMutateToolHandler,
    HospitalBagManageToolHandler,
    IbclcConsultCardCreateToolHandler,
    PregnancyIntakeManageToolHandler,
    PumpModelsReadToolHandler,
    SupportTicketDraftCreateToolHandler,
    runtime_native_tool_registry,
)
from app.auth import RuntimePrincipal
from app.core.errors import ApiError


def test_native_registry_matches_agent_allowlists_and_action_boundary() -> None:
    registry = runtime_native_tool_registry()

    assert set(registry.names_for_sdk()) == {
        "conversation_history_image_read",
        "devices_guidance_manage",
        "hospital_bag_cart_mutate",
        "hospital_bag_manage",
        "ibclc_consult_card_create",
        "pregnancy_intake_manage",
        "pump_models_read",
        "support_ticket_draft_create",
    }
    assert MAIN_AGENT_NATIVE_TOOLS == ("conversation_history_image_read",)
    assert PRENATAL_AGENT_NATIVE_TOOLS == (
        "pregnancy_intake_manage",
        "hospital_bag_manage",
        "hospital_bag_cart_mutate",
    )
    assert LACTATION_AGENT_NATIVE_TOOLS == (
        "ibclc_consult_card_create",
    )
    assert DEVICE_AGENT_NATIVE_TOOLS == (
        "devices_guidance_manage",
        "pump_models_read",
        "support_ticket_draft_create",
    )
    cart = registry.get("hospital_bag_cart_mutate")
    assert cart.effect_scope == "user_resource"
    assert cart.action_types == ("hospital_bag.cart.update",)
    assert registry.get("devices_guidance_manage").effect_scope == "agent_internal"
    assert (
        registry.get("ibclc_consult_card_create").effect_scope
        == "agent_internal"
    )
    assert (
        registry.get("support_ticket_draft_create").effect_scope
        == "agent_internal"
    )


def test_history_image_contract_does_not_accept_asset_id_or_url() -> None:
    with pytest.raises(ValidationError):
        ConversationHistoryImageReadArguments.model_validate({"asset_id": str(uuid4())})
    with pytest.raises(ValidationError):
        ConversationHistoryImageReadArguments.model_validate(
            {"image_url": "http://arbitrary.example/image.jpg"}
        )


def test_pump_models_read_uses_versioned_runtime_reference() -> None:
    result = asyncio.run(PumpModelsReadToolHandler()(_context(args={})))
    observation = cast(dict[str, Any], result.to_observation())

    assert observation["schema_version"] == "pump-models.result.v1"
    assert observation["reference_version"].startswith("pump-models-")
    assert observation["count"] >= 1
    assert observation["products"][0]["sku_id"]
    assert all(str(product["image_url"]).startswith("https://") for product in observation["products"])


def test_ibclc_card_is_persisted_as_owner_scoped_runtime_artifact() -> None:
    store = FakeNativeRepository()
    handler = IbclcConsultCardCreateToolHandler(
        repository=cast(RuntimeLedgerRepository, store)
    )

    result = asyncio.run(
        handler(
            _context(
                args={
                    "reason": "衔乳时持续疼痛",
                    "urgency": "soon",
                },
                trusted_args={
                    "trusted_current_user_text": (
                        "请帮我创建 IBCLC 咨询入口"
                    )
                },
            )
        )
    )
    observation = cast(dict[str, Any], result.to_observation())

    assert observation["status"] == "card_created"
    assert store.artifacts[-1].owner_user_id == OWNER_ID
    assert store.artifacts[-1].artifact_type == "ibclc_consult_card"
    assert store.event_types == ["artifact.created"]
    assert store.event_payloads[-1]["artifact"] == {
        "id": str(store.artifacts[-1].id),
        "artifact_type": "ibclc_consult_card",
        "schema_version": "v1",
        "status": "created",
        "payload": store.artifacts[-1].payload,
        "raw_payload_ref": "",
    }


def test_support_ticket_create_persists_draft_without_external_action() -> None:
    store = FakeNativeRepository()
    handler = SupportTicketDraftCreateToolHandler(
        repository=cast(RuntimeLedgerRepository, store)
    )

    result = asyncio.run(
        handler(
            _context(
                args={
                    "issue_summary": "吸奶器无法开机",
                    "issue_type": "malfunction",
                    "urgency": "high",
                },
                trusted_args={
                    "trusted_current_user_text": (
                        "可以，现在帮我创建售后工单"
                    )
                },
            )
        )
    ).to_observation()

    assert result["status"] == "draft_created"
    assert result["submission_status"] == "draft"
    assert store.artifacts[-1].artifact_type == "support_ticket_draft"
    assert store.artifacts[-1].payload["submission_status"] == "draft"


def test_pregnancy_intake_pause_resume_abandon_events_are_explicit() -> None:
    store = FakeNativeRepository()
    handler = PregnancyIntakeManageToolHandler(
        repository=cast(RuntimeLedgerRepository, store)
    )

    asyncio.run(
        handler(_context(args={"command": "start_or_resume"}))
    )
    paused = asyncio.run(
        handler(_context(args={"command": "pause"}))
    ).to_observation()
    resumed = asyncio.run(
        handler(_context(args={"command": "resume"}))
    ).to_observation()
    abandoned = asyncio.run(
        handler(_context(args={"command": "abandon"}))
    ).to_observation()

    assert paused["status"] == "intake_paused"
    assert resumed["status"] == "intake_resumed"
    assert abandoned["status"] == "intake_abandoned"
    assert store.workflow_event_types[-3:] == [
        "pregnancy_plan.paused",
        "pregnancy_plan.resumed",
        "pregnancy_plan.abandoned",
    ]
    focus_areas = next(
        field
        for field in store.artifacts[0].payload["form"]["fields"]
        if field["id"] == "focus_areas"
    )
    assert focus_areas["type"] == "multi_select"


def test_hospital_bag_flow_persists_and_resumes_the_same_form() -> None:
    store = FakeNativeRepository()
    handler = HospitalBagManageToolHandler(repository=cast(RuntimeLedgerRepository, store))

    first = cast(
        dict[str, Any],
        asyncio.run(
            handler(
                _context(
                    args={
                        "generation_mode": "standard",
                    }
                )
            )
        ).to_observation(),
    )
    resumed = cast(
        dict[str, Any],
        asyncio.run(handler(_context(args={}))).to_observation(),
    )

    assert first["status"] == "intake_required"
    assert resumed["status"] == "intake_required"
    assert resumed["reused"] is True
    assert resumed["artifact_id"] == first["artifact_id"]
    assert len(store.artifacts) == 1
    assert store.workflow is not None
    assert store.workflow.owner_user_id == OWNER_ID
    assert store.workflow.thread_id == THREAD_ID


def test_hospital_bag_submission_creates_card_and_completes_workflow() -> None:
    store = FakeNativeRepository()
    handler = HospitalBagManageToolHandler(repository=cast(RuntimeLedgerRepository, store))
    started = cast(
        dict[str, Any],
        asyncio.run(
            handler(
                _context(
                    args={
                        "generation_mode": "immediate",
                    }
                )
            )
        ).to_observation(),
    )

    completed = cast(
        dict[str, Any],
        asyncio.run(
            handler(
                _context(
                    args={"generation_mode": "immediate"},
                    trusted_args={
                        "form_artifact_id": started["artifact_id"],
                        "form_submission_id": "submission-1",
                        "confirmed_form_data": {
                            "due_date": "2026-08-18",
                            "delivery_method": "unknown",
                            "feeding_plan": "breastfeeding",
                            "hospital_stay_days": 3,
                        },
                    },
                )
            )
        ).to_observation(),
    )

    assert completed["status"] == "card_ready"
    assert store.artifacts[-1].artifact_type == "hospital_bag_card"
    card = store.artifacts[-1].payload
    assert card["card_type"] == "hospital_bag_card"
    assert card["generation_mode"] == "immediate"
    assert "groups" not in card
    assert card["packing_groups"]
    assert all(
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"]
        and isinstance(item.get("label"), str)
        and item["label"]
        for group in card["packing_groups"]
        for item in group["items"]
    )
    assert store.workflow is not None
    assert store.workflow.status == "completed"


def test_device_walkthrough_is_durable_and_advances_one_step() -> None:
    store = FakeNativeRepository()
    handler = DeviceGuidanceManageToolHandler(repository=cast(RuntimeLedgerRepository, store))

    started = cast(
        dict[str, Any],
        asyncio.run(
            handler(
                _context(
                    args={
                        "model": "Air1",
                        "operation": "start_or_resume",
                    }
                )
            )
        ).to_observation(),
    )
    advanced = cast(
        dict[str, Any],
        asyncio.run(
            handler(
                _context(
                    args={
                        "operation": "complete_current",
                    }
                )
            )
        ).to_observation(),
    )

    assert started["workflow"]["current_step"] == "guide.parts"
    assert advanced["workflow"]["current_step"] == "guide.controls"
    assert store.workflow is not None
    assert store.workflow.active_step == "guide.controls"
    assert store.workflow.owner_user_id == OWNER_ID
    assert [artifact.artifact_type for artifact in store.artifacts] == [
        "device_guidance_card",
        "device_guidance_card",
    ]
    for artifact in store.artifacts:
        assert artifact.payload["title"]
        assert artifact.payload["content"]
        assert artifact.payload["steps"]
        assert artifact.payload["current_step"]["id"]
        assert "image_refs" in artifact.payload


def test_history_image_only_reinjects_current_thread_tool_image() -> None:
    image_url = "https://assets.example/current-thread.jpg"
    handler = ConversationHistoryImageReadToolHandler()

    result = asyncio.run(
        handler(
            _context(
                args={
                    "image_url": image_url,
                    "detail": "high",
                },
                trusted_args={"visible_image_urls": [image_url]},
            )
        )
    )

    assert result.to_function_call_output()[-1] == {
        "type": "input_image",
        "detail": "high",
        "image_url": image_url,
    }

    with pytest.raises(ApiError, match="visible"):
        asyncio.run(
            handler(
                _context(
                    args={"image_url": "https://assets.example/other.jpg"},
                    trusted_args={"visible_image_urls": [image_url]},
                )
            )
        )


def test_history_image_can_reinject_runtime_artifact_image() -> None:
    image_url = "https://assets.example/device-step.jpg"
    handler = ConversationHistoryImageReadToolHandler()

    result = asyncio.run(
        handler(
            _context(
                args={"image_url": image_url},
                trusted_args={"visible_image_urls": [image_url]},
            )
        )
    )

    assert result.to_function_call_output()[-1] == {
        "type": "input_image",
        "detail": "low",
        "image_url": image_url,
    }


def test_hospital_bag_cart_mutate_uses_runtime_action_and_applicator() -> None:
    proposer = RecordingActionProposer()
    handler = HospitalBagCartMutateToolHandler(
        action_proposer=proposer
    )

    result = cast(
        dict[str, Any],
        asyncio.run(
            handler(
                _context(
                    args={
                        "operation": "update_quantity",
                        "quantity_updates": [
                            {"item_id": "mom-wipes", "qty": 2}
                        ],
                    },
                    trusted_args={
                        "runtime_cart": {
                            "groups": [
                                {
                                    "title": "妈妈护理",
                                    "tone": "rose",
                                    "items": [
                                        {
                                            "id": "mom-wipes",
                                            "name": "产后护理湿巾",
                                            "qty": 1,
                                            "price": 29.9,
                                        }
                                    ],
                                }
                            ],
                            "totals": {},
                        }
                    },
                )
            )
        ).to_observation(),
    )

    assert proposer.proposal is not None
    assert proposer.proposal.actor_user_id == OWNER_ID
    assert proposer.proposal.action_type == "hospital_bag.cart.update"
    assert result["action_status"] == "applied"

    store = FakeNativeRepository()
    action = AgentAction(
        id=uuid4(),
        run_id=RUN_ID,
        actor_user_id=OWNER_ID,
        action_type="hospital_bag.cart.update",
        target_type="hospital_bag_cart",
        target_id="current",
        status="applying",
        side_effect_level="low",
        preview_payload={},
        apply_payload=proposer.proposal.apply_payload,
        idempotency_key="cart-call",
    )
    applied: ActionApplyResult = asyncio.run(HospitalBagCartActionApplicator(repository=cast(RuntimeLedgerRepository, store))(action))

    assert applied.resource_type == "hospital_bag_cart"
    assert store.artifacts[-1].artifact_type == "hospital_bag_cart"
    assert store.artifacts[-1].owner_user_id == OWNER_ID
    artifact_event = applied.application_events[0]
    assert artifact_event["type"] == "artifact.created"
    assert artifact_event["payload"]["artifact"] == {
        "id": str(store.artifacts[-1].id),
        "artifact_type": "hospital_bag_cart",
        "schema_version": "v1",
        "status": "created",
        "payload": store.artifacts[-1].payload,
        "raw_payload_ref": "",
    }


OWNER_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
THREAD_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
RUN_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def _context(
    *,
    args: dict[str, Any],
    trusted_args: dict[str, Any] | None = None,
) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=RuntimePrincipal(
            user_id=OWNER_ID,
            subject=str(OWNER_ID),
            session_id=uuid4(),
            token_id="token",
            token_version=1,
            roles=frozenset(),
            permissions=frozenset(),
        ),
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        tool_name="native",
        call_id=str(uuid4()),
        args=args,
        request_id="request",
        trusted_args=trusted_args,
        as_of_date=date(2026, 7, 26),
    )


class RecordingActionProposer:
    def __init__(self) -> None:
        self.proposal: ActionProposal | None = None

    async def propose_action(self, proposal: ActionProposal) -> ActionProposed:
        self.proposal = proposal
        return ActionProposed(
            id=uuid4(),
            action_type=proposal.action_type,
            status="applied",
            requires_confirmation=False,
        )


class FakeNativeRepository:
    def __init__(self) -> None:
        self.artifacts: list[Any] = []
        self.workflow: Any | None = None
        self.event_types: list[str] = []
        self.event_payloads: list[dict[str, Any]] = []
        self.workflow_event_types: list[str] = []
        self.tool_context_items: dict[UUID, Any] = {}

    async def create_artifact(self, **kwargs: Any) -> Any:
        assert kwargs["owner_user_id"] == OWNER_ID
        artifact = SimpleNamespace(
            id=uuid4(),
            created_at=None,
            updated_at=None,
            raw_payload_ref="",
            **kwargs,
        )
        self.artifacts.append(artifact)
        return artifact

    async def get_artifact_for_thread_owner(
        self,
        *,
        artifact_id: UUID,
        thread_id: UUID,
        owner_user_id: UUID,
    ) -> Any | None:
        if thread_id != THREAD_ID or owner_user_id != OWNER_ID:
            return None
        return next(
            (artifact for artifact in self.artifacts if artifact.id == artifact_id),
            None,
        )

    async def get_latest_workflow_state_for_owner(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        workflow_type: str,
        active_only: bool = False,
    ) -> Any | None:
        del active_only
        if owner_user_id != OWNER_ID or thread_id != THREAD_ID or self.workflow is None or self.workflow.workflow_type != workflow_type:
            return None
        return self.workflow

    async def upsert_workflow_state(self, **kwargs: Any) -> Any:
        assert kwargs["owner_user_id"] == OWNER_ID
        assert kwargs["thread_id"] == THREAD_ID
        revision = (
            int(self.workflow.revision) + 1 if self.workflow is not None and self.workflow.workflow_type == kwargs["workflow_type"] else 1
        )
        self.workflow = SimpleNamespace(
            id=(self.workflow.id if self.workflow is not None and self.workflow.workflow_type == kwargs["workflow_type"] else uuid4()),
            revision=revision,
            completed_at=None,
            expires_at=None,
            step_token="",
            created_at=None,
            updated_at=None,
            **kwargs,
        )
        return self.workflow

    async def append_workflow_event(self, **kwargs: Any) -> Any:
        assert kwargs["owner_user_id"] == OWNER_ID
        self.workflow_event_types.append(kwargs["event_type"])
        return SimpleNamespace(id=uuid4(), **kwargs)

    async def append_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        del kwargs
        self.event_types.append(event_type)
        self.event_payloads.append(dict(payload))
        return SimpleNamespace(event_id=uuid4(), event_type=event_type)

    async def get_tool_output_context_item_for_owner(
        self,
        *,
        tool_call_id: UUID,
        thread_id: UUID,
        owner_user_id: UUID,
    ) -> Any | None:
        if thread_id != THREAD_ID or owner_user_id != OWNER_ID:
            return None
        return self.tool_context_items.get(tool_call_id)
