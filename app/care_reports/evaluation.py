from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .generation_schemas import ReportGenerationInput, StructuredCareReport


class ReportEvalCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z0-9_]+$')
    priority: Literal['p0', 'p1']
    scenario: str
    input: dict[str, Any]
    candidate: dict[str, Any]
    expected: Literal['accepted', 'invalid_input', 'invalid_output']
    quality_rubric: list[str] = Field(min_length=1)


class ReportEvalSuite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['care_report_eval.v1']
    suite: Literal['care_reports']
    cases: list[ReportEvalCase] = Field(min_length=1)


def check_case(case: ReportEvalCase, candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    observed = 'accepted'
    try:
        request = ReportGenerationInput.model_validate(case.input)
    except ValidationError:
        observed = 'invalid_input'
    else:
        try:
            report = StructuredCareReport.model_validate(case.candidate if candidate is None else candidate)
            report.validate_evidence(request.sources)
        except ValueError:
            observed = 'invalid_output'
    return {'case_id': case.id, 'priority': case.priority, 'expected': case.expected, 'observed': observed,
        'passed': observed == case.expected, 'failure_category': None if observed == case.expected else 'contract_mismatch',
        'quality_status': 'requires_review' if observed == 'accepted' else 'not_generated', 'quality_rubric': case.quality_rubric}
