from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.agent_runtime.facts import (  # noqa: E402
    FactExtractionWorker,
    ModelFactExtractor,
    SqlFactExtractionStore,
)
from app.agent_runtime.providers import OpenAIResponsesProvider  # noqa: E402
from app.core.observability import configure_logging  # noqa: E402
from app.core.settings import get_settings  # noqa: E402
from app.infrastructure.db import (  # noqa: E402
    create_db_engine,
    create_session_factory,
)
from app.infrastructure.redis import (  # noqa: E402
    RedisWorkerHeartbeat,
    close_redis_client,
    create_redis_client,
)


async def run(*, once: bool) -> None:
    settings = get_settings()
    settings.validate_for_worker()
    engine = create_db_engine(settings)
    redis_client = create_redis_client(
        settings.redis_url,
        timeout_seconds=settings.redis_timeout_seconds,
    )
    heartbeat = RedisWorkerHeartbeat(
        client=redis_client,
        role="fact-worker",
        version=settings.app_version,
        interval_seconds=settings.worker_heartbeat_interval_seconds,
        ttl_seconds=settings.worker_heartbeat_ttl_seconds,
    )
    worker = FactExtractionWorker(
        store=SqlFactExtractionStore(
            session_factory=create_session_factory(engine)
        ),
        extractor=ModelFactExtractor(
            provider=OpenAIResponsesProvider(
                model=settings.openai_model,
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                reasoning_effort="low",
                text_verbosity="low",
                store=False,
                timeout_seconds=settings.agent_model_timeout_seconds,
            )
        ),
        batch_size=settings.fact_worker_batch_size,
        concurrency=settings.fact_worker_concurrency,
        lease_seconds=settings.fact_worker_lease_seconds,
    )
    try:
        async with heartbeat.maintain():
            while True:
                results = await worker.run_once()
                if results:
                    logging.info(
                        "Processed fact jobs.",
                        extra={
                            "event": "fact_worker.batch.completed",
                            "worker": "fact-worker",
                            "count": len(results),
                        },
                    )
                if once:
                    return
                await asyncio.sleep(
                    settings.fact_worker_poll_interval_seconds
                )
    finally:
        await close_redis_client(redis_client)
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the durable conversation fact extraction worker."
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        environment=settings.app_env,
        version=settings.app_version,
        process="fact-worker",
    )
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
