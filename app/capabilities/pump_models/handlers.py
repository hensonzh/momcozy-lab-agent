from app.agent_runtime.tools import ToolHandlerContext, ToolResult
from app.capabilities._internal.execution import validate_arguments

from .contracts import PumpModelsReadArguments
from .references import PumpModelsReferenceService


class PumpModelsReadToolHandler:
    def __init__(
        self,
        *,
        reference_service: PumpModelsReferenceService | None = None,
    ) -> None:
        self.reference_service = (
            reference_service or PumpModelsReferenceService()
        )

    async def __call__(
        self,
        context: ToolHandlerContext,
    ) -> ToolResult:
        validate_arguments(
            PumpModelsReadArguments,
            context.args,
            "Pump model arguments are invalid.",
        )
        return ToolResult.json(self.reference_service.result)

__all__ = ["PumpModelsReadToolHandler"]
