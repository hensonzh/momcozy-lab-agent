from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ErrorEnvelope:
    code: str
    message: str
    status: int
    request_id: str = ""
    details: dict[str, Any] | None = None

    def to_response_body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "request_id": self.request_id,
                "details": self.details or {},
            }
        }


class ApiError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        status: int,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}
        self.headers = headers or {}


class DependencyError(ApiError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        status: int = 502,
        retryable: bool,
        dependency_status: int | None = None,
        issue: dict[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {"retryable": retryable}
        if dependency_status is not None:
            details["dependency_status"] = dependency_status
        if issue is not None:
            details["issue"] = issue
        super().__init__(
            code=code,
            message=message,
            status=status,
            details=details,
        )
        self.retryable = retryable
        self.dependency_status = dependency_status
