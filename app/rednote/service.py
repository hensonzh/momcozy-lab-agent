from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.settings import Settings
from app.agent_runtime.safety import RuntimeSafetyPolicy
from .provider import AuthorizedGatewayProvider, ProviderUnavailable, RedNoteProvider
from .schemas import RedNotePost, SearchRequest, SearchResult, note_identity


class RedNoteSearchService:
    def __init__(self, provider: RedNoteProvider) -> None:
        self.provider = provider

    async def search(self, request: SearchRequest) -> SearchResult:
        try:
            # Overall budget includes response streaming, not just per-socket timeouts.
            async with asyncio.timeout(7):
                candidates = await self.provider.search(request.query, limit=20)
        except (ProviderUnavailable, TimeoutError):
            return SearchResult(status="unavailable")
        now = datetime.now(timezone.utc)
        policy = RuntimeSafetyPolicy()
        eligible = [post for post in candidates if post.relevance_score >= 0.7
            and (post.published_at is None or post.published_at <= now)
            and _safe_excerpt(post, policy)]
        # Highest relevance band first; saves win within a comparable relevance band.
        # Missing saves remain unknown, never presented as zero.
        eligible.sort(key=lambda p: (
            int(p.relevance_score * 10 + 1e-9),
            p.favorites if p.favorites is not None else -1,
            min(len(p.summary), 300) + 20 * bool(p.author) + 10 * bool(p.thumbnail),
            p.relevance_score,
            p.published_at.timestamp() if p.published_at else 0,
        ), reverse=True)
        selected: list[RedNotePost] = []
        seen: set[str] = set()
        for post in eligible:
            identity = note_identity(post.url)
            if identity in seen:
                continue
            seen.add(identity)
            selected.append(post)
            if len(selected) == request.limit:
                break
        return SearchResult(status="ok" if selected else "no_results", posts=selected)


def build_rednote_service(settings: Settings) -> RedNoteSearchService:
    return RedNoteSearchService(AuthorizedGatewayProvider(
        endpoint=settings.rednote_gateway_url,
        token=settings.rednote_gateway_token,
        authorization_reference=settings.rednote_authorization_reference,
    ))


def _safe_excerpt(post: RedNotePost, policy: RuntimeSafetyPolicy) -> bool:
    text = "\n".join((post.title, post.summary, post.author or ""))
    for decision in (policy.evaluate(text), policy.evaluate_output_rules(text)):
        if decision.decision in {"block", "escalate"} or decision.masked_text is not None:
            return False
    return True
