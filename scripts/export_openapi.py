from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.core.settings import Settings  # noqa: E402
from app.factory import create_app  # noqa: E402


def build_openapi_document() -> dict[str, Any]:
    """Build the public schema without opening any external connection."""

    app = create_app(
        Settings(
            app_env="test",
            database_url=(
                "postgresql+asyncpg://runtime:runtime@"
                "database.invalid:5432/runtime"
            ),
            redis_url="redis://cache.invalid:6379/15",
            product_backend_base_url="https://product-backend.invalid",
            product_backend_service_key=(
                "openapi-export-test-service-key-32-bytes"
            ),
            auth_jwks_url=(
                "https://identity.invalid/.well-known/jwks.json"
            ),
            auth_jwt_issuer="https://identity.invalid",
        )
    )
    return app.openapi()


def render_openapi_document() -> str:
    return (
        json.dumps(
            build_openapi_document(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the Agent Runtime public OpenAPI schema."
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_openapi_document(), encoding="utf-8")


if __name__ == "__main__":
    main()
