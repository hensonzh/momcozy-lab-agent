from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from uuid import UUID


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.agent_runtime.memory import (  # noqa: E402
    MemoryConsolidationWorker,
    SqlMemoryConsolidationStore,
    StructuredMemoryConsolidator,
)
from app.core.observability import configure_logging  # noqa: E402
from app.core.settings import get_settings  # noqa: E402
from app.infrastructure.db import (  # noqa: E402
    create_db_engine,
    create_session_factory,
)


async def run(
    *,
    source_date: date,
    owner_user_id: UUID | None,
) -> list[dict[str, object]]:
    settings = get_settings()
    settings.validate_for_startup()
    engine = create_db_engine(settings)
    try:
        worker = MemoryConsolidationWorker(
            store=SqlMemoryConsolidationStore(
                session_factory=create_session_factory(engine)
            ),
            consolidator=StructuredMemoryConsolidator(),
            concurrency=settings.agent_worker_concurrency,
        )
        results = await worker.run_date(
            source_date=source_date,
            owner_user_id=owner_user_id,
        )
        return [
            {
                "status": item.status,
                "upserted_count": item.upserted_count,
                "rejected_count": item.rejected_count,
            }
            for item in results
        ]
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consolidate approved conversation facts into memory."
    )
    parser.add_argument(
        "--source-date",
        type=date.fromisoformat,
        default=datetime.now(timezone.utc).date() - timedelta(days=1),
    )
    parser.add_argument("--owner-user-id", type=UUID)
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        environment=settings.app_env,
        version=settings.app_version,
        process="memory-consolidation",
    )
    result = asyncio.run(
        run(
            source_date=args.source_date,
            owner_user_id=args.owner_user_id,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
