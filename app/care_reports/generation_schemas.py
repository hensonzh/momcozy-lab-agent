from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
import re
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

REPORT_SCHEMA_VERSION: Literal['care_report.v1'] = 'care_report.v1'
REPORT_PROMPT_VERSION: Literal['care_report.2026-09-08.v1'] = 'care_report.2026-09-08.v1'
MAX_INPUT_BYTES = 96 * 1024
_NON_ENGLISH_PROSE = re.compile(r'[\u3400-\u9fff\U00020000-\U000323af\u3040-\u30ff\u31f0-\u31ff\uac00-\ud7af\u0400-\u052f\u0600-\u06ff\u0900-\u097f]|\bcozy[\s-]*mate\b', re.IGNORECASE)


class StrictReportModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ReportSource(StrictReportModel):
    id: str = Field(min_length=1, max_length=160, pattern=r'^[a-zA-Z0-9:_.-]+$')
    kind: Literal['dialogue', 'intake', 'lactation', 'care_plan', 'baby_record']
    recorded_at: AwareDatetime
    content: str = Field(min_length=1, max_length=16000)
    truncated: bool = False


class ReportGenerationInput(StrictReportModel):
    episode_id: UUID
    purpose: Literal['daily', 'preparation'] = 'daily'
    report_date: date
    timezone: str = Field(max_length=80)
    as_of: AwareDatetime
    sources: list[ReportSource] = Field(min_length=1, max_length=120)
    omitted_count: int = Field(ge=0)

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError('A valid IANA timezone is required.') from error
        return value

    @model_validator(mode='after')
    def bounded_sources(self) -> ReportGenerationInput:
        if len({value.id for value in self.sources}) != len(self.sources):
            raise ValueError('Source identifiers must be unique.')
        if any(value.recorded_at > self.as_of for value in self.sources):
            raise ValueError('Sources must not follow the cutoff.')
        if self.report_date > self.as_of.astimezone(ZoneInfo(self.timezone)).date():
            raise ValueError('Future report dates are not available.')
        if len(self.canonical_json().encode('utf-8')) > MAX_INPUT_BYTES:
            raise ValueError('Report input exceeds its byte budget.')
        return self

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode='json'), ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    def input_hash(self) -> str:
        return sha256(self.canonical_json().encode('utf-8')).hexdigest()


class ReportCitation(StrictReportModel):
    source_id: str = Field(min_length=1, max_length=160)
    quote: str = Field(min_length=2, max_length=400)


class ReportFinding(StrictReportModel):
    text: str = Field(min_length=1, max_length=800)
    evidence: list[ReportCitation] = Field(min_length=1, max_length=5)


class StructuredCareReport(StrictReportModel):
    summary: list[ReportFinding] = Field(max_length=6)
    emotional_state: list[ReportFinding] = Field(max_length=3)
    communication_preferences: list[ReportFinding] = Field(max_length=3)
    checks: list[ReportFinding] = Field(max_length=6)
    data_gaps: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(max_length=8)

    @model_validator(mode='after')
    def has_information(self) -> StructuredCareReport:
        if not any([self.summary, self.emotional_state, self.communication_preferences, self.checks, self.data_gaps]):
            raise ValueError('A report must contain findings or explicit data gaps.')
        prose = [
            *(finding.text for group in (self.summary, self.emotional_state, self.communication_preferences, self.checks)
              for finding in group),
            *self.data_gaps,
        ]
        if any(_NON_ENGLISH_PROSE.search(text) for text in prose):
            raise ValueError('Report prose must be in English and use the current brand.')
        return self

    def validate_evidence(self, sources: list[ReportSource]) -> None:
        content = {value.id: value.content for value in sources}
        for finding in [*self.summary, *self.emotional_state, *self.communication_preferences, *self.checks]:
            seen: set[tuple[str, str]] = set()
            for citation in finding.evidence:
                if citation.source_id not in content or citation.quote not in content[citation.source_id]:
                    raise ValueError('Every finding must cite an exact excerpt of an included source.')
                key = citation.source_id, citation.quote
                if key in seen:
                    raise ValueError('Evidence must not contain duplicate citations.')
                seen.add(key)


class ReportGenerationResult(StrictReportModel):
    content: StructuredCareReport
    input_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    prompt_version: Literal['care_report.2026-09-08.v1'] = REPORT_PROMPT_VERSION
    schema_version: Literal['care_report.v1'] = REPORT_SCHEMA_VERSION
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    provider_response_id: str = Field(min_length=1, max_length=200)
