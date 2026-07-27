from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    uri: str
    size_bytes: int
    content_type: str


class ObjectStore(Protocol):
    async def put_bytes(
        self,
        *,
        key: str,
        body: bytes,
        content_type: str,
    ) -> StoredObject: ...
