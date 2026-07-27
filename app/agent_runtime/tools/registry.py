from __future__ import annotations

from collections.abc import Collection

from app.core.errors import ApiError

from .contracts import ToolContract


class ToolContractRegistry:
    def __init__(self) -> None:
        self._contracts: dict[str, ToolContract] = {}

    def register(self, contract: ToolContract) -> None:
        if contract.name in self._contracts:
            raise ApiError(
                code="conflict",
                message="Tool contract is already registered.",
                status=409,
            )
        self._contracts[contract.name] = contract

    def get(self, name: str) -> ToolContract:
        contract = self._contracts.get(name)
        if contract is None:
            raise ApiError(
                code="not_found",
                message="Tool contract not found.",
                status=404,
            )
        return contract

    def list(self) -> list[ToolContract]:
        return list(self._contracts.values())

    def names_for_sdk(self) -> tuple[str, ...]:
        return tuple(sorted(self._contracts))

    def validate_action_bindings(
        self,
        *,
        policy_action_types: Collection[str],
        handler_action_types: Collection[str],
    ) -> None:
        policy_names = set(policy_action_types)
        handler_names = set(handler_action_types)
        required = {
            action_type
            for contract in self._contracts.values()
            for action_type in contract.action_types
        }
        missing_policy = sorted(required - policy_names)
        if missing_policy:
            raise ValueError(f"tool action types missing policy bindings: {missing_policy}")
        missing_handlers = sorted(required - handler_names)
        if missing_handlers:
            raise ValueError(f"tool action types missing handler bindings: {missing_handlers}")
