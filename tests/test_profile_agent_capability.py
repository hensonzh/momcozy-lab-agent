from __future__ import annotations

import asyncio
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agent_runtime.actions import (
    ActionProposal,
    ActionProposed,
)
from app.agent_runtime.ledger import AgentAction
from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.capabilities.profile import (
    PROFILE_TOOL_NAMES,
    ProfileReadToolHandler,
    ProfileUpdateActionApplicator,
    ProfileUpdateToolHandler,
    profile_tool_registry,
)
from app.auth import RuntimePrincipal
from app.core.errors import ApiError, DependencyError
from app.infrastructure.product_backend import (
    ProfileReadResponse,
    ProfileUpdateApplyResponse,
)


def test_profile_tool_registry_exposes_the_canonical_tool_names() -> None:
    registry = profile_tool_registry()

    assert PROFILE_TOOL_NAMES == ("profile_read", "profile_update")
    assert registry.names_for_sdk() == PROFILE_TOOL_NAMES
    assert registry.get("profile_update").action_types == (
        "profile.update",
        "profile.current_infants.replace",
    )
    assert "operation" not in registry.get("profile_update").input_schema["properties"]
    assert registry.get("profile_update").input_schema["anyOf"] == [
        {"type": "object", "required": ["mother"]},
        {"type": "object", "required": ["infants"]},
        {"type": "object", "required": ["current_infants"]},
    ]


def test_profile_read_calls_product_backend_with_trusted_actor_and_returns_tool_result() -> None:
    actor_user_id = uuid4()
    backend = RecordingProfileBackend()
    handler = ProfileReadToolHandler(client=backend)

    result = asyncio.run(
        handler(
            _context(
                actor_user_id=actor_user_id,
                args={"infant_scope": "all"},
                as_of_date=date(2026, 7, 26),
            )
        )
    )

    assert isinstance(result, ToolResult)
    assert backend.read_query is not None
    assert backend.read_query.actor_user_id == actor_user_id
    assert backend.read_query.infant_scope == "all"
    assert backend.read_query.as_of_date == date(2026, 7, 26)
    assert backend.read_request_id == "req-profile-tool"
    assert result.canonical_output == _profile_response(infant_scope="all")


def test_profile_read_rejects_unknown_model_arguments_before_http_call() -> None:
    backend = RecordingProfileBackend()
    handler = ProfileReadToolHandler(client=backend)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            handler(
                _context(
                    actor_user_id=uuid4(),
                    args={"actor_user_id": str(uuid4())},
                )
            )
        )

    assert exc_info.value.code == "validation_failed"
    assert backend.read_query is None


def test_profile_update_creates_action_proposal_without_calling_product_backend() -> None:
    actor_user_id = uuid4()
    infant_id = uuid4()
    proposer = RecordingActionProposer()
    handler = ProfileUpdateToolHandler(action_proposer=proposer)
    context = _context(
        actor_user_id=actor_user_id,
        args={
            "mother": {
                "preferred_name": None,
                "actual_delivery_date": "2026-07-20",
            },
            "infants": [
                {
                    "infant_id": str(infant_id),
                    "feeding_mode": "mixed_feeding",
                }
            ],
        },
        as_of_date=date(2026, 7, 26),
    )

    result = asyncio.run(handler(context))

    assert isinstance(result, ToolResult)
    assert proposer.proposal is not None
    assert proposer.proposal.actor_user_id == actor_user_id
    assert proposer.proposal.run_id == context.run_id
    assert proposer.proposal.action_type == "profile.update"
    assert proposer.proposal.target_type == "profile"
    assert proposer.proposal.target_id == str(actor_user_id)
    assert proposer.proposal.idempotency_key == (
        f"{context.run_id}:{context.call_id}:profile-update"
    )
    assert proposer.proposal.apply_payload == {
        "mother": {
            "preferred_name": None,
            "actual_delivery_date": "2026-07-20",
        },
        "infants": [
            {
                "infant_id": str(infant_id),
                "feeding_mode": "mixed_feeding",
            }
        ],
        "reference_date": "2026-07-26",
    }
    assert result.canonical_output == {
        "action_id": str(proposer.action_id),
        "action_status": "proposed",
        "action_type": "profile.update",
        "confirmation_policy": "explicit_intent",
        "preview_payload": {
            "infants": [
                {
                    "fields": ["feeding_mode"],
                    "infant_id": str(infant_id),
                }
            ],
            "mother_fields": ["actual_delivery_date", "preferred_name"],
        },
        "requires_confirmation": False,
        "user_visible": False,
        "write_succeeded": False,
    }


def test_profile_update_requires_a_real_update() -> None:
    proposer = RecordingActionProposer()
    handler = ProfileUpdateToolHandler(action_proposer=proposer)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            handler(
                _context(
                    actor_user_id=uuid4(),
                    args={},
                )
            )
        )

    assert exc_info.value.code == "validation_failed"
    assert proposer.proposal is None


def test_profile_update_action_binds_all_identities_and_is_safe_to_retry_after_timeout() -> None:
    actor_user_id = uuid4()
    action_id = uuid4()
    run_id = uuid4()
    client = TimeoutOnceProfileBackend()
    applicator = ProfileUpdateActionApplicator(client=client)
    action = AgentAction(
        id=action_id,
        run_id=run_id,
        actor_user_id=actor_user_id,
        action_type="profile.update",
        target_type="profile",
        target_id=str(actor_user_id),
        status="confirmed",
        side_effect_level="low",
        preview_payload={},
        apply_payload={
            "mother": {"preferred_name": None},
            "reference_date": "2026-07-26",
        },
        idempotency_key="proposal-key",
    )

    with pytest.raises(DependencyError) as exc_info:
        asyncio.run(applicator(action))

    assert exc_info.value.retryable is True
    result = asyncio.run(applicator(action))

    assert result.resource_type == "profile"
    assert result.resource_id == str(actor_user_id)
    assert len(client.apply_calls) == 2
    for call in client.apply_calls:
        assert call["command"].actor_user_id == actor_user_id
        assert call["command"].action_id == action_id
        assert call["command"].run_id == run_id
        assert call["command"].action_type == "profile.update"
        assert call["command"].payload.model_dump(exclude_unset=True) == {
            "mother": {"preferred_name": None},
            "reference_date": date(2026, 7, 26),
        }
        assert call["idempotency_key"] == f"agent-action:{action_id}"
        assert call["request_id"] == f"agent-action:{action_id}"


def test_profile_update_action_rejects_mismatched_target_without_http_call() -> None:
    client = TimeoutOnceProfileBackend(timeout_once=False)
    action = AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type="profile.update",
        target_type="profile",
        target_id=str(uuid4()),
        status="confirmed",
        side_effect_level="low",
        preview_payload={},
        apply_payload={"mother": {"preferred_name": "Mai"}},
        idempotency_key="proposal-key",
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(ProfileUpdateActionApplicator(client=client)(action))

    assert exc_info.value.code == "agent_action_scope_violation"
    assert client.apply_calls == []


def _context(
    *,
    actor_user_id: UUID,
    args: dict[str, Any],
    as_of_date: date | None = None,
) -> ToolHandlerContext:
    return ToolHandlerContext(
        actor=RuntimePrincipal(
            user_id=actor_user_id,
            subject=str(actor_user_id),
            session_id=uuid4(),
            token_id="token-id",
            token_version=1,
            roles=frozenset({"user"}),
            permissions=frozenset(),
        ),
        run_id=uuid4(),
        tool_name="profile_read",
        call_id="call-profile",
        args=args,
        request_id="req-profile-tool",
        as_of_date=as_of_date,
    )


class RecordingActionProposer:
    def __init__(self) -> None:
        self.action_id = uuid4()
        self.proposal: ActionProposal | None = None

    async def propose_action(self, proposal: ActionProposal) -> ActionProposed:
        self.proposal = proposal
        return ActionProposed(
            id=self.action_id,
            action_type=proposal.action_type,
            status="proposed",
            requires_confirmation=False,
        )


class RecordingProfileBackend:
    def __init__(self) -> None:
        self.read_query: Any | None = None
        self.read_request_id = ""

    async def read_profile(self, *, query: Any, request_id: str) -> ProfileReadResponse:
        self.read_query = query
        self.read_request_id = request_id
        return ProfileReadResponse.model_validate(
            _profile_response(infant_scope=query.infant_scope)
        )


class TimeoutOnceProfileBackend:
    def __init__(self, *, timeout_once: bool = True) -> None:
        self.timeout_once = timeout_once
        self.apply_calls: list[dict[str, Any]] = []

    async def apply_profile_update(
        self,
        *,
        command: Any,
        idempotency_key: str,
        request_id: str,
    ) -> ProfileUpdateApplyResponse:
        self.apply_calls.append(
            {
                "command": command,
                "idempotency_key": idempotency_key,
                "request_id": request_id,
            }
        )
        if self.timeout_once:
            self.timeout_once = False
            raise DependencyError(
                code="product_backend_timeout",
                message="Product Backend request timed out.",
                status=504,
                retryable=True,
            )
        return ProfileUpdateApplyResponse.model_validate(
            {
                "status": "applied",
                "action_id": command.action_id,
                "resource_type": "profile",
                "resource_id": command.actor_user_id,
                "details": {
                    "mother_fields": ["preferred_name"],
                    "infants": [],
                    "current_infants_updated": False,
                },
                "application_events": [],
            }
        )


def _profile_response(*, infant_scope: str) -> dict[str, Any]:
    return {
        "as_of_date": "2026-07-26",
        "infant_scope": infant_scope,
        "mother": {
            "preferred_name": None,
            "age": None,
            "delivery_count": None,
            "current_delivery_method": None,
            "actual_delivery_date": None,
            "has_cesarean_history": None,
            "postpartum_days": None,
            "current_feeding_mode": None,
        },
        "infants": [],
        "missing_fields": [],
        "data_quality_issues": [],
    }
