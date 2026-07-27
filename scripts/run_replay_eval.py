from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any
from uuid import UUID


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.agent_runtime.evals import (  # noqa: E402
    RuntimeEvalRepository,
    RuntimeEvalService,
)
from app.agent_runtime.audit import (  # noqa: E402
    AuditService,
    RuntimeAuditRepository,
)
from app.agent_runtime.replay import (  # noqa: E402
    RuntimeReplayRepository,
    RuntimeReplayService,
)
from app.core.observability import configure_logging  # noqa: E402
from app.core.settings import get_settings  # noqa: E402
from app.infrastructure.db import (  # noqa: E402
    create_db_engine,
    create_session_factory,
)


async def run(
    *,
    run_id: UUID,
    include_message_content: bool,
    eval_case_id: UUID | None,
) -> dict[str, Any]:
    settings = get_settings()
    settings.validate_for_startup()
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            audit_service = AuditService(
                repository=RuntimeAuditRepository(session)
            )
            replay_service = RuntimeReplayService(
                repository=RuntimeReplayRepository(session),
                audit_service=audit_service,
            )
            bundle = await replay_service.export_run_bundle(
                run_id=run_id,
                include_message_content=include_message_content,
            )
            payload: dict[str, Any] = {"replay_bundle": bundle}
            if eval_case_id is not None:
                result = await RuntimeEvalService(
                    repository=RuntimeEvalRepository(session),
                    replay_service=replay_service,
                    audit_service=audit_service,
                ).evaluate_case(
                    case_id=eval_case_id,
                    run_id=run_id,
                )
                payload["eval_result"] = asdict(result)
            await session.commit()
            return payload
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a persisted Agent run and optionally evaluate it."
    )
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--eval-case-id", type=UUID)
    parser.add_argument("--include-message-content", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        environment=settings.app_env,
        version=settings.app_version,
        process="replay-eval",
    )
    payload = asyncio.run(
        run(
            run_id=args.run_id,
            include_message_content=args.include_message_content,
            eval_case_id=args.eval_case_id,
        )
    )
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output is None:
        print(encoded)
    else:
        args.output.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
