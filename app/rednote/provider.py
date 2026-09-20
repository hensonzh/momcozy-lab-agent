from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx
from pydantic import ValidationError

from .schemas import RedNotePost, safe_https_url


class RedNoteProvider(Protocol):
    async def search(self, query: str, *, limit: int) -> list[RedNotePost]: ...


class ProviderUnavailable(Exception):
    """Safe boundary; vendor response bodies and credentials must not reach the model."""


@dataclass(frozen=True)
class AuthorizedGatewayProvider:
    """MomCozy normalized gateway contract, NOT an undocumented RedNote endpoint.

    Enable only after the upstream licence permits search, derived summaries,
    thumbnails, metrics, and durable citation display. See docs/rednote-retrieval.md.
    """

    endpoint: str
    token: str = field(repr=False)
    authorization_reference: str
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    async def search(self, query: str, *, limit: int) -> list[RedNotePost]:
        if not self.endpoint or not self.token or not self.authorization_reference:
            raise ProviderUnavailable()
        try:
            safe_https_url(self.endpoint)
            async with httpx.AsyncClient(timeout=6.0, follow_redirects=False, transport=self.transport) as client:
                async with client.stream("POST", self.endpoint,
                    headers={"Authorization": f"Bearer {self.token}"},
                    json={"query": query, "limit": limit}) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 256 * 1024:
                            raise ProviderUnavailable()
            import json
            payload = json.loads(data)
            if not isinstance(payload, dict) or not isinstance(payload.get("posts"), list):
                raise ProviderUnavailable()
            if len(payload["posts"]) > limit:
                raise ProviderUnavailable()
            posts = []
            for raw in payload["posts"]:
                try:
                    posts.append(RedNotePost.model_validate(raw))
                except ValidationError:
                    continue  # One malformed candidate must not discard valid results.
            return posts
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ProviderUnavailable() from exc
