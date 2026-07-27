from __future__ import annotations

from collections.abc import Mapping

from app.agent_runtime.actions import ActionApplyResult, ActionPolicyRule
from app.agent_runtime.ledger import AgentAction
from app.agent_runtime.ledger.artifacts import artifact_event_payload
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository

HOSPITAL_BAG_CART_UPDATE_ACTION = "hospital_bag.cart.update"
HOSPITAL_BAG_ACTION_TYPES = (HOSPITAL_BAG_CART_UPDATE_ACTION,)
HOSPITAL_BAG_ACTION_POLICY_RULES: Mapping[str, ActionPolicyRule] = {
    HOSPITAL_BAG_CART_UPDATE_ACTION: ActionPolicyRule(
        HOSPITAL_BAG_CART_UPDATE_ACTION,
        "hospital_bag_cart",
        "low",
    ),
}


class HospitalBagCartActionApplicator:
    """Persists a client-applied cart mutation inside the Runtime ledger."""

    def __init__(self, *, repository: RuntimeLedgerRepository) -> None:
        self.repository = repository

    async def __call__(self, action: AgentAction) -> ActionApplyResult:
        artifact = await self.repository.create_artifact(
            run_id=action.run_id,
            owner_user_id=action.actor_user_id,
            artifact_type="hospital_bag_cart",
            schema_version="v1",
            status="created",
            payload=dict(action.apply_payload),
        )
        return ActionApplyResult(
            resource_type="hospital_bag_cart",
            resource_id=str(artifact.id),
            details={
                "artifact_id": str(artifact.id),
                "operation": str(action.apply_payload.get("operation") or ""),
            },
            application_events=(
                {
                    "type": "artifact.created",
                    "payload": artifact_event_payload(artifact),
                },
            ),
        )
