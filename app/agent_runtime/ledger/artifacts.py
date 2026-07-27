from __future__ import annotations

from typing import Any

from .models import AgentArtifact


def artifact_event_payload(artifact: AgentArtifact) -> dict[str, Any]:
    """Return the durable public envelope consumed by Agent clients."""

    payload: dict[str, Any] = {
        "artifact_id": str(artifact.id),
        "artifact_type": artifact.artifact_type,
        "schema_version": artifact.schema_version,
        "status": artifact.status,
        "artifact": {
            "id": str(artifact.id),
            "artifact_type": artifact.artifact_type,
            "schema_version": artifact.schema_version,
            "status": artifact.status,
            "payload": dict(artifact.payload),
            "raw_payload_ref": artifact.raw_payload_ref,
        },
    }
    payload.update(
        {
            key: value
            for key, value in artifact.payload.items()
            if key
            in {
                "form",
                "card",
                "card_json",
                "cart_update",
                "summary",
            }
        }
    )
    return payload


__all__ = ["artifact_event_payload"]
