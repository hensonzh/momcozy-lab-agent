from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any
from xml.etree import ElementTree


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.agent_runtime.evals.behavior import (  # noqa: E402
    BEHAVIOR_REPORT_SCHEMA_VERSION,
    BehaviorEvalResult,
    ObservedReplay,
    encode_report,
    evaluate_behavior_case,
    load_behavior_run_map,
    load_behavior_suite,
)
from app.agent_runtime.replay import (  # noqa: E402
    RuntimeReplayRepository,
    RuntimeReplayService,
)
from app.core.observability import configure_logging  # noqa: E402
from app.core.settings import get_settings  # noqa: E402
from app.infrastructure.db import (  # noqa: E402
    create_db_engine,
    create_session_factory,
)


async def evaluate_from_runtime_database(
    *,
    suite_path: Path,
    run_map_path: Path,
) -> dict[str, Any]:
    suite = load_behavior_suite(suite_path)
    active_cases = tuple(
        case for case in suite.cases if case.status == "active"
    )
    run_map = load_behavior_run_map(run_map_path)
    expected_case_ids = {case.id for case in active_cases}
    mapped_case_ids = set(run_map.runs)
    if mapped_case_ids != expected_case_ids:
        missing = sorted(expected_case_ids - mapped_case_ids)
        unknown = sorted(mapped_case_ids - expected_case_ids)
        raise ValueError(
            "run map must contain every active case exactly once; "
            f"missing={missing}, unknown={unknown}"
        )

    settings = get_settings()
    settings.validate_for_startup()
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    results: list[BehaviorEvalResult] = []
    try:
        async with session_factory() as session:
            replay_service = RuntimeReplayService(
                repository=RuntimeReplayRepository(session)
            )
            for case in active_cases:
                run_id = run_map.runs[case.id]
                bundle = await replay_service.export_run_bundle(
                    run_id=run_id,
                    include_message_content=False,
                )
                results.append(
                    await evaluate_behavior_case(
                        case=case,
                        observed=ObservedReplay.from_runtime_database(
                            run_id=run_id,
                            bundle=bundle,
                        ),
                    )
                )
    finally:
        await engine.dispose()
    return _evaluation_report(
        suite_id=suite.suite_id,
        case_count=len(suite.cases),
        results=results,
    )


def validation_report(*, suite_path: Path) -> dict[str, Any]:
    suite = load_behavior_suite(suite_path)
    return {
        "schema_version": BEHAVIOR_REPORT_SCHEMA_VERSION,
        "suite_id": suite.suite_id,
        "catalog_valid": True,
        "catalog_cases": len(suite.cases),
        "evaluated": 0,
        "structural_passed": 0,
        "failed": 0,
        "review_required": 0,
        "overall_status": "catalog_valid",
        "results": [],
    }


def _evaluation_report(
    *,
    suite_id: str,
    case_count: int,
    results: list[BehaviorEvalResult],
) -> dict[str, Any]:
    failed = sum(
        result.release_status == "failed" for result in results
    )
    review_required = sum(
        result.release_status == "review_required" for result in results
    )
    structural_passed = sum(
        result.structural_pass for result in results
    )
    if failed:
        overall_status = "failed"
    elif review_required:
        overall_status = "review_required"
    else:
        overall_status = "passed"
    return {
        "schema_version": BEHAVIOR_REPORT_SCHEMA_VERSION,
        "suite_id": suite_id,
        "catalog_valid": True,
        "catalog_cases": case_count,
        "evaluated": len(results),
        "structural_passed": structural_passed,
        "failed": failed,
        "review_required": review_required,
        "overall_status": overall_status,
        "results": [result.to_dict() for result in results],
    }


def _junit_xml(report: dict[str, Any]) -> str:
    results = report.get("results")
    if not isinstance(results, list) or not results:
        failed = report.get("overall_status") == "failed"
        suite = ElementTree.Element(
            "testsuite",
            {
                "name": "behavior-eval-catalog",
                "tests": "1",
                "failures": "1" if failed else "0",
                "skipped": "0",
            },
        )
        case = ElementTree.SubElement(
            suite,
            "testcase",
            {
                "classname": "behavior_eval.schema",
                "name": "catalog_validation",
            },
        )
        if failed:
            failure = ElementTree.SubElement(
                case,
                "failure",
                {"message": "behavior eval catalog or run map is invalid"},
            )
            failure.text = encode_report(
                {"errors": report.get("errors", [])}
            )
        return ElementTree.tostring(
            suite,
            encoding="unicode",
            xml_declaration=True,
        )

    failures = sum(
        item.get("release_status") == "failed" for item in results
    )
    skipped = sum(
        item.get("release_status") == "review_required"
        for item in results
    )
    suite = ElementTree.Element(
        "testsuite",
        {
            "name": str(report.get("suite_id") or "behavior-eval"),
            "tests": str(len(results)),
            "failures": str(failures),
            "skipped": str(skipped),
        },
    )
    for item in results:
        case = ElementTree.SubElement(
            suite,
            "testcase",
            {
                "classname": "behavior_eval.runtime_replay",
                "name": str(item.get("case_id") or "unknown"),
            },
        )
        properties = ElementTree.SubElement(case, "properties")
        ElementTree.SubElement(
            properties,
            "property",
            {
                "name": "run_id",
                "value": str(item.get("run_id") or ""),
            },
        )
        if item.get("release_status") == "failed":
            failure = ElementTree.SubElement(
                case,
                "failure",
                {"message": "behavior eval failed"},
            )
            failure.text = encode_report(
                {"failures": item.get("failures", [])}
            )
        elif item.get("release_status") == "review_required":
            ElementTree.SubElement(
                case,
                "skipped",
                {
                    "message": (
                        "structural pass; live model judge or manual "
                        "rubric review required"
                    )
                },
            )
    return ElementTree.tostring(
        suite,
        encoding="unicode",
        xml_declaration=True,
    )


def _write_text(*, path: Path | None, content: str) -> None:
    if path is None:
        print(content)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the versioned behavior catalog or evaluate mapped "
            "Runtime database replay bundles."
        )
    )
    parser.add_argument(
        "--suite",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "evals"
            / "behavior"
            / "v1"
            / "scenarios.json"
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument(
        "--run-map",
        type=Path,
        help=(
            "Strict case-to-run UUID map. Every bundle is loaded from the "
            "Runtime database; catalog data is never treated as observed trace."
        ),
    )
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        environment=settings.app_env,
        version=settings.app_version,
        process="behavior-eval",
    )

    try:
        if args.validate_only:
            report = validation_report(suite_path=args.suite)
        else:
            assert args.run_map is not None
            report = asyncio.run(
                evaluate_from_runtime_database(
                    suite_path=args.suite,
                    run_map_path=args.run_map,
                )
            )
    except Exception as exc:
        report = {
            "schema_version": BEHAVIOR_REPORT_SCHEMA_VERSION,
            "catalog_valid": False,
            "evaluated": 0,
            "structural_passed": 0,
            "failed": 1,
            "review_required": 0,
            "overall_status": "failed",
            "errors": [str(exc)],
            "results": [],
        }

    encoded = encode_report(report)
    _write_text(path=args.output_json, content=encoded)
    if args.junit is not None:
        _write_text(path=args.junit, content=_junit_xml(report))

    if report["overall_status"] == "failed":
        return 1
    if report["overall_status"] == "review_required":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
