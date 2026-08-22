from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Any, Protocol

from app.agent_runtime.ledger import AgentRun
from app.infrastructure.product_backend import (
    ProductBackendClient,
    ProfileReadRequest,
)


VERIFIED_FORM_PREFIX = "runtime_verified_form_submission\n"
MARKDOWN_IMAGE_URL_PATTERN = re.compile(
    r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))"
)


class _WorkflowRepository(Protocol):
    async def get_latest_user_message_for_run(
        self,
        *,
        run_id: Any,
    ) -> Any | None: ...

class TrustedToolArgumentsProvider:
    """Build Runtime-owned tool inputs from durable, verified context."""

    def __init__(
        self,
        *,
        repository: _WorkflowRepository,
        product_client: ProductBackendClient,
    ) -> None:
        self.repository = repository
        self.product_client = product_client

    async def build(
        self,
        *,
        run: AgentRun,
        tool_name: str,
        model_args: dict[str, Any],
        context_records: Sequence[Any],
        as_of_date: Any,
    ) -> dict[str, Any]:
        user_message = (
            await self.repository.get_latest_user_message_for_run(
                run_id=run.id
            )
        )
        message_content = (
            dict(user_message.content)
            if user_message is not None
            and isinstance(user_message.content, dict)
            else {}
        )
        user_text = str(message_content.get("text") or "").strip()
        raw_client_context = message_content.get("client_context")
        client_context = (
            dict(raw_client_context)
            if isinstance(raw_client_context, dict)
            else {}
        )
        timezone_name = str(
            client_context.get("timezone") or "UTC"
        )
        locale = str(client_context.get("locale") or "")
        local_date = (
            as_of_date.isoformat()
            if as_of_date is not None
            else str(client_context.get("as_of_date") or "")
        )
        common = {
            "trusted_current_user_text": user_text,
            "runtime_timezone": timezone_name,
            "runtime_local_date": local_date,
        }
        if tool_name in {
            "profile_read",
            "schedule_timeline_read",
            "get_lactation_summary",
            "get_lactation_records",
            "get_feeding_summary",
            "get_feeding_records",
            "get_growth_summary",
            "get_growth_records",
        }:
            return {
                "runtime_timezone": timezone_name,
                "runtime_local_date": local_date,
            }
        if tool_name == "profile_update":
            trusted: dict[str, Any] = {
                "runtime_local_date": local_date,
            }
            if "current_infants" in model_args:
                profile = await self.product_client.read_profile(
                    query=ProfileReadRequest(
                        actor_user_id=run.actor_user_id,
                        infant_scope="all",
                        as_of_date=as_of_date,
                    ),
                    request_id=run.request_id,
                )
                trusted["expected_current_infants"] = [
                    {
                        "infant_id": str(infant.infant_id),
                        "birth_order": infant.birth_order,
                    }
                    for infant in profile.infants
                    if infant.is_current_delivery
                    and infant.birth_order is not None
                ]
            return trusted
        if tool_name == "schedule_timeline_mutate":
            return {
                **common,
                "runtime_source": "agent",
            }
        if tool_name == "ibclc_consult_card_create":
            return {
                "trusted_current_user_text": user_text,
                "trusted_previous_assistant_text": (
                    _previous_assistant_text(
                        context_records,
                        run_id=run.id,
                    )
                ),
                "locale": locale,
                "runtime_timezone": timezone_name,
            }
        if tool_name == "support_ticket_draft_create":
            return {
                "trusted_current_user_text": user_text,
                "locale": locale,
            }
        if tool_name == "conversation_history_image_read":
            return {
                "visible_image_urls": _visible_image_urls(
                    context_records
                )
            }
        return {}

def _previous_assistant_text(
    records: Sequence[Any],
    *,
    run_id: Any,
) -> str:
    current_user_seen = False
    for record in reversed(records):
        item = getattr(record, "item", {})
        role = item.get("role")
        if (
            getattr(record, "run_id", None) == run_id
            and role == "user"
        ):
            current_user_seen = True
            continue
        if current_user_seen and role == "assistant":
            return _content_text(item.get("content"))
    return ""


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    for block in content:
        text = block.get("text") if isinstance(block, dict) else None
        if (
            isinstance(block, dict)
            and block.get("type") == "input_text"
            and isinstance(text, str)
            and not text.startswith(VERIFIED_FORM_PREFIX)
        ):
            return text.strip()
    return ""


def _visible_image_urls(
    records: Sequence[Any],
) -> list[str]:
    urls: list[str] = []
    for record in records:
        item = getattr(record, "item", {})
        if not isinstance(item, dict):
            continue
        if item.get("role") == "assistant":
            content = item.get("content")
            if isinstance(content, str):
                for match in MARKDOWN_IMAGE_URL_PATTERN.finditer(
                    content
                ):
                    markdown_url = str(
                        match.group(1) or match.group(2) or ""
                    ).strip()
                    if (
                        markdown_url.startswith("https://")
                        and markdown_url not in urls
                    ):
                        urls.append(markdown_url)
        output = item.get("output")
        blocks = output if isinstance(output, list) else []
        for block in blocks:
            block_url = (
                block.get("image_url")
                if isinstance(block, dict)
                else None
            )
            if (
                isinstance(block_url, str)
                and block_url.startswith("https://")
                and block_url not in urls
            ):
                urls.append(block_url)
    return urls
