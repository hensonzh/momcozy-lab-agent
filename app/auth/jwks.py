from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from time import monotonic
from typing import Any, Protocol

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
import httpx
from jwt import InvalidKeyError, PyJWK


MAX_JWKS_BYTES = 64 * 1024
MAX_JWKS_KEYS = 8
MAX_KID_LENGTH = 128
MIN_RSA_KEY_SIZE = 2048
PRIVATE_RSA_PARAMETERS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth"})


class JwksUnavailableError(Exception):
    pass


class UnknownSigningKeyError(Exception):
    pass


class JwksFetcher(Protocol):
    async def fetch(self) -> bytes: ...


class HttpJwksFetcher:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        jwks_url: str,
    ) -> None:
        self._http_client = http_client
        self._jwks_url = jwks_url

    async def fetch(self) -> bytes:
        try:
            async with self._http_client.stream(
                "GET",
                self._jwks_url,
            ) as response:
                if response.status_code != 200:
                    raise JwksUnavailableError(
                        "JWKS endpoint returned an unsuccessful response."
                    )
                content_length = response.headers.get("content-length")
                if content_length is not None and int(content_length) > MAX_JWKS_BYTES:
                    raise JwksUnavailableError("JWKS response is too large.")
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_JWKS_BYTES:
                        raise JwksUnavailableError("JWKS response is too large.")
                return bytes(payload)
        except JwksUnavailableError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise JwksUnavailableError("JWKS endpoint is unavailable.") from exc


class JwksCache:
    def __init__(
        self,
        *,
        fetcher: JwksFetcher,
        cache_ttl_seconds: float,
        kid_miss_cooldown_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._cache_ttl_seconds = cache_ttl_seconds
        self._kid_miss_cooldown_seconds = kid_miss_cooldown_seconds
        self._clock = clock or monotonic
        self._keys: dict[str, RSAPublicKey] = {}
        self._expires_at = 0.0
        self._last_required_refresh_failed_at: float | None = None
        self._last_kid_miss_refresh_at: float | None = None
        self._last_kid_miss_refresh_failed = False
        self._refresh_lock = asyncio.Lock()

    async def ensure_ready(self) -> bool:
        if self._is_fresh():
            return True
        try:
            await self._refresh_required()
        except JwksUnavailableError:
            return False
        return self._is_fresh()

    async def get_signing_key(self, kid: str) -> RSAPublicKey:
        normalized_kid = _normalize_kid(kid)
        if self._is_fresh():
            cached_key = self._keys.get(normalized_kid)
            if cached_key is not None:
                return cached_key
            return await self._refresh_for_kid_miss(normalized_kid)

        await self._refresh_required()
        cached_key = self._keys.get(normalized_kid)
        if cached_key is None:
            raise UnknownSigningKeyError("Signing key is unknown.")
        return cached_key

    def _is_fresh(self) -> bool:
        return bool(self._keys) and self._clock() < self._expires_at

    async def _refresh_required(self) -> None:
        async with self._refresh_lock:
            if self._is_fresh():
                return
            now = self._clock()
            if (
                self._last_required_refresh_failed_at is not None
                and now - self._last_required_refresh_failed_at
                < self._kid_miss_cooldown_seconds
            ):
                raise JwksUnavailableError("JWKS refresh is cooling down.")
            try:
                await self._replace_from_fetch(now=now)
            except JwksUnavailableError:
                self._last_required_refresh_failed_at = now
                raise
            self._last_required_refresh_failed_at = None

    async def _refresh_for_kid_miss(
        self,
        kid: str,
    ) -> RSAPublicKey:
        async with self._refresh_lock:
            if not self._is_fresh():
                raise JwksUnavailableError("Cached JWKS expired.")
            cached_key = self._keys.get(kid)
            if cached_key is not None:
                return cached_key

            now = self._clock()
            if (
                self._last_kid_miss_refresh_at is not None
                and now - self._last_kid_miss_refresh_at
                < self._kid_miss_cooldown_seconds
            ):
                if self._last_kid_miss_refresh_failed:
                    raise JwksUnavailableError("JWKS refresh is cooling down.")
                raise UnknownSigningKeyError("Signing key is unknown.")

            self._last_kid_miss_refresh_at = now
            try:
                await self._replace_from_fetch(now=now)
            except JwksUnavailableError:
                self._last_kid_miss_refresh_failed = True
                raise
            self._last_kid_miss_refresh_failed = False

            refreshed_key = self._keys.get(kid)
            if refreshed_key is None:
                raise UnknownSigningKeyError("Signing key is unknown.")
            return refreshed_key

    async def _replace_from_fetch(self, *, now: float) -> None:
        try:
            payload = await self._fetcher.fetch()
            parsed_keys = _parse_jwks(payload)
        except JwksUnavailableError:
            raise
        except Exception as exc:
            raise JwksUnavailableError("JWKS response is invalid.") from exc
        self._keys = parsed_keys
        self._expires_at = now + self._cache_ttl_seconds


def _parse_jwks(payload: bytes) -> dict[str, RSAPublicKey]:
    if len(payload) > MAX_JWKS_BYTES:
        raise JwksUnavailableError("JWKS response is too large.")
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise JwksUnavailableError("JWKS response is invalid.") from exc
    if not isinstance(document, dict):
        raise JwksUnavailableError("JWKS response is invalid.")
    raw_keys = document.get("keys")
    if (
        not isinstance(raw_keys, list)
        or not raw_keys
        or len(raw_keys) > MAX_JWKS_KEYS
    ):
        raise JwksUnavailableError("JWKS key set is invalid.")

    parsed_keys: dict[str, RSAPublicKey] = {}
    for raw_key in raw_keys:
        kid, public_key = _parse_signing_key(raw_key)
        if kid in parsed_keys:
            raise JwksUnavailableError("JWKS contains duplicate key IDs.")
        parsed_keys[kid] = public_key
    return parsed_keys


def _parse_signing_key(raw_key: Any) -> tuple[str, RSAPublicKey]:
    if not isinstance(raw_key, dict):
        raise JwksUnavailableError("JWKS signing key is invalid.")
    kid = _normalize_kid(raw_key.get("kid"))
    if (
        raw_key.get("kty") != "RSA"
        or raw_key.get("use") != "sig"
        or raw_key.get("alg") != "RS256"
        or PRIVATE_RSA_PARAMETERS.intersection(raw_key)
    ):
        raise JwksUnavailableError("JWKS signing key is invalid.")
    try:
        parsed_key = PyJWK.from_dict(raw_key, algorithm="RS256").key
    except (InvalidKeyError, ValueError, TypeError) as exc:
        raise JwksUnavailableError("JWKS signing key is invalid.") from exc
    if (
        not isinstance(parsed_key, RSAPublicKey)
        or parsed_key.key_size < MIN_RSA_KEY_SIZE
    ):
        raise JwksUnavailableError("JWKS RSA key is too small.")
    return kid, parsed_key


def _normalize_kid(value: Any) -> str:
    if not isinstance(value, str):
        raise JwksUnavailableError("JWKS key ID is invalid.")
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_KID_LENGTH:
        raise JwksUnavailableError("JWKS key ID is invalid.")
    return normalized
