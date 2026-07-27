import re

from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.capabilities._internal.execution import (
    create_artifact,
    validate_arguments,
)

from .contracts import IbclcConsultCardCreateArguments


class IbclcConsultCardCreateToolHandler:
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
            IbclcConsultCardCreateArguments,
            context.args,
            "IBCLC consultation card arguments are invalid.",
        )
        trusted = context.trusted_args or {}
        if not _ibclc_consult_allowed(
            current_text=str(
                trusted.get("trusted_current_user_text") or ""
            ),
            previous_assistant_text=str(
                trusted.get("trusted_previous_assistant_text") or ""
            ),
        ):
            return ToolResult.json(
                {
                    "status": "ibclc_consult_blocked",
                    "requires_confirmation": True,
                }
            )
        payload = {
            "title": "IBCLC 在线咨询",
            **arguments.model_dump(
                mode="json",
                exclude_unset=True,
            ),
            "locale": str(trusted.get("locale") or ""),
            "timezone": str(
                trusted.get("runtime_timezone") or "UTC"
            ),
        }
        artifact = await create_artifact(
            repository=self.repository,
            context=context,
            artifact_type="ibclc_consult_card",
            schema_version="v1",
            payload=payload,
        )
        return ToolResult.json(
            {
                "status": "card_created",
                "artifact_id": str(artifact.id),
                "artifact_type": artifact.artifact_type,
                "schema_version": artifact.schema_version,
                "title": payload["title"],
                "reason": arguments.reason,
                "urgency": arguments.urgency,
            }
        )


def _ibclc_consult_allowed(
    *,
    current_text: str,
    previous_assistant_text: str,
) -> bool:
    current = _normalized_consent_text(current_text)
    if _explicit_ibclc_request(current):
        return True
    if _short_affirmation(current):
        previous = _normalized_consent_text(
            previous_assistant_text
        )
        return _previous_assistant_offered_ibclc(previous)
    return False


def _explicit_ibclc_request(text: str) -> bool:
    if not text or _contains_negative_intent(text):
        return False
    subjects = (
        "ibclc",
        "哺乳顾问",
        "泌乳顾问",
        "真人哺乳咨询",
        "人工哺乳咨询",
        "咨询入口",
        "在线咨询",
    )
    actions = (
        "帮我找",
        "给我找",
        "帮我推荐",
        "给我推荐",
        "请推荐",
        "我想找",
        "我要找",
        "安排",
        "预约",
        "联系",
        "接通",
        "转接",
        "打开",
        "启动",
        "创建",
        "我想咨询",
        "我要咨询",
        "咨询一下",
        "同意推荐",
    )
    return any(subject in text for subject in subjects) and any(
        action in text for action in actions
    )


def _previous_assistant_offered_ibclc(text: str) -> bool:
    subjects = (
        "ibclc",
        "哺乳顾问",
        "泌乳顾问",
        "咨询入口",
        "在线咨询",
    )
    offers = (
        "需要我",
        "要我",
        "可以帮你",
        "帮你推荐",
        "帮你打开",
        "是否要",
    )
    return (
        not _contains_negative_intent(text)
        and any(subject in text for subject in subjects)
        and any(offer in text for offer in offers)
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


def _short_affirmation(text: str) -> bool:
    normalized = re.sub(r"[。！？!?,，、~～….\-_]", "", text)
    return normalized in {
        "ok",
        "okay",
        "yes",
        "好",
        "好的",
        "好啊",
        "可以",
        "行",
        "可以的",
    }


def _normalized_consent_text(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())

__all__ = ["IbclcConsultCardCreateToolHandler"]
