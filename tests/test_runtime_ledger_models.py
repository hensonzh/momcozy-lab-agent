from app.agent_runtime.ledger import models as _ledger_models  # noqa: F401
from app.infrastructure.db import Base


RUNTIME_TABLES = {
    "agent_actions",
    "agent_artifacts",
    "agent_context_items",
    "agent_eval_cases",
    "agent_events",
    "agent_image_accesses",
    "agent_memories",
    "agent_memory_consolidation_runs",
    "agent_memory_settings",
    "agent_memory_snapshots",
    "agent_messages",
    "agent_runs",
    "agent_threads",
    "agent_tool_calls",
    "agent_tool_outputs",
    "agent_workflow_events",
    "agent_workflow_states",
    "audit_logs",
    "idempotency_keys",
    "user_fact_extraction_runs",
    "user_facts",
}


def test_runtime_metadata_owns_only_the_complete_runtime_table_set() -> None:
    assert set(Base.metadata.tables) == RUNTIME_TABLES


def test_runtime_models_have_no_cross_database_foreign_keys() -> None:
    foreign_key_targets = {foreign_key.target_fullname for table in Base.metadata.tables.values() for foreign_key in table.foreign_keys}

    assert not any(target.startswith("users.") for target in foreign_key_targets)
    assert not any(target.startswith("files.") for target in foreign_key_targets)
    assert {target.split(".", maxsplit=1)[0] for target in foreign_key_targets} <= RUNTIME_TABLES


def test_external_identity_and_asset_references_remain_plain_uuid_columns() -> None:
    external_references = {
        ("agent_actions", "actor_user_id"),
        ("agent_artifacts", "owner_user_id"),
        ("agent_image_accesses", "asset_id"),
        ("agent_memories", "owner_user_id"),
        ("agent_memory_consolidation_runs", "owner_user_id"),
        ("agent_memory_settings", "owner_user_id"),
        ("agent_memory_snapshots", "owner_user_id"),
        ("agent_runs", "actor_user_id"),
        ("agent_threads", "owner_user_id"),
        ("agent_workflow_events", "owner_user_id"),
        ("agent_workflow_states", "owner_user_id"),
        ("audit_logs", "actor_user_id"),
        ("idempotency_keys", "actor_user_id"),
        ("user_fact_extraction_runs", "owner_user_id"),
        ("user_facts", "owner_user_id"),
    }

    for table_name, column_name in external_references:
        assert not Base.metadata.tables[table_name].c[column_name].foreign_keys


def test_runtime_ledger_keeps_critical_recovery_and_owner_indexes() -> None:
    index_names = {index.name for table in Base.metadata.tables.values() for index in table.indexes}

    assert {
        "ix_agent_events_run_sequence",
        "ix_agent_actions_confirmation_expiry",
        "ix_agent_messages_thread_sequence",
        "ix_agent_runs_actor_status_started",
        "ix_agent_runs_runnable_lease",
        "ix_agent_threads_owner_updated",
        "uq_agent_runs_thread_active",
    } <= index_names


def test_agent_run_schema_has_database_lease_fencing_columns() -> None:
    run_table = Base.metadata.tables["agent_runs"]

    assert run_table.c.lease_token.nullable is True
    assert run_table.c.locked_until.nullable is True
    assert any(constraint.name == "ck_agent_runs_ck_agent_runs_lease_pair" for constraint in run_table.constraints)
