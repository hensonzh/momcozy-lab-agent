from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent_runtime.providers import create_model_provider_runtime  # noqa: E402
from app.care_reports.evaluation import ReportEvalSuite, check_case  # noqa: E402
from app.care_reports.generation import CareReportGenerator  # noqa: E402
from app.care_reports.generation_schemas import ReportGenerationInput  # noqa: E402
from app.core.errors import ApiError  # noqa: E402
from app.core.settings import Settings  # noqa: E402
from app.infrastructure.model_provider import model_provider_config  # noqa: E402


async def run(suite: ReportEvalSuite, *, live: bool) -> list[dict[str, Any]]:
    if not live:
        return [check_case(case) for case in suite.cases]
    settings = Settings.from_env()
    settings.validate_model_provider()
    provider = create_model_provider_runtime(model_provider_config(settings))
    try:
        generator = CareReportGenerator(client=provider.client, model=provider.profile.model, provider=provider.profile.provider_id,
            reasoning_effort=settings.agent_model_reasoning_effort, timeout_seconds=min(120, settings.agent_model_timeout_seconds), error_mapper=provider.error_mapper)
        rows: list[dict[str, Any]] = []
        for case in suite.cases:
            if case.expected != 'accepted':
                # Invalid candidate fixtures exercise the deterministic validator, not a requested model behavior.
                rows.append(check_case(case))
                continue
            request = ReportGenerationInput.model_validate(case.input)
            try:
                result = await generator.generate(request)
                row = check_case(case, result.content.model_dump(mode='json'))
                row['artifact'] = result.model_dump(mode='json')
                row['source_ids'] = [value.id for value in request.sources]
                rows.append(row)
            except ApiError as error:
                rows.append({'case_id': case.id, 'passed': False, 'failure_category': 'generation_error', 'error_code': error.code})
        return rows
    finally:
        await provider.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description='Validate synthetic care-report contracts, or collect live output for professional quality review.')
    parser.add_argument('--suite', type=Path, default=ROOT / 'evals/care_reports/v1/scenarios.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live', action='store_true', help='Use configured model credentials with the synthetic suite only.')
    args = parser.parse_args()
    suite = ReportEvalSuite.model_validate_json(args.suite.read_text())
    rows = asyncio.run(run(suite, live=args.live))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'schema_version': 'care_report_eval_result.v1', 'mode': 'live' if args.live else 'contract',
        'quality_approved': False, 'results': rows}, ensure_ascii=False, indent=2) + '\n')
    print(f'{sum(row["passed"] for row in rows)}/{len(rows)} contract checks passed; semantic quality requires review.')
    raise SystemExit(0 if all(row['passed'] for row in rows) else 1)


if __name__ == '__main__':
    main()
