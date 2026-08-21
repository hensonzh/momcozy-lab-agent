from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    internal_input_schema,
)
from app.capabilities.model_input_schemas import input_schema_for_tool

from .contracts import (
    FeedingRecordsArguments,
    FeedingSummaryArguments,
    FeedingSummaryResult,
    GrowthRecordsArguments,
    GrowthSummaryArguments,
    GrowthSummaryResult,
    LactationRecordsArguments,
    LactationSummaryArguments,
    LactationSummaryResult,
    RecordsResult,
)


LACTATION_ANALYSIS_TOOL_NAMES = (
    "get_feeding_records",
    "get_feeding_summary",
    "get_growth_records",
    "get_growth_summary",
    "get_lactation_records",
    "get_lactation_summary",
)


def lactation_analysis_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    definitions: tuple[
        tuple[str, str, type[BaseModel], type[BaseModel], int], ...
    ] = (
        (
            "get_lactation_summary",
            "汇总当前用户指定窗口内的吸奶次数、实测产出和逐日覆盖，不推断亲喂量或供奶结论。"
            "当回顾吸奶表现、奶量变化或数据覆盖时使用。",
            LactationSummaryArguments,
            LactationSummaryResult,
            20,
        ),
        (
            "get_lactation_records",
            "读取当前用户指定窗口内的逐次吸奶和母乳亲喂记录，并显式披露截断。"
            "当核对某次吸奶或亲喂的时间、奶量、时长或侧别时使用。",
            LactationRecordsArguments,
            RecordsResult,
            20,
        ),
        (
            "get_feeding_summary",
            "汇总指定宝宝的喂养次数、实测体积、喂养方式和逐日覆盖，不估算亲喂摄入。"
            "当回顾单个宝宝的近期摄入记录时使用。",
            FeedingSummaryArguments,
            FeedingSummaryResult,
            20,
        ),
        (
            "get_feeding_records",
            "读取指定宝宝在选定窗口内的逐次喂养事实，并区分未测量与零体积。"
            "当核对某次亲喂、瓶喂或配方奶记录时使用。",
            FeedingRecordsArguments,
            RecordsResult,
            20,
        ),
        (
            "get_growth_summary",
            "读取指定宝宝最近两次原始生长测量及数值变化，不生成百分位、参考分类或诊断。"
            "当奶量分析需要核对近期体重、身高或头围变化时使用。",
            GrowthSummaryArguments,
            GrowthSummaryResult,
            20,
        ),
        (
            "get_growth_records",
            "读取指定宝宝在选定窗口内的原始生长测量，并显式披露结果截断。"
            "当核对具体测量日期、体重、身高或头围时使用。",
            GrowthRecordsArguments,
            RecordsResult,
            20,
        ),
    )
    for name, description, arguments, result, timeout in definitions:
        registry.register(
            ToolContract(
                name=name,
                description=description,
                input_schema=input_schema_for_tool(name),
                internal_input_schema=_internal_schema(arguments),
                output_schema=result.model_json_schema(),
                timeout_seconds=timeout,
            )
        )
    return registry


def _internal_schema(arguments: type[BaseModel]) -> dict[str, Any]:
    return internal_input_schema(
        arguments.model_json_schema(),
        trusted_properties={
            "runtime_timezone": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
            },
            "runtime_local_date": {
                "type": "string",
                "format": "date",
            },
        },
        required=("runtime_timezone", "runtime_local_date"),
    )


__all__ = [
    "LACTATION_ANALYSIS_TOOL_NAMES",
    "lactation_analysis_tool_registry",
]
