from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from app.infrastructure.db.schema import RUNTIME_SCHEMA_REVISION


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_BASELINE_REVISION = "20260727_0001"


def test_runtime_has_one_fresh_alembic_head() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "heads"],
        cwd=REPOSITORY_ROOT,
        env=_migration_env(),
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == (
        f"{RUNTIME_BASELINE_REVISION} (head)"
    )
    assert RUNTIME_SCHEMA_REVISION == RUNTIME_BASELINE_REVISION


def test_runtime_baseline_generates_empty_database_sql_without_product_tables() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=REPOSITORY_ROOT,
        env=_migration_env(),
        check=True,
        capture_output=True,
        text=True,
    )

    sql = result.stdout
    assert "CREATE TABLE agent_threads" in sql
    assert "CREATE TABLE agent_runs" in sql
    assert "lease_token UUID" in sql
    assert "locked_until TIMESTAMP WITH TIME ZONE" in sql
    assert "CREATE INDEX ix_agent_runs_runnable_lease" in sql
    assert "CREATE INDEX ix_agent_runs_skill_id" in sql
    assert "CREATE INDEX ix_agent_actions_confirmation_expiry" in sql
    assert "CREATE TABLE user_fact_extraction_runs" in sql
    assert "consent_version INTEGER" in sql
    assert "memory_type VARCHAR(80)" in sql
    assert "CREATE TABLE audit_logs" in sql
    assert "CREATE TABLE idempotency_keys" in sql
    assert "output_json JSONB" in sql
    assert "output_ref VARCHAR(512)" in sql
    assert "result_payload_json JSONB" in sql
    assert "ALTER TABLE" not in sql
    assert "RENAME" not in sql
    assert "safe_output_json" not in sql
    assert "raw_output_ref" not in sql
    assert "agent_run_summaries" not in sql
    assert "REFERENCES users" not in sql
    assert "REFERENCES files" not in sql


def _migration_env() -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": ("postgresql+asyncpg://momcozy_agent_runtime:momcozy_agent_runtime@localhost:5432/momcozy_agent_runtime"),
    }
