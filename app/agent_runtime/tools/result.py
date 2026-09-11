from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias


@dataclass(frozen=True)
class ToolImageOutput:
    image_url: str | None = None
    file_id: str | None = None
    detail: Literal["auto", "low", "high", "original"] = "auto"

    def __post_init__(self) -> None:
        if bool(self.image_url) == bool(self.file_id):
            raise ValueError("ToolImageOutput requires exactly one of image_url or file_id.")


@dataclass(frozen=True)
class ToolFileOutput:
    file_id: str | None = None
    file_url: str | None = None
    file_data: str | None = None
    filename: str | None = None
    detail: Literal["auto", "low", "high"] | None = None

    def __post_init__(self) -> None:
        locators = (self.file_id, self.file_url, self.file_data)
        if sum(bool(locator) for locator in locators) != 1:
            raise ValueError("ToolFileOutput requires exactly one of file_id, file_url, or file_data.")


ToolMediaOutput: TypeAlias = ToolImageOutput | ToolFileOutput
FunctionCallOutput: TypeAlias = str | list[dict[str, Any]]


@dataclass(frozen=True)
class ToolResult:
    """A persisted canonical result and the explicit result shown to the model."""

    canonical_output: dict[str, Any]
    model_output: dict[str, Any]
    supplemental_content: tuple[ToolMediaOutput, ...] = ()
    deferred_events: tuple[dict[str, Any], ...] = ()

    @classmethod
    def json(
        cls,
        value: dict[str, Any],
        *,
        model_output: dict[str, Any] | None = None,
        supplemental_content: tuple[ToolMediaOutput, ...] = (),
        deferred_events: tuple[dict[str, Any], ...] = (),
    ) -> ToolResult:
        return cls(
            canonical_output=deepcopy(value),
            model_output=deepcopy(value if model_output is None else model_output),
            supplemental_content=supplemental_content,
            deferred_events=tuple(deepcopy(deferred_events)),
        )

    def to_function_call_output(
        self,
        *,
        max_bytes: int | None = None,
    ) -> FunctionCallOutput:
        primary = self._serialized_model_output()
        if not self.supplemental_content:
            output: FunctionCallOutput = primary
        else:
            output = [
                {"type": "input_text", "text": primary},
                *(_serialize_media_block(block) for block in self.supplemental_content),
            ]
        if max_bytes is not None and _function_output_bytes(output) > max_bytes:
            raise ValueError("Tool model output exceeds its declared byte limit.")
        return output

    def _serialized_model_output(self) -> str:
        return json.dumps(
            self.model_output,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _function_output_bytes(output: FunctionCallOutput) -> int:
    if isinstance(output, str):
        return len(output.encode("utf-8"))
    return len(
        json.dumps(
            output,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _serialize_media_block(block: ToolMediaOutput) -> dict[str, Any]:
    if isinstance(block, ToolImageOutput):
        payload: dict[str, Any] = {"type": "input_image", "detail": block.detail}
        if block.image_url:
            payload["image_url"] = block.image_url
        if block.file_id:
            payload["file_id"] = block.file_id
        return payload

    payload = {"type": "input_file"}
    for key in ("file_id", "file_url", "file_data", "filename", "detail"):
        value = getattr(block, key)
        if value is not None:
            payload[key] = value
    return payload
