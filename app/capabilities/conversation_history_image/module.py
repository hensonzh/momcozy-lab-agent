from app.capability_module import (
    CapabilityDependencies,
    CapabilityModule,
    CapabilityNamespace,
)

from .handlers import ConversationHistoryImageReadToolHandler
from .registry import conversation_history_image_tool_registry


def _build_handlers(
    dependencies: CapabilityDependencies,
) -> dict[str, ConversationHistoryImageReadToolHandler]:
    del dependencies
    return {
        "conversation_history_image_read": (
            ConversationHistoryImageReadToolHandler()
        )
    }


CAPABILITY_MODULE = CapabilityModule(
    name="conversation_history_image",
    namespace=CapabilityNamespace(
        name="attachments",
        description="按权限读取当前会话历史中的图片附件。",
    ),
    registry_factory=conversation_history_image_tool_registry,
    handler_factory=_build_handlers,
)


__all__ = ["CAPABILITY_MODULE"]
