from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias


@dataclass(frozen=True)
class ToolTextOutput:
    text: str


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


ToolOutput: TypeAlias = ToolTextOutput | ToolImageOutput | ToolFileOutput
FunctionCallOutput: TypeAlias = str | list[dict[str, Any]]


@dataclass(frozen=True)
class ToolResult:
    """Provider-neutral result appended as one function_call_output item."""

    output: tuple[ToolOutput, ...]
    audit_output: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.output:
            raise ValueError("ToolResult.output must contain at least one output block.")

    @classmethod
    def text(cls, value: str) -> ToolResult:
        return cls(output=(ToolTextOutput(text=value),))

    @classmethod
    def json(cls, value: Any) -> ToolResult:
        return cls(
            output=(
                ToolTextOutput(
                    text=json.dumps(
                        value,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                ),
            ),
            audit_output=dict(value) if isinstance(value, dict) else None,
        )

    def to_function_call_output(self) -> FunctionCallOutput:
        if len(self.output) == 1 and isinstance(self.output[0], ToolTextOutput):
            return self.output[0].text
        return [_serialize_output_block(block) for block in self.output]

    def to_observation(self) -> Any:
        if self.audit_output is not None:
            return dict(self.audit_output)
        serialized = self.to_function_call_output()
        if not isinstance(serialized, str):
            return serialized
        try:
            return json.loads(serialized)
        except json.JSONDecodeError:
            return serialized


def _serialize_output_block(block: ToolOutput) -> dict[str, Any]:
    if isinstance(block, ToolTextOutput):
        return {"type": "input_text", "text": block.text}
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
