from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class HistoryInputAdapter(Protocol):
    """Request projection boundary; durable history is always runtime-owned.

    The execution manifest is opaque provenance supplied by the ledger. Only
    the adapter interprets it. Implementations must not mutate their inputs.
    """

    def project_item(
        self,
        item: dict[str, Any],
        *,
        source_manifest: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None: ...
