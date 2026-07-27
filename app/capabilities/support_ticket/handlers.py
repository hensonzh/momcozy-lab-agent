import re

from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.capabilities._internal.execution import (
    create_artifact,
    validate_arguments,
)

from .contracts import SupportTicketDraftCreateArguments


class SupportTicketDraftCreateToolHandler:
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
            SupportTicketDraftCreateArguments,
            context.args,
            "Support ticket draft arguments are invalid.",
        )
        if not _support_ticket_creation_confirmed(
            str(
                (context.trusted_args or {}).get(
                    "trusted_current_user_text"
                )
                or ""
            )
        ):
            return ToolResult.json(
                {
                    "status": "support_ticket_draft_blocked",
                    "requires_confirmation": True,
                }
            )
        payload = {
            "title": "售后支持工单草稿",
            **arguments.model_dump(mode="json"),
            "locale": str(
                (context.trusted_args or {}).get("locale") or ""
            ),
            "submission_status": "draft",
        }
        artifact = await create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="support_ticket_draft",
            schema_version="v1",
            payload=payload,
        )
        return ToolResult.json(
            {
                "status": "draft_created",
                "artifact_id": str(artifact.id),
                "artifact_type": artifact.artifact_type,
                "schema_version": artifact.schema_version,
                "submission_status": "draft",
            }
        )


def _support_ticket_creation_confirmed(text: str) -> bool:
    normalized = _normalized_consent_text(text)
    if not normalized or _contains_negative_intent(normalized):
        return False
    terms = (
        "需要",
        "可以",
        "好的",
        "确认",
        "同意",
        "创建",
        "帮我建",
        "建售后",
        "建工单",
        "提交工单",
        "提交售后",
        "售后工单",
        "联系客服",
        "现在帮我",
    )
    return any(term in normalized for term in terms) or bool(
        re.search(
            r"\b(?:yes|ok(?:ay)?|confirm|agree|create|submit|"
            r"contactsupport)\b",
            normalized,
        )
    )


def _contains_negative_intent(text: str) -> bool:
    negative = (
        "不要",
        "不用",
        "不需要",
        "不找",
        "不推荐",
        "别找",
        "别推荐",
        "不想咨询",
        "不咨询",
        "不用咨询",
        "别咨询",
        "不想联系",
        "不联系",
        "别联系",
        "不预约",
        "别预约",
        "不打开",
        "别打开",
        "不创建",
        "别创建",
        "取消",
        "先别",
        "先不",
        "暂时不",
        "没必要",
    )
    return any(token in text for token in negative) or bool(
        re.search(
            r"\b(?:no|notnow|donot|don't|cancel)\b",
            text,
        )
    )


def _normalized_consent_text(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())

__all__ = ["SupportTicketDraftCreateToolHandler"]
