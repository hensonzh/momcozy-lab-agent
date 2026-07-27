from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.jwks import (
    HttpJwksFetcher,
    JwksCache,
    JwksUnavailableError,
    UnknownSigningKeyError,
)


def _jwks(*keys: dict[str, Any]) -> bytes:
    return json.dumps({"keys": list(keys)}, separators=(",", ":")).encode()


def _rsa_jwk(kid: str, *, key_size: int = 2048) -> dict[str, str]:
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
    )
    public_numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _base64url_uint(public_numbers.n),
        "e": _base64url_uint(public_numbers.e),
    }


def _base64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_fresh_signing_key_cache_hit_performs_no_additional_http_fetch() -> None:
    clock = FakeClock()
    fetcher = SequenceFetcher([_jwks(_rsa_jwk("key-1"))])
    cache = _cache(fetcher=fetcher, clock=clock)

    first = asyncio.run(cache.get_signing_key("key-1"))
    second = asyncio.run(cache.get_signing_key("key-1"))

    assert first.key_size == 2048
    assert second is first
    assert fetcher.calls == 1


def test_concurrent_initial_key_lookups_use_one_single_flight_refresh() -> None:
    fetcher = BlockingFetcher(_jwks(_rsa_jwk("key-1")))
    cache = _cache(fetcher=fetcher)

    async def scenario() -> None:
        lookups = [
            asyncio.create_task(cache.get_signing_key("key-1"))
            for _ in range(10)
        ]
        await fetcher.started.wait()
        fetcher.release.set()
        keys = await asyncio.gather(*lookups)
        assert len({id(key) for key in keys}) == 1

    asyncio.run(scenario())

    assert fetcher.calls == 1


def test_unknown_rotated_kid_refreshes_once_and_discovers_new_key() -> None:
    key_1 = _rsa_jwk("key-1")
    key_2 = _rsa_jwk("key-2")
    fetcher = SequenceFetcher(
        [
            _jwks(key_1),
            _jwks(key_1, key_2),
        ]
    )
    cache = _cache(fetcher=fetcher)

    asyncio.run(cache.get_signing_key("key-1"))
    rotated_key = asyncio.run(cache.get_signing_key("key-2"))

    assert rotated_key.key_size == 2048
    assert fetcher.calls == 2


def test_random_unknown_kids_are_bounded_by_global_cooldown() -> None:
    clock = FakeClock()
    key = _rsa_jwk("key-1")
    fetcher = SequenceFetcher([_jwks(key), _jwks(key), _jwks(key)])
    cache = _cache(
        fetcher=fetcher,
        clock=clock,
        kid_miss_cooldown_seconds=30,
    )
    asyncio.run(cache.get_signing_key("key-1"))

    with pytest.raises(UnknownSigningKeyError):
        asyncio.run(cache.get_signing_key("random-1"))
    with pytest.raises(UnknownSigningKeyError):
        asyncio.run(cache.get_signing_key("random-2"))

    assert fetcher.calls == 2

    clock.advance(30)
    with pytest.raises(UnknownSigningKeyError):
        asyncio.run(cache.get_signing_key("random-3"))

    assert fetcher.calls == 3


def test_failed_kid_refresh_does_not_clear_still_fresh_cached_keys() -> None:
    key = _rsa_jwk("key-1")
    fetcher = SequenceFetcher(
        [
            _jwks(key),
            JwksUnavailableError("upstream unavailable"),
        ]
    )
    cache = _cache(fetcher=fetcher)
    cached_key = asyncio.run(cache.get_signing_key("key-1"))

    with pytest.raises(JwksUnavailableError):
        asyncio.run(cache.get_signing_key("rotated-key"))

    assert asyncio.run(cache.get_signing_key("key-1")) is cached_key
    assert fetcher.calls == 2


def test_ensure_ready_is_in_memory_while_cache_is_fresh_and_refreshes_after_expiry() -> None:
    clock = FakeClock()
    key = _rsa_jwk("key-1")
    fetcher = SequenceFetcher(
        [
            _jwks(key),
            JwksUnavailableError("upstream unavailable"),
        ]
    )
    cache = _cache(fetcher=fetcher, clock=clock, cache_ttl_seconds=60)

    assert asyncio.run(cache.ensure_ready()) is True
    assert asyncio.run(cache.ensure_ready()) is True
    assert fetcher.calls == 1

    clock.advance(61)

    assert asyncio.run(cache.ensure_ready()) is False
    assert fetcher.calls == 2


def test_successful_refresh_is_not_blocked_when_ttl_is_shorter_than_cooldown() -> None:
    clock = FakeClock()
    key = _rsa_jwk("key-1")
    fetcher = SequenceFetcher([_jwks(key), _jwks(key)])
    cache = _cache(
        fetcher=fetcher,
        clock=clock,
        cache_ttl_seconds=10,
        kid_miss_cooldown_seconds=30,
    )

    asyncio.run(cache.get_signing_key("key-1"))
    clock.advance(11)
    asyncio.run(cache.get_signing_key("key-1"))

    assert fetcher.calls == 2


@pytest.mark.parametrize(
    "payload",
    (
        b"x" * (64 * 1024 + 1),
        _jwks(*[_rsa_jwk(f"key-{index}") for index in range(9)]),
        _jwks(_rsa_jwk("duplicate"), _rsa_jwk("duplicate")),
        _jwks(_rsa_jwk("small", key_size=1024)),
        _jwks({**_rsa_jwk("wrong-use"), "use": "enc"}),
        _jwks({**_rsa_jwk("wrong-alg"), "alg": "RS512"}),
    ),
)
def test_invalid_jwks_documents_fail_closed(payload: bytes) -> None:
    cache = _cache(fetcher=SequenceFetcher([payload]))

    with pytest.raises(JwksUnavailableError):
        asyncio.run(cache.get_signing_key("key-1"))


def test_http_fetcher_uses_only_configured_url_and_enforces_response_limit() -> None:
    requested_urls: list[str] = []

    async def handler(request: Any) -> Any:
        import httpx

        requested_urls.append(str(request.url))
        return httpx.Response(200, content=b"x" * (64 * 1024 + 1))

    async def scenario() -> None:
        import httpx

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as client:
            fetcher = HttpJwksFetcher(
                http_client=client,
                jwks_url="https://issuer.test/.well-known/jwks.json",
            )
            with pytest.raises(JwksUnavailableError):
                await fetcher.fetch()

    asyncio.run(scenario())

    assert requested_urls == ["https://issuer.test/.well-known/jwks.json"]


class SequenceFetcher:
    def __init__(self, outcomes: list[bytes | Exception]) -> None:
        self._outcomes = outcomes
        self.calls = 0

    async def fetch(self) -> bytes:
        outcome = self._outcomes[min(self.calls, len(self._outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class BlockingFetcher:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def fetch(self) -> bytes:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self._payload


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _cache(
    *,
    fetcher: SequenceFetcher | BlockingFetcher,
    clock: Callable[[], float] | None = None,
    cache_ttl_seconds: float = 300,
    kid_miss_cooldown_seconds: float = 30,
) -> JwksCache:
    return JwksCache(
        fetcher=fetcher,
        cache_ttl_seconds=cache_ttl_seconds,
        kid_miss_cooldown_seconds=kid_miss_cooldown_seconds,
        clock=clock,
    )
