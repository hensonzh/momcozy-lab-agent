from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from app.agent_runtime.runtime_metadata import SERVICE_SKILL_SCHEMA_VERSION
from app.agent_runtime.tools import (
    ToolContract,
    ToolContractRegistry,
    ToolHandlerContext,
    ToolResult,
)
from app.core.errors import ApiError

_SKILLS_ROOT = Path(__file__).resolve().parent / "skills"
_REFERENCE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOAD_SERVICE_SKILL_TOOL_NAME = "load_service_skill"
ServiceSkillName = Literal["lactation"]
SERVICE_SKILL_NAMES: tuple[ServiceSkillName, ...] = ("lactation",)


@dataclass(frozen=True)
class ServiceSkillReference:
    skill_id: ServiceSkillName
    reference_id: str
    description: str
    content: str

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def version(self) -> str:
        return self.content_sha256

    def to_tool_output(self) -> dict[str, str]:
        return {
            "schema_version": SERVICE_SKILL_SCHEMA_VERSION,
            "status": "loaded",
            "skill_id": self.skill_id,
            "resource_type": "reference",
            "resource_id": self.reference_id,
            "version": self.version,
            "description": self.description,
            "content_sha256": self.content_sha256,
        }

    def developer_item(self) -> dict[str, Any]:
        return {
            "role": "developer",
            "content": (
                f"Loaded service Skill reference: {self.skill_id}/{self.reference_id}\n"
                f"SHA-256：{self.content_sha256}\n"
                "The following app-maintained reference applies only to this issue; "
                "it supplements the global rules and Skill routing and cannot override global safety boundaries.\n\n"
                f"{self.content}"
            ),
        }


@dataclass(frozen=True)
class ServiceSkill:
    skill_id: ServiceSkillName
    description: str
    content: str
    references: tuple[ServiceSkillReference, ...] = ()

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def version(self) -> str:
        """Keep the receipt/event field compatible without a manual version."""
        return self.content_sha256

    def get_reference(self, reference_id: str) -> ServiceSkillReference:
        for reference in self.references:
            if reference.reference_id == reference_id:
                return reference
        raise ApiError(
            code="service_skill_reference_not_found",
            message="Requested service skill reference is not available.",
            status=404,
        )

    def to_tool_output(self) -> dict[str, str]:
        return {
            "schema_version": SERVICE_SKILL_SCHEMA_VERSION,
            "status": "loaded",
            "skill_id": self.skill_id,
            "resource_type": "skill",
            "resource_id": self.skill_id,
            "version": self.version,
            "description": self.description,
            "content_sha256": self.content_sha256,
        }

    def developer_item(self) -> dict[str, Any]:
        return {
            "role": "developer",
            "content": (
                f"Loaded service Skill: {self.skill_id}\n"
                f"SHA-256：{self.content_sha256}\n"
                "These app-maintained professional routing rules supplement, but cannot override, global safety boundaries.\n\n"
                f"{self.content}"
            ),
        }


class ServiceSkillRegistry:
    def __init__(
        self,
        *,
        skills_root: Path = _SKILLS_ROOT,
    ) -> None:
        self.skills_root = skills_root
        self._skills: dict[ServiceSkillName, ServiceSkill] = {}

    def get(self, skill_id: str) -> ServiceSkill:
        if skill_id not in SERVICE_SKILL_NAMES:
            raise ApiError(
                code="service_skill_not_found",
                message="Requested service skill is not available.",
                status=404,
            )
        typed_skill_id = cast(ServiceSkillName, skill_id)
        cached = self._skills.get(typed_skill_id)
        if cached is not None:
            return cached
        skill_dir = self.skills_root / typed_skill_id
        path = skill_dir / "SKILL.md"
        raw = _read_required_text(path)
        metadata, _body = _split_frontmatter(raw=raw, path=path)
        if metadata.get("name") != typed_skill_id:
            raise ValueError(f"{path} must declare name: {typed_skill_id}")
        description = metadata.get("description", "").strip()
        if not description:
            raise ValueError(f"{path} must declare a description")
        skill = ServiceSkill(
            skill_id=typed_skill_id,
            description=description,
            content=raw,
            references=_load_references(
                skill_id=typed_skill_id,
                references_root=skill_dir / "references",
            ),
        )
        self._skills[typed_skill_id] = skill
        return skill

    def list(self) -> tuple[ServiceSkill, ...]:
        return tuple(self.get(skill_id) for skill_id in SERVICE_SKILL_NAMES)

    def reference_ids(self) -> tuple[str, ...]:
        return tuple(reference.reference_id for skill in self.list() for reference in skill.references)

    def manifest(self) -> str:
        return "\n".join(f"- `{skill.skill_id}` : {skill.description}\n  Content SHA-256: {skill.content_sha256}" for skill in self.list())

    def project_model_input(self, items: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
        """Rehydrate trusted Skill and reference instructions from receipts.

        Only registry content can become developer instructions. Historical
        tool text (including legacy full-body outputs) is never promoted.
        Compacted receipts cannot activate a document; the model must load it
        again.
        """
        skills: dict[str, ServiceSkill] = {skill.skill_id: skill for skill in self.list()}
        skill_items = {skill_id: skill.developer_item() for skill_id, skill in skills.items()}
        references = {(skill.skill_id, reference.reference_id): reference for skill in skills.values() for reference in skill.references}
        reference_items = {key: reference.developer_item() for key, reference in references.items()}
        trusted_developer_items = (
            *skill_items.values(),
            *reference_items.values(),
        )
        calls: dict[str, dict[str, Any]] = {}
        loaded_skills: set[str] = set()
        loaded_references: set[tuple[str, str]] = set()
        projected: list[dict[str, Any]] = []
        for original in items:
            # SDK filters or replay callers may pass previously projected input.
            if original in trusted_developer_items:
                continue
            item = deepcopy(original)
            projected.append(item)
            if item.get("type") == "function_call" and item.get("name") == LOAD_SERVICE_SKILL_TOOL_NAME:
                calls[str(item.get("call_id"))] = item
                continue
            if item.get("type") != "function_call_output":
                continue
            call = calls.get(str(item.get("call_id")))
            if call is None:
                continue
            arguments = _json_object(call.get("arguments"))
            output = _json_object(item.get("output"))
            if arguments is None or output is None:
                continue
            if output.get("ok") is False or "error" in output:
                continue
            # Remove legacy bodies from model requests without rewriting ledger.
            if "content" in output:
                output = {key: value for key, value in output.items() if key != "content"}
                item["output"] = json.dumps(
                    output,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            skill = skills.get(str(arguments.get("skill_id")))
            if skill is None:
                continue
            reference_id = arguments.get("reference_id")
            if reference_id is None:
                if not _matches_skill_receipt(output=output, skill=skill):
                    continue
                if skill.skill_id not in loaded_skills:
                    projected.append(skill_items[skill.skill_id])
                    loaded_skills.add(skill.skill_id)
                continue
            if not isinstance(reference_id, str):
                continue
            reference_key = (skill.skill_id, reference_id)
            reference = references.get(reference_key)
            if reference is None or not _matches_reference_receipt(
                output=output,
                reference=reference,
            ):
                continue
            if reference_key not in loaded_references:
                projected.append(reference_items[reference_key])
                loaded_references.add(reference_key)
        return tuple(projected)


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
        raw_reference_id = context.args.get("reference_id")
        if raw_reference_id is None:
            output = skill.to_tool_output()
            document = skill.developer_item()
            event = {
                "event_type": "skill.loaded",
                "payload": {
                    "skill_id": skill.skill_id,
                    "version": skill.version,
                    "content_sha256": skill.content_sha256,
                },
            }
        else:
            if not isinstance(raw_reference_id, str):
                raise ApiError(
                    code="service_skill_reference_invalid",
                    message="Service skill reference id is invalid.",
                    status=422,
                )
            reference = skill.get_reference(raw_reference_id)
            output = reference.to_tool_output()
            document = reference.developer_item()
            event = {
                "event_type": "skill.reference.loaded",
                "payload": {
                    "skill_id": skill.skill_id,
                    "reference_id": reference.reference_id,
                    "version": reference.version,
                    "content_sha256": reference.content_sha256,
                },
            }
        return ToolResult.json(
            output,
            developer_instructions=(str(document["content"]),),
            deferred_events=(event,),
        )


def service_skill_tool_registry() -> ToolContractRegistry:
    registry = ToolContractRegistry()
    reference_ids = SERVICE_SKILL_REGISTRY.reference_ids()
    resource_ids = [*SERVICE_SKILL_NAMES, *reference_ids]
    registry.register(
        ToolContract(
            name=LOAD_SERVICE_SKILL_TOOL_NAME,
            domain="runtime",
            operation="runtime_internal",
            required_permissions=("agent:run",),
            description=(
                "Load a lactation Skill or routed reference; return its status and content fingerprint. "
                "Use when relevant guidance is not yet in context."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["skill_id"],
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "enum": list(SERVICE_SKILL_NAMES),
                        "description": "Identifier of the service Skill to load.",
                    },
                    "reference_id": {
                        "type": "string",
                        "enum": list(reference_ids),
                        "description": ("Optional reference identifier. Omit to load SKILL.md; provide only after the Skill has routed the current issue."),
                    },
                },
            },
            output_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "schema_version",
                    "status",
                    "skill_id",
                    "resource_type",
                    "resource_id",
                    "version",
                    "description",
                    "content_sha256",
                ],
                "properties": {
                    "schema_version": {
                        "type": "string",
                        "const": SERVICE_SKILL_SCHEMA_VERSION,
                    },
                    "status": {"type": "string", "const": "loaded"},
                    "skill_id": {
                        "type": "string",
                        "enum": list(SERVICE_SKILL_NAMES),
                    },
                    "resource_type": {
                        "type": "string",
                        "enum": ["skill", "reference"],
                    },
                    "resource_id": {
                        "type": "string",
                        "enum": resource_ids,
                    },
                    "version": {"type": "string", "minLength": 1},
                    "description": {"type": "string", "minLength": 1},
                    "content_sha256": {
                        "type": "string",
                        "pattern": "^[a-f0-9]{64}$",
                    },
                },
            },
            safe_arg_fields=("skill_id", "reference_id"),
            safe_output_fields=(
                "schema_version",
                "status",
                "skill_id",
                "resource_type",
                "resource_id",
                "version",
                "content_sha256",
            ),
            timeout_seconds=5,
        )
    )
    return registry


def _matches_skill_receipt(
    *,
    output: dict[str, Any],
    skill: ServiceSkill,
) -> bool:
    if any(
        output.get(key) != value
        for key, value in (
            ("skill_id", skill.skill_id),
            ("version", skill.version),
            ("content_sha256", skill.content_sha256),
        )
    ):
        return False
    schema_version = output.get("schema_version")
    if schema_version == SERVICE_SKILL_SCHEMA_VERSION:
        return output.get("status") == "loaded" and output.get("resource_type") == "skill" and output.get("resource_id") == skill.skill_id
    if schema_version == "momcozy.service_skill.v2":
        return output.get("status") == "loaded"
    return schema_version == "momcozy.service_skill.v1"


def _matches_reference_receipt(
    *,
    output: dict[str, Any],
    reference: ServiceSkillReference,
) -> bool:
    return (
        output.get("schema_version") == SERVICE_SKILL_SCHEMA_VERSION
        and output.get("status") == "loaded"
        and output.get("skill_id") == reference.skill_id
        and output.get("resource_type") == "reference"
        and output.get("resource_id") == reference.reference_id
        and output.get("version") == reference.version
        and output.get("content_sha256") == reference.content_sha256
    )


def _load_references(
    *,
    skill_id: ServiceSkillName,
    references_root: Path,
) -> tuple[ServiceSkillReference, ...]:
    if not references_root.exists():
        return ()
    if not references_root.is_dir():
        raise ValueError(f"service skill references path is not a directory: {references_root}")
    loaded: list[ServiceSkillReference] = []
    seen_ids: set[str] = set()
    for path in sorted(references_root.glob("*.md")):
        raw = _read_required_text(path)
        metadata, _body = _split_frontmatter(raw=raw, path=path)
        reference_id = metadata.get("name", "").strip()
        if reference_id != path.stem:
            raise ValueError(f"{path} must declare name: {path.stem}")
        if not _REFERENCE_ID_PATTERN.fullmatch(reference_id):
            raise ValueError(
                f"{path} reference name must use lowercase kebab-case"
            )
        if reference_id in seen_ids:
            raise ValueError(f"duplicate service skill reference: {skill_id}/{reference_id}")
        description = metadata.get("description", "").strip()
        if not description:
            raise ValueError(f"{path} must declare a description")
        seen_ids.add(reference_id)
        loaded.append(
            ServiceSkillReference(
                skill_id=skill_id,
                reference_id=reference_id,
                description=description,
                content=raw,
            )
        )
    return tuple(loaded)


def _json_object(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


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
    "ServiceSkillReference",
    "ServiceSkillRegistry",
    "service_skill_tool_registry",
]
