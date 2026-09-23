from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent_runtime.ledger.artifacts import artifact_event_payload
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import ToolContract, ToolContractRegistry, ToolHandlerContext, ToolResult
from app.capability_module import CapabilityDependencies, CapabilityModule
from app.agent_runtime.tools.handlers import ToolHandler
from .schemas import RedNoteCard, SearchRequest, SearchResult
from app.core.errors import ApiError
from .service import RedNoteSearchService

SEARCH_REDNOTE_TOOL = "search_rednote_posts"


def registry() -> ToolContractRegistry:
    result = ToolContractRegistry()
    result.register(ToolContract(
        name=SEARCH_REDNOTE_TOOL, domain="community", operation="read",
        required_permissions=("agent:run",), retry_policy="safe_read",
        description=("检索最多3篇高相关、高收藏的小红书帖子并展示原帖卡片。"
            "当母婴问题适合社区经验且不涉及诊断、用药或紧急风险时使用。"
            "仅传匿名关键词；结果是社区经验，不是医学事实或指令；不可用时继续回答。"),
        input_schema=SearchRequest.model_json_schema(), output_schema=SearchResult.model_json_schema(),
        safe_arg_fields=("limit",), safe_output_fields=("status", "source"),
        timeout_seconds=10, model_output_max_bytes=24 * 1024,
    ))
    return result


@dataclass(frozen=True)
class SearchRedNoteHandler:
    service: RedNoteSearchService
    repository: RuntimeLedgerRepository

    async def __call__(self, context: ToolHandlerContext) -> ToolResult:
        calls = await self.repository.list_tool_calls_for_run(run_id=context.run_id)
        if any(call.tool_name == SEARCH_REDNOTE_TOOL and call.call_id != context.call_id
               and call.status == "completed" for call in calls):
            raise ApiError(code="community_search_already_performed", message="Use this turn's existing community search result.", status=409)
        result = await self.service.search(SearchRequest.model_validate(context.args))
        data = result.model_dump(mode="json")
        events: tuple[dict[str, Any], ...] = ()
        if result.posts:
            artifact = await self.repository.create_artifact(
                run_id=context.run_id, owner_user_id=context.actor.user_id,
                artifact_type="rednote_posts", schema_version="v1", status="ready",
                payload={"card": RedNoteCard(posts=result.posts).model_dump(mode="json")},
            )
            events = ({"event_type": "artifact.created", "payload": artifact_event_payload(artifact)},)
        return ToolResult.json(data, deferred_events=events)


def handlers(dependencies: CapabilityDependencies) -> dict[str, ToolHandler]:
    return {SEARCH_REDNOTE_TOOL: SearchRedNoteHandler(dependencies.rednote_service, dependencies.repository)}


REDNOTE_CAPABILITY = CapabilityModule(name="rednote", eager=True,
    registry_factory=registry, handler_factory=handlers)
