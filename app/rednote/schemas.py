from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=2, max_length=120, description="2～6个匿名母婴搜索关键词，不含姓名、联系方式、地址或健康档案。")
    limit: int = Field(default=3, ge=1, le=3, strict=True, description="最多返回的高相关帖子数量，通常为3，不足时不凑数。")

    @field_validator("query")
    @classmethod
    def keywords_only(cls, value: str) -> str:
        if re.search(r"https?://|[\w.+-]+@[\w.-]+\.[a-z]+|\d{7,}|[\x00-\x1f]", value, re.I):
            raise ValueError("Use anonymous search keywords, without links or personal identifiers.")
        return value


def safe_https_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or re.search(r"[\s\\\x00-\x1f]", value)):
        raise ValueError("An absolute HTTPS URL without credentials is required.")
    return value


def note_identity(value: str) -> str:
    safe_https_url(value)
    parsed = urlsplit(value)
    if parsed.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com", "www.rednote.com", "rednote.com"}:
        raise ValueError("A RedNote original post URL is required.")
    match = re.fullmatch(r"/(?:explore|discovery/item)/([a-fA-F0-9]{24})/?", parsed.path)
    if not match:
        raise ValueError("A canonical RedNote post URL is required.")
    return match.group(1).lower()


class RedNotePost(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=2, max_length=160)
    summary: str = Field(min_length=20, max_length=600)
    thumbnail: str | None = Field(default=None, max_length=2048)
    author: str | None = Field(default=None, max_length=120)
    favorites: int | None = Field(default=None, ge=0, strict=True)
    url: str = Field(max_length=2048)
    published_at: datetime | None = None
    relevance_score: float = Field(ge=0, le=1, allow_inf_nan=False)

    @field_validator("url")
    @classmethod
    def original_post(cls, value: str) -> str:
        note_identity(value)
        return value

    @field_validator("thumbnail", mode="before")
    @classmethod
    def optional_image(cls, value: object) -> str | None:
        if not isinstance(value, str) or not value.strip() or len(value) > 2048:
            return None
        try:
            return safe_https_url(value.strip())
        except ValueError:
            return None

    @field_validator("published_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("published_at requires a timezone")
        return value


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok", "no_results", "unavailable"]
    posts: list[RedNotePost] = Field(default_factory=list, max_length=3)
    source: Literal["rednote"] = "rednote"
    evidence_type: Literal["community_experience"] = "community_experience"


class RedNoteCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_type: Literal["rednote_posts"] = "rednote_posts"
    title: Literal["参考帖子"] = "参考帖子"
    evidence_type: Literal["community_experience"] = "community_experience"
    posts: list[RedNotePost] = Field(min_length=1, max_length=3)
