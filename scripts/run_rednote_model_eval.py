"""Exercise production model/tool prompts using synthetic posts; never query RedNote.

This is a model behavior probe, not a ledger, real-source, or medical-quality certification.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402
from app.agent import AGENT, SERVICE_SKILL_REGISTRY  # noqa: E402
from app.bootstrap import RUNTIME_DEFINITION, build_runtime_tool_registry, build_runtime_contract_catalog_snapshot  # noqa: E402
from app.agent_runtime.orchestration.openai_agents import ResponsesAgentsExecutionEngine  # noqa: E402
from app.agent_runtime.providers import create_model_provider_runtime  # noqa: E402
from app.core.errors import ApiError  # noqa: E402
from app.core.settings import Settings  # noqa: E402
from app.infrastructure.model_provider import model_provider_config  # noqa: E402
from app.rednote.provider import ProviderUnavailable  # noqa: E402
from app.rednote.schemas import RedNotePost, SearchRequest  # noqa: E402
from app.rednote.service import RedNoteSearchService  # noqa: E402


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    prompt: str
    provider_status: Literal["ok", "no_results", "unavailable"]
    posts: list[RedNotePost] = Field(max_length=20)
    expect_search: bool
    review_criteria: str


class Suite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["rednote_model_eval.v1"]
    synthetic_content: Literal[True]
    cases: list[Case]


class FixtureProvider:
    def __init__(self, case: Case) -> None:
        self.case = case

    async def search(self, query: str, *, limit: int) -> list[RedNotePost]:
        if self.case.provider_status == "unavailable":
            raise ProviderUnavailable()
        return self.case.posts[:limit]


class EvaluationPort:
    def __init__(self, case: Case) -> None:
        self.service = RedNoteSearchService(FixtureProvider(case))
        self.calls: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self.search_count = 0

    async def resolve_model_input(self, *, input_items: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
        return input_items

    async def ensure_model_request_fits(self, **kwargs: Any) -> None:
        pass

    async def invoke_tool(self, *, agent_name: str, tool_name: str, call_id: str, arguments: dict[str, Any]) -> str:
        self.calls.append({"tool": tool_name, "arguments": arguments})
        if tool_name == "load_service_skill":
            result = SERVICE_SKILL_REGISTRY.get(str(arguments["skill_id"])).to_tool_output()
            return json.dumps(result, ensure_ascii=False)
        if tool_name != "search_rednote_posts":
            raise ValueError("Unexpected tool")
        self.search_count += 1
        if self.search_count > 1:
            return '{"error":{"code":"community_search_already_performed"}}'
        result_search = await self.service.search(SearchRequest.model_validate(arguments))
        self.outputs.append(result_search.model_dump(mode="json"))
        return result_search.model_dump_json()

    async def persist_model_output(self, **kwargs: Any) -> None:
        pass

    async def record_execution_manifest(self, **kwargs: Any) -> None:
        pass

    async def publish_text_delta(self, **kwargs: Any) -> None:
        pass


async def run_live(suite: Suite) -> dict[str, Any]:
    if "search_rednote_posts" not in RUNTIME_DEFINITION.tools.tool_names:
        raise SystemExit("RedNote retrieval is disabled in the runtime catalog; live retrieval evaluation is unavailable.")
    started_at = datetime.now(timezone.utc).isoformat()
    settings = Settings.from_env()
    settings.validate_model_provider()
    provider = create_model_provider_runtime(model_provider_config(settings))
    registry = build_runtime_tool_registry()
    engine = ResponsesAgentsExecutionEngine(model=provider.model, model_name=provider.profile.model,
        tool_registry=registry, runtime=RUNTIME_DEFINITION,
        runtime_contract_catalog=build_runtime_contract_catalog_snapshot(registry=registry),
        max_turns=4, max_output_tokens=2500, reasoning_effort=settings.agent_model_reasoning_effort,
        text_verbosity=settings.agent_model_text_verbosity, store=False, base_url=provider.profile.base_url,
        timeout_seconds=45, provider_profile=provider.profile, request_policy=provider.request_policy,
        provider_error_mapper=provider.error_mapper)
    rows: list[dict[str, Any]] = []
    try:
        for case in suite.cases:
            port = EvaluationPort(case)
            row: dict[str, Any] = {"case_id": case.id, "review_criteria": case.review_criteria, "quality_review": "required"}
            try:
                async with asyncio.timeout(120):
                    response = await engine.execute(input_items=({"role": "user", "content": case.prompt},),
                        port=port, authorization_permissions=frozenset({"agent:run"}))
                row.update(answer=response.text, structural_passed=port.search_count == int(case.expect_search))
            except Exception as exc:
                row.update(structural_passed=False, error_code=exc.code if isinstance(exc, ApiError) else type(exc).__name__)
            row.update(tool_calls=port.calls, retrieval_outputs=port.outputs)
            rows.append(row)
            print(f"{case.id}: structural_passed={row['structural_passed']}; semantic review required", flush=True)
    finally:
        await provider.aclose()
    return {"schema_version": "rednote_model_eval_result.v1", "mode": "live_model_synthetic_source",
        "source_is_real": False, "quality_approved": False, "model": provider.profile.model,
        "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
        "prompt_sha256": hashlib.sha256(AGENT.instructions.encode()).hexdigest(),
        "fixture_sha256": hashlib.sha256(suite.model_dump_json().encode()).hexdigest(), "results": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=ROOT / "evals/rednote/v1/scenarios.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    suite = Suite.model_validate_json(args.suite.read_text())
    if args.live:
        report = asyncio.run(run_live(suite))
    else:
        report = {"mode": "validate_only", "case_count": len(suite.cases), "quality_approved": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    if args.live and not all(row["structural_passed"] for row in report["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
