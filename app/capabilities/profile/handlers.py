from __future__ import annotations

from datetime import date
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.agent_runtime.actions import (
    PROFILE_UPDATE_ACTION,
    ActionProposal,
    ActionProposer,
)
from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.core.errors import ApiError
from app.infrastructure.product_backend import (
    CurrentInfantLink,
    ProfileReadRequest,
    ProfileReadResponse,
)

from .contracts import ProfileReadArguments, ProfileUpdateArguments


ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)


class _ProfileReadClient(Protocol):
    async def read_profile(
        self,
        *,
        query: ProfileReadRequest,
        request_id: str,
    ) -> ProfileReadResponse: ...


class ProfileReadToolHandler:
    def __init__(self, *, client: _ProfileReadClient) -> None:
        self.client = client

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate_arguments(ProfileReadArguments, context.args)
        result = await self.client.read_profile(
            query=ProfileReadRequest(
                actor_user_id=context.actor.user_id,
                infant_scope=arguments.infant_scope,
                as_of_date=context.as_of_date,
            ),
            request_id=context.request_id,
        )
        return ToolResult.json(result.model_dump(mode="json"))


class ProfileUpdateToolHandler:
    def __init__(self, *, action_proposer: ActionProposer) -> None:
        self.action_proposer = action_proposer

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        arguments = _validate_arguments(ProfileUpdateArguments, context.args)
        reference_date = _runtime_reference_date(context)
        replacing_current = (
            "current_infants" in arguments.model_fields_set
        )
        expected_links: list[CurrentInfantLink] | None = None
        if replacing_current:
            raw_expected = (context.trusted_args or {}).get(
                "expected_current_infants"
            )
            if not isinstance(raw_expected, list):
                raise ApiError(
                    code="profile_current_infants_context_unavailable",
                    message=(
                        "Current infant relationship state is unavailable."
                    ),
                    status=503,
                )
            try:
                expected_links = [
                    CurrentInfantLink.model_validate(item)
                    for item in raw_expected
                ]
            except ValidationError as exc:
                raise ApiError(
                    code="profile_current_infants_context_invalid",
                    message=(
                        "Current infant relationship state is invalid."
                    ),
                    status=500,
                ) from exc
        payload = arguments.to_profile_payload(
            reference_date=reference_date,
            expected_current_infants=expected_links,
        ).model_dump(
            mode="json",
            exclude_unset=True,
        )
        preview = _profile_update_preview(payload)
        if replacing_current:
            preview.update(
                {
                    "previous_current_infants": payload[
                        "expected_current_infants"
                    ],
                    "proposed_current_infants": payload[
                        "current_infants"
                    ],
                }
            )
        action_type = (
            "profile.current_infants.replace"
            if replacing_current
            else PROFILE_UPDATE_ACTION
        )
        proposed = await self.action_proposer.propose_action(
            ActionProposal(
                actor_user_id=context.actor.user_id,
                run_id=context.run_id,
                action_type=action_type,
                target_type="profile",
                target_id=str(context.actor.user_id),
                side_effect_level=(
                    "medium" if replacing_current else "low"
                ),
                preview_payload=preview,
                apply_payload=payload,
                idempotency_key=(
                    f"{context.run_id}:{context.call_id}:profile-update"
                ),
            )
        )
        output: dict[str, object] = {
            "action_id": str(proposed.id),
            "action_type": proposed.action_type,
            "action_status": proposed.status,
            "requires_confirmation": proposed.requires_confirmation,
            "confirmation_policy": (
                "always" if proposed.requires_confirmation else "explicit_intent"
            ),
            "user_visible": proposed.requires_confirmation,
            "write_succeeded": proposed.status == "applied",
            "preview_payload": preview,
        }
        if proposed.status == "applied":
            output["status"] = "maternal_infant_profile_updated"
            output["updated"] = _profile_update_summary(payload)
        elif proposed.status == "failed":
            output["status"] = "action_failed"
            output["error_code"] = proposed.error_code or "agent_action_handler_error"
        return ToolResult.json(output)


def _validate_arguments(model: type[ArgumentsT], args: dict[str, Any]) -> ArgumentsT:
    try:
        return model.model_validate(args)
    except ValidationError as exc:
        raise _arguments_error(exc) from exc


def _arguments_error(exc: ValidationError) -> ApiError:
    return ApiError(
        code="validation_failed",
        message="Profile tool arguments are invalid.",
        status=422,
        details={"errors": exc.errors(include_url=False)},
    )


def _profile_update_preview(payload: dict[str, object]) -> dict[str, object]:
    summary = _profile_update_summary(payload)
    return {
        key: value
        for key, value in summary.items()
        if value not in ([], False)
    }


def _profile_update_summary(payload: dict[str, object]) -> dict[str, object]:
    preview: dict[str, object] = {}
    mother = payload.get("mother")
    if isinstance(mother, dict):
        preview["mother_fields"] = sorted(mother)

    infants = payload.get("infants")
    if isinstance(infants, list):
        preview["infants"] = [
            {
                "infant_id": str(infant.get("infant_id") or ""),
                "fields": sorted(set(infant) - {"infant_id"}),
            }
            for infant in infants
            if isinstance(infant, dict)
        ]

    if "current_infants" in payload:
        preview["current_infants_updated"] = True
    else:
        preview["current_infants_updated"] = False
    return preview


def _runtime_reference_date(context: ToolHandlerContext) -> date:
    raw = (context.trusted_args or {}).get("runtime_local_date")
    if isinstance(raw, str) and raw:
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ApiError(
                code="runtime_context_invalid",
                message="Runtime local date is invalid.",
                status=500,
            ) from exc
    if context.as_of_date is not None:
        return context.as_of_date
    raise ApiError(
        code="runtime_context_unavailable",
        message="Runtime local date is unavailable.",
        status=503,
    )
