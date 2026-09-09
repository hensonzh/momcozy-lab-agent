from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ReportThreadSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    thread_id: UUID
    shared_since: AwareDatetime
    shared_until: AwareDatetime | None = None

    @model_validator(mode='after')
    def positive_interval(self) -> ReportThreadSource:
        if self.shared_until is not None and self.shared_until <= self.shared_since:
            raise ValueError('Sharing interval must be positive.')
        return self


class ReportSourceQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    owner_user_id: UUID
    threads: list[ReportThreadSource] = Field(max_length=100)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    as_of: AwareDatetime
    limit: int = Field(default=40, ge=1, le=50)

    @model_validator(mode='after')
    def valid_window(self) -> ReportSourceQuery:
        if not self.starts_at < self.ends_at or self.ends_at - self.starts_at > timedelta(days=31):
            raise ValueError('Source window must be positive and no longer than 31 days.')
        if self.as_of < self.starts_at:
            raise ValueError('Source cutoff must not precede the requested window.')
        if len({value.thread_id for value in self.threads}) > 50:
            raise ValueError('At most 50 distinct threads may be included.')
        if len({(value.thread_id, value.shared_since, value.shared_until) for value in self.threads}) != len(self.threads):
            raise ValueError('Sharing intervals must be distinct.')
        return self


class ReportDialogueSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: UUID
    thread_id: UUID
    question_id: UUID
    answer_id: UUID
    question: str = Field(min_length=1, max_length=6000)
    answer: str = Field(min_length=1, max_length=12000)
    question_at: AwareDatetime
    answered_at: AwareDatetime
    question_truncated: bool
    answer_truncated: bool

    @model_validator(mode='after')
    def ordered_messages(self) -> ReportDialogueSource:
        if self.answered_at < self.question_at or self.question_id == self.answer_id:
            raise ValueError('Dialogue messages must be distinct and ordered.')
        return self


class ReportSourcesRead(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: list[ReportDialogueSource] = Field(max_length=50)
    available_count: int = Field(ge=0)
    omitted_count: int = Field(ge=0)
    as_of: AwareDatetime

    @model_validator(mode='after')
    def consistent_coverage(self) -> ReportSourcesRead:
        if self.available_count != len(self.items) + self.omitted_count:
            raise ValueError('Source counts must describe the included and omitted records.')
        if len({value.run_id for value in self.items}) != len(self.items):
            raise ValueError('A completed run may appear only once.')
        if any(value.answered_at > self.as_of for value in self.items):
            raise ValueError('Answers must be complete before the cutoff.')
        return self
