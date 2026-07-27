from app.agent_runtime.tools import (
    ToolHandlerContext,
    ToolImageOutput,
    ToolResult,
)
from app.capabilities._internal.execution import validate_arguments
from app.core.errors import ApiError

from .contracts import ConversationHistoryImageReadArguments


class ConversationHistoryImageReadToolHandler:
    async def __call__(
        self,
        context: ToolHandlerContext,
    ) -> ToolResult:
        arguments = validate_arguments(
            ConversationHistoryImageReadArguments,
            context.args,
            "Conversation history image arguments are invalid.",
        )
        raw_visible_urls = (context.trusted_args or {}).get(
            "visible_image_urls"
        )
        visible_urls = (
            {
                value
                for value in raw_visible_urls
                if isinstance(value, str)
            }
            if isinstance(raw_visible_urls, list)
            else set()
        )
        if arguments.image_url not in visible_urls:
            raise ApiError(
                code="image_reference_not_visible",
                message=(
                    "The selected image is not visible in the "
                    "current conversation thread."
                ),
                status=422,
            )
        metadata = {
            "status": "image_context_ready",
            "image_url": arguments.image_url,
            "detail": arguments.detail,
            "agent_instruction": (
                "这是当前线程中由智能体工具或 artifact 此前展示的目标图片。"
                "只依据图片可见内容回答。"
            ),
        }
        image = ToolImageOutput(
            image_url=arguments.image_url,
            detail=arguments.detail,
        )
        return ToolResult.json(
            metadata,
            supplemental_content=(image,),
        )

__all__ = ["ConversationHistoryImageReadToolHandler"]
