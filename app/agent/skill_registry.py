from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    ToolHandlerContext,
    ToolResult,
)
from app.agent_runtime.runtime_metadata import SERVICE_SKILL_SCHEMA_VERSION
from app.core.errors import ApiError

from .tool_catalog import LOAD_SERVICE_SKILL_TOOL_NAME


_SKILLS_ROOT = Path(__file__).resolve().parent / "skills"
ServiceSkillName = Literal["prenatal", "lactation", "device"]
SERVICE_SKILL_NAMES: tuple[ServiceSkillName, ...] = (
    "prenatal",
    "lactation",
    "device",
)


@dataclass(frozen=True)
class ServiceSkill:
    skill_id: ServiceSkillName
    version: str
    description: str
    content: str

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def to_tool_output(self) -> dict[str, str]:
        return {
            "schema_version": SERVICE_SKILL_SCHEMA_VERSION,
            "skill_id": self.skill_id,
            "version": self.version,
            "description": self.description,
            "content": self.content,
            "content_sha256": self.content_sha256,
        }


class ServiceSkillRegistry:
    def __init__(
        self,
        *,
        skills_root: Path = _SKILLS_ROOT,
        versions: dict[ServiceSkillName, str] | None = None,
    ) -> None:
        selected_versions = versions or {
            skill_id: "v1" for skill_id in SERVICE_SKILL_NAMES
        }
        if tuple(selected_versions) != SERVICE_SKILL_NAMES:
            raise ValueError(
                "service skill versions must follow the canonical skill order"
            )
        self.skills_root = skills_root
        self.versions = MappingProxyType(dict(selected_versions))
        self._skills: dict[ServiceSkillName, ServiceSkill] = {}

    def get(self, skill_id: str) -> ServiceSkill:
        if skill_id not in self.versions:
            raise ApiError(
                code="service_skill_not_found",
                message="Requested service skill is not available.",
                status=404,
            )
        typed_skill_id = cast(ServiceSkillName, skill_id)
        cached = self._skills.get(typed_skill_id)
        if cached is not None:
            return cached
        version = self.versions[typed_skill_id]
        path = self.skills_root / typed_skill_id / version / "SKILL.md"
        raw = _read_required_text(path)
        metadata, _body = _split_frontmatter(raw=raw, path=path)
        if metadata.get("name") != typed_skill_id:
            raise ValueError(
                f"{path} must declare name: {typed_skill_id}"
            )
        if metadata.get("reference_version") != version:
            raise ValueError(
                f"{path} must declare reference_version: {version}"
            )
        description = metadata.get("description", "").strip()
        if not description:
            raise ValueError(f"{path} must declare a description")
        skill = ServiceSkill(
            skill_id=typed_skill_id,
            version=version,
            description=description,
            content=raw,
        )
        self._skills[typed_skill_id] = skill
        return skill

    def list(self) -> tuple[ServiceSkill, ...]:
        return tuple(self.get(skill_id) for skill_id in SERVICE_SKILL_NAMES)

    def manifest(self) -> str:
        return "\n".join(
            f"- `{skill.skill_id}` ({skill.version})：{skill.description}"
            for skill in self.list()
        )


@dataclass(frozen=True)
class LoadServiceSkillToolHandler:
    registry: ServiceSkillRegistry

    def __call__(self, context: ToolHandlerContext) -> ToolResult:
        raw_skill_id = context.args.get("skill_id")
        if not isinstance(raw_skill_id, str):
            raise ApiError(
                code="service_skill_invalid",
                message="Service skill id is invalid.",
                status=422,
            )
        skill = self.registry.get(raw_skill_id)
        output = skill.to_tool_output()
        return ToolResult.json(
            output,
            deferred_events=(
                {
                    "event_type": "skill.loaded",
                    "payload": {
                        "skill_id": skill.skill_id,
                        "version": skill.version,
                        "content_sha256": skill.content_sha256,
                    },
                },
            ),
        )


def service_skill_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    registry.register(
        ToolContract(
            name=LOAD_SERVICE_SKILL_TOOL_NAME,
            domain="runtime",
            operation="runtime_internal",
            required_permissions=("agent:run",),
            description=(
                "加载一个版本化服务 Skill，并以普通工具结果返回完整 SKILL.md。"
                "当当前请求需要孕期、泌乳或设备专业工作流且同版本 Skill 尚未进入上下文时使用。"
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["skill_id"],
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "enum": list(SERVICE_SKILL_NAMES),
                        "description": "要加载的服务 Skill 标识。",
                    }
                },
            },
            output_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "schema_version",
                    "skill_id",
                    "version",
                    "description",
                    "content",
                    "content_sha256",
                ],
                "properties": {
                    "schema_version": {
                        "type": "string",
                        "const": SERVICE_SKILL_SCHEMA_VERSION,
                    },
                    "skill_id": {
                        "type": "string",
                        "enum": list(SERVICE_SKILL_NAMES),
                    },
                    "version": {"type": "string", "minLength": 1},
                    "description": {"type": "string", "minLength": 1},
                    "content": {"type": "string", "minLength": 1},
                    "content_sha256": {
                        "type": "string",
                        "pattern": "^[a-f0-9]{64}$",
                    },
                },
            },
            safe_arg_fields=("skill_id",),
            safe_output_fields=(
                "schema_version",
                "skill_id",
                "version",
                "content_sha256",
            ),
            model_output_max_bytes=None,
            timeout_seconds=5,
        )
    )
    return registry


def _read_required_text(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"service skill is unavailable: {path}") from exc
    if not value.strip():
        raise ValueError(f"service skill is empty: {path}")
    return value


def _split_frontmatter(
    *,
    raw: str,
    path: Path,
) -> tuple[dict[str, str], str]:
    if not raw.startswith("---\n"):
        raise ValueError(f"{path} must start with frontmatter")
    marker_index = raw.find("\n---\n", 4)
    if marker_index == -1:
        raise ValueError(f"{path} frontmatter is not closed")
    metadata: dict[str, str] = {}
    for line in raw[4:marker_index].splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"{path} contains invalid frontmatter")
        metadata[key.strip()] = value.strip()
    body = raw[marker_index + 5 :].strip()
    if not body:
        raise ValueError(f"{path} body is empty")
    return metadata, body


SERVICE_SKILL_REGISTRY = ServiceSkillRegistry()


__all__ = [
    "SERVICE_SKILL_REGISTRY",
    "SERVICE_SKILL_NAMES",
    "SERVICE_SKILL_SCHEMA_VERSION",
    "LoadServiceSkillToolHandler",
    "ServiceSkill",
    "ServiceSkillName",
    "ServiceSkillRegistry",
    "service_skill_tool_registry",
]
