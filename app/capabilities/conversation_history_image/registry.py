from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    internal_input_schema,
)
from app.capabilities._internal.schemas import object_output_schema
from .model_schemas import model_input_schema

from .contracts import ConversationHistoryImageReadArguments


CONVERSATION_HISTORY_IMAGE_TOOL_NAMES = (
    "conversation_history_image_read",
)


def conversation_history_image_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name="conversation_history_image_read",
            domain="files",
            operation="read",
            required_permissions=("files:read",),
            description=(
                "将当前对话历史中由智能体展示过的一张图片重新载入模型上下文。"
                "当本轮请求依赖该历史图片、但模型无法直接查看其内容时使用。"
            ),
            input_schema=model_input_schema(
                "conversation_history_image_read"
            ),
            internal_input_schema=internal_input_schema(
                ConversationHistoryImageReadArguments.model_json_schema(),
                trusted_properties={
                    "visible_image_urls": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "pattern": "^https://",
                        },
                        "uniqueItems": True,
                    },
                },
                required=("visible_image_urls",),
            ),
            output_schema=object_output_schema(),
            retry_policy="safe_read",
            timeout_seconds=10,
        )
    )
    return registry


__all__ = [
    "CONVERSATION_HISTORY_IMAGE_TOOL_NAMES",
    "conversation_history_image_tool_registry",
]
