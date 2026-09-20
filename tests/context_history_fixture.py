from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


class PassthroughHistoryAdapter:
    """Context tests use a provider-independent request projection."""

    def project_item(
        self,
        item: dict[str, Any],
        *,
        source_manifest: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        return deepcopy(item)
