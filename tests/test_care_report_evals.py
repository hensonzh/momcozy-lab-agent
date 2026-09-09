import json
from pathlib import Path

from app.care_reports.evaluation import ReportEvalSuite, check_case

DIRECTORY = Path(__file__).parents[1] / 'evals/care_reports/v1'


def test_care_report_seed_cases_and_schema_are_current() -> None:
    suite = ReportEvalSuite.model_validate_json((DIRECTORY / 'scenarios.json').read_text())
    assert json.loads((DIRECTORY / 'suite.schema.json').read_text()) == ReportEvalSuite.model_json_schema()
    assert len({case.id for case in suite.cases}) == len(suite.cases)
    assert all(check_case(case)['passed'] for case in suite.cases)
    assert all(check_case(case)['quality_status'] == 'requires_review' for case in suite.cases if case.expected == 'accepted')
