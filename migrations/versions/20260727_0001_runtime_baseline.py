"""Create the final Agent Runtime schema on an empty database."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260727_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('agent_threads',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=255), server_default='', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='active', nullable=False),
    sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_threads'))
    )
    op.create_index('ix_agent_threads_owner_status_updated', 'agent_threads', ['owner_user_id', 'status', 'updated_at'], unique=False)
    op.create_index('ix_agent_threads_owner_updated', 'agent_threads', ['owner_user_id', 'updated_at'], unique=False)
    op.create_table('audit_logs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('actor_type', sa.String(length=32), server_default='user', nullable=False),
    sa.Column('actor_service', sa.String(length=120), server_default='', nullable=False),
    sa.Column('action', sa.String(length=120), nullable=False),
    sa.Column('resource_type', sa.String(length=120), nullable=False),
    sa.Column('resource_id', sa.String(length=120), server_default='', nullable=False),
    sa.Column('request_id', sa.String(length=80), server_default='', nullable=False),
    sa.Column('outcome', sa.String(length=32), server_default='succeeded', nullable=False),
    sa.Column('details_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index('ix_audit_logs_actor_created', 'audit_logs', ['actor_user_id', 'created_at'], unique=False)
    op.create_index('ix_audit_logs_actor_service', 'audit_logs', ['actor_service', 'created_at'], unique=False)
    op.create_index('ix_audit_logs_request_id', 'audit_logs', ['request_id'], unique=False)
    op.create_index('ix_audit_logs_resource', 'audit_logs', ['resource_type', 'resource_id'], unique=False)
    op.create_table('idempotency_keys',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=False),
    sa.Column('scope', sa.String(length=120), nullable=False),
    sa.Column('key', sa.String(length=255), nullable=False),
    sa.Column('request_hash', sa.String(length=128), nullable=False),
    sa.Column('response_ref', sa.String(length=512), server_default='', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='in_progress', nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_idempotency_keys')),
    sa.UniqueConstraint('actor_user_id', 'scope', 'key', name='uq_idempotency_actor_scope_key')
    )
    op.create_index('ix_idempotency_keys_expires_at', 'idempotency_keys', ['expires_at'], unique=False)
    op.create_table('agent_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('thread_id', sa.UUID(), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='queued', nullable=False),
    sa.Column('runtime_pattern', sa.String(length=64), server_default='proprietary_runtime', nullable=False),
    sa.Column('runtime_version', sa.String(length=80), server_default='', nullable=False),
    sa.Column('agent_name', sa.String(length=64), server_default='', nullable=False),
    sa.Column('request_id', sa.String(length=80), server_default='', nullable=False),
    sa.Column('trace_id', sa.String(length=120), server_default='', nullable=False),
    sa.Column('error_code', sa.String(length=120), server_default='', nullable=False),
    sa.Column('error_details_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('execution_manifest_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('context_state_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('lease_token', sa.UUID(), nullable=True),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('(lease_token IS NULL) = (locked_until IS NULL)', name=op.f('ck_agent_runs_ck_agent_runs_lease_pair')),
    sa.ForeignKeyConstraint(['thread_id'], ['agent_threads.id'], name=op.f('fk_agent_runs_thread_id_agent_threads')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_runs'))
    )
    op.create_index('ix_agent_runs_actor_status_started', 'agent_runs', ['actor_user_id', 'status', 'started_at'], unique=False)
    op.create_index('ix_agent_runs_request_id', 'agent_runs', ['request_id'], unique=False)
    op.create_index('ix_agent_runs_runnable_lease', 'agent_runs', ['status', 'locked_until', 'created_at', 'id'], unique=False, postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_index('ix_agent_runs_agent_name', 'agent_runs', ['agent_name'], unique=False)
    op.create_index('ix_agent_runs_thread_started', 'agent_runs', ['thread_id', 'started_at'], unique=False)
    op.create_index('ix_agent_runs_trace_id', 'agent_runs', ['trace_id'], unique=False)
    op.create_index('uq_agent_runs_thread_active', 'agent_runs', ['thread_id'], unique=True, postgresql_where=sa.text("status IN ('queued', 'running', 'waiting_for_confirmation')"))
    op.create_table('agent_actions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=False),
    sa.Column('action_type', sa.String(length=120), nullable=False),
    sa.Column('target_type', sa.String(length=120), server_default='', nullable=False),
    sa.Column('target_id', sa.String(length=120), server_default='', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='proposed', nullable=False),
    sa.Column('side_effect_level', sa.String(length=32), server_default='medium', nullable=False),
    sa.Column('preview_payload_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('apply_payload_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('result_payload_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), server_default='', nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_code', sa.String(length=120), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name=op.f('fk_agent_actions_run_id_agent_runs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_actions'))
    )
    op.create_index('ix_agent_actions_actor_status', 'agent_actions', ['actor_user_id', 'status'], unique=False)
    op.create_index('ix_agent_actions_confirmation_expiry', 'agent_actions', ['expires_at', 'id'], unique=False, postgresql_where=sa.text("status = 'confirmation_required'"))
    op.create_index('ix_agent_actions_idempotency_key', 'agent_actions', ['idempotency_key'], unique=False)
    op.create_index('ix_agent_actions_run_status', 'agent_actions', ['run_id', 'status'], unique=False)
    op.create_table('agent_artifacts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('artifact_type', sa.String(length=120), nullable=False),
    sa.Column('schema_version', sa.String(length=80), server_default='v1', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='created', nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('raw_payload_ref', sa.String(length=512), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name=op.f('fk_agent_artifacts_run_id_agent_runs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_artifacts'))
    )
    op.create_index('ix_agent_artifacts_owner_type_created', 'agent_artifacts', ['owner_user_id', 'artifact_type', 'created_at'], unique=False)
    op.create_index('ix_agent_artifacts_run_status', 'agent_artifacts', ['run_id', 'status'], unique=False)
    op.create_table('agent_context_items',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('thread_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=True),
    sa.Column('item_key', sa.String(length=255), nullable=False),
    sa.Column('item_type', sa.String(length=64), nullable=False),
    sa.Column('item_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name=op.f('fk_agent_context_items_run_id_agent_runs')),
    sa.ForeignKeyConstraint(['thread_id'], ['agent_threads.id'], name=op.f('fk_agent_context_items_thread_id_agent_threads')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_context_items')),
    sa.UniqueConstraint('thread_id', 'item_key', name='uq_agent_context_items_thread_item_key'),
    sa.UniqueConstraint('thread_id', 'sequence', name='uq_agent_context_items_thread_sequence')
    )
    op.create_index('ix_agent_context_items_run_sequence', 'agent_context_items', ['run_id', 'sequence'], unique=False)
    op.create_index('ix_agent_context_items_thread_sequence', 'agent_context_items', ['thread_id', 'sequence'], unique=False)
    op.create_table('agent_context_checkpoints',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('thread_id', sa.UUID(), nullable=False),
    sa.Column('source_cutoff_run_id', sa.UUID(), nullable=False),
    sa.Column('source_cutoff_sequence', sa.Integer(), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('schema_version', sa.String(length=80), nullable=False),
    sa.Column('source_sha256', sa.String(length=64), nullable=False),
    sa.Column('summary_sha256', sa.String(length=64), nullable=False),
    sa.Column('model', sa.String(length=120), nullable=False),
    sa.Column('token_counter', sa.String(length=120), nullable=False),
    sa.Column('token_counter_version', sa.String(length=32), nullable=False),
    sa.Column('source_input_tokens', sa.Integer(), nullable=False),
    sa.Column('summary_output_tokens', sa.Integer(), nullable=False),
    sa.Column('prompt_version', sa.String(length=80), nullable=False),
    sa.Column('materializer_version', sa.String(length=80), nullable=False),
    sa.Column('context_schema_version', sa.String(length=80), nullable=False),
    sa.Column('summary_policy_version', sa.String(length=80), nullable=False),
    sa.Column('provider_response_id', sa.String(length=255), server_default='', nullable=False),
    sa.Column('checkpoint_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['source_cutoff_run_id'], ['agent_runs.id'], name=op.f('fk_agent_context_checkpoints_source_cutoff_run_id_agent_runs')),
    sa.ForeignKeyConstraint(['thread_id'], ['agent_threads.id'], name=op.f('fk_agent_context_checkpoints_thread_id_agent_threads')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_context_checkpoints')),
    sa.UniqueConstraint('thread_id', 'source_cutoff_sequence', 'source_sha256', name='uq_agent_context_checkpoints_source')
    )
    op.create_index('ix_agent_context_checkpoints_thread_cutoff', 'agent_context_checkpoints', ['thread_id', 'source_cutoff_sequence'], unique=False)
    op.create_table('agent_context_compaction_jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('thread_id', sa.UUID(), nullable=False),
    sa.Column('trigger_run_id', sa.UUID(), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=False),
    sa.Column('base_checkpoint_id', sa.UUID(), nullable=True),
    sa.Column('source_cutoff_run_id', sa.UUID(), nullable=False),
    sa.Column('source_cutoff_sequence', sa.Integer(), nullable=False),
    sa.Column('source_sha256', sa.String(length=64), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=False),
    sa.Column('model', sa.String(length=120), nullable=False),
    sa.Column('token_counter', sa.String(length=120), nullable=False),
    sa.Column('token_counter_version', sa.String(length=32), nullable=False),
    sa.Column('source_input_tokens', sa.Integer(), nullable=False),
    sa.Column('summary_max_tokens', sa.Integer(), nullable=False),
    sa.Column('prompt_version', sa.String(length=80), nullable=False),
    sa.Column('materializer_version', sa.String(length=80), nullable=False),
    sa.Column('context_schema_version', sa.String(length=80), nullable=False),
    sa.Column('summary_policy_version', sa.String(length=80), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='queued', nullable=False),
    sa.Column('checkpoint_id', sa.UUID(), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('max_attempts', sa.Integer(), server_default='3', nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('lease_token', sa.UUID(), nullable=True),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_code', sa.String(length=120), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('supersedes_job_id', sa.UUID(), nullable=True),
    sa.CheckConstraint('attempts >= 0 AND max_attempts > 0', name=op.f('ck_agent_context_compaction_jobs_ck_agent_context_compaction_jobs_attempts')),
    sa.CheckConstraint('(lease_token IS NULL) = (locked_until IS NULL)', name=op.f('ck_agent_context_compaction_jobs_ck_agent_context_compaction_jobs_lease_pair')),
    sa.ForeignKeyConstraint(['base_checkpoint_id'], ['agent_context_checkpoints.id'], name=op.f('fk_agent_context_compaction_jobs_base_checkpoint_id_agent_context_checkpoints')),
    sa.ForeignKeyConstraint(['checkpoint_id'], ['agent_context_checkpoints.id'], name=op.f('fk_agent_context_compaction_jobs_checkpoint_id_agent_context_checkpoints')),
    sa.ForeignKeyConstraint(['source_cutoff_run_id'], ['agent_runs.id'], name=op.f('fk_agent_context_compaction_jobs_source_cutoff_run_id_agent_runs')),
    sa.ForeignKeyConstraint(['supersedes_job_id'], ['agent_context_compaction_jobs.id'], name=op.f('fk_agent_context_compaction_jobs_supersedes_job_id_agent_context_compaction_jobs')),
    sa.ForeignKeyConstraint(['thread_id'], ['agent_threads.id'], name=op.f('fk_agent_context_compaction_jobs_thread_id_agent_threads')),
    sa.ForeignKeyConstraint(['trigger_run_id'], ['agent_runs.id'], name=op.f('fk_agent_context_compaction_jobs_trigger_run_id_agent_runs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_context_compaction_jobs'))
    )
    op.create_index('ix_agent_context_compaction_jobs_runnable', 'agent_context_compaction_jobs', ['status', 'next_attempt_at', 'locked_until', 'created_at', 'id'], unique=False, postgresql_where=sa.text("status IN ('queued', 'retry_wait', 'running')"))
    op.create_index('ix_agent_context_compaction_jobs_thread_status', 'agent_context_compaction_jobs', ['thread_id', 'status', 'source_cutoff_sequence'], unique=False)
    op.create_index('uq_agent_context_compaction_jobs_active_idempotency', 'agent_context_compaction_jobs', ['thread_id', 'idempotency_key'], unique=True, postgresql_where=sa.text("status IN ('queued', 'retry_wait', 'running', 'completed')"))
    op.create_table('agent_thread_context_heads',
    sa.Column('thread_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='ready', nullable=False),
    sa.Column('generation', sa.Integer(), server_default='0', nullable=False),
    sa.Column('ready_checkpoint_id', sa.UUID(), nullable=True),
    sa.Column('pending_job_id', sa.UUID(), nullable=True),
    sa.Column('error_code', sa.String(length=120), server_default='', nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['pending_job_id'], ['agent_context_compaction_jobs.id'], name=op.f('fk_agent_thread_context_heads_pending_job_id_agent_context_compaction_jobs')),
    sa.ForeignKeyConstraint(['ready_checkpoint_id'], ['agent_context_checkpoints.id'], name=op.f('fk_agent_thread_context_heads_ready_checkpoint_id_agent_context_checkpoints')),
    sa.ForeignKeyConstraint(['thread_id'], ['agent_threads.id'], name=op.f('fk_agent_thread_context_heads_thread_id_agent_threads')),
    sa.PrimaryKeyConstraint('thread_id', name=op.f('pk_agent_thread_context_heads'))
    )
    op.create_table('agent_eval_cases',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('suite', sa.String(length=120), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('domain', sa.String(length=120), server_default='', nullable=False),
    sa.Column('input_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('expected_behavior_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('expected_tool_calls_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('source_run_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=32), server_default='draft', nullable=False),
    sa.Column('owner_team', sa.String(length=120), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['source_run_id'], ['agent_runs.id'], name=op.f('fk_agent_eval_cases_source_run_id_agent_runs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_eval_cases'))
    )
    op.create_index('ix_agent_eval_cases_domain_status', 'agent_eval_cases', ['domain', 'status'], unique=False)
    op.create_index('ix_agent_eval_cases_suite_status', 'agent_eval_cases', ['suite', 'status'], unique=False)
    op.create_table('agent_events',
    sa.Column('event_id', sa.UUID(), nullable=False),
    sa.Column('thread_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=120), nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name=op.f('fk_agent_events_run_id_agent_runs')),
    sa.ForeignKeyConstraint(['thread_id'], ['agent_threads.id'], name=op.f('fk_agent_events_thread_id_agent_threads')),
    sa.PrimaryKeyConstraint('event_id', name=op.f('pk_agent_events')),
    sa.UniqueConstraint('run_id', 'sequence', name='uq_agent_events_run_sequence')
    )
    op.create_index('ix_agent_events_run_sequence', 'agent_events', ['run_id', 'sequence'], unique=False)
    op.create_index('ix_agent_events_thread_created', 'agent_events', ['thread_id', 'created_at'], unique=False)
    op.create_index('ix_agent_events_type_created', 'agent_events', ['event_type', 'created_at'], unique=False)
    op.create_table('agent_messages',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('thread_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=True),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('message_type', sa.String(length=32), server_default='text', nullable=False),
    sa.Column('content_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='completed', nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name=op.f('fk_agent_messages_run_id_agent_runs')),
    sa.ForeignKeyConstraint(['thread_id'], ['agent_threads.id'], name=op.f('fk_agent_messages_thread_id_agent_threads')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_messages')),
    sa.UniqueConstraint('thread_id', 'sequence', name='uq_agent_messages_thread_sequence')
    )
    op.create_index('ix_agent_messages_run_created', 'agent_messages', ['run_id', 'created_at'], unique=False)
    op.create_index('ix_agent_messages_thread_sequence', 'agent_messages', ['thread_id', 'sequence'], unique=False)
    op.create_table('agent_tool_calls',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('tool_name', sa.String(length=120), nullable=False),
    sa.Column('call_id', sa.String(length=120), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='started', nullable=False),
    sa.Column('safe_args_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_code', sa.String(length=120), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name=op.f('fk_agent_tool_calls_run_id_agent_runs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_tool_calls')),
    sa.UniqueConstraint('run_id', 'call_id', name='uq_agent_tool_calls_run_call_id')
    )
    op.create_index('ix_agent_tool_calls_run_status', 'agent_tool_calls', ['run_id', 'status'], unique=False)
    op.create_index('ix_agent_tool_calls_run_tool', 'agent_tool_calls', ['run_id', 'tool_name'], unique=False)
    op.create_table('agent_workflow_states',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('thread_id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=True),
    sa.Column('workflow_type', sa.String(length=120), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='collecting', nullable=False),
    sa.Column('schema_version', sa.String(length=80), server_default='v1', nullable=False),
    sa.Column('state_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('active_step', sa.String(length=120), server_default='', nullable=False),
    sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
    sa.Column('step_token', sa.String(length=128), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name=op.f('fk_agent_workflow_states_run_id_agent_runs')),
    sa.ForeignKeyConstraint(['thread_id'], ['agent_threads.id'], name=op.f('fk_agent_workflow_states_thread_id_agent_threads')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_workflow_states'))
    )
    op.create_index('ix_agent_workflow_states_expires_at', 'agent_workflow_states', ['expires_at'], unique=False)
    op.create_index('ix_agent_workflow_states_owner_type_status', 'agent_workflow_states', ['owner_user_id', 'workflow_type', 'status'], unique=False)
    op.create_index('ix_agent_workflow_states_run_created', 'agent_workflow_states', ['run_id', 'created_at'], unique=False)
    op.create_index('ix_agent_workflow_states_thread_status', 'agent_workflow_states', ['thread_id', 'status'], unique=False)
    op.create_index('uq_agent_workflow_states_owner_type_active', 'agent_workflow_states', ['owner_user_id', 'workflow_type'], unique=True, postgresql_where=sa.text("workflow_type = 'pregnancy_plan' AND status IN ('collecting', 'ready', 'waiting', 'paused')"))
    op.create_table('agent_tool_outputs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('tool_call_id', sa.UUID(), nullable=False),
    sa.Column('output_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('output_ref', sa.String(length=512), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tool_call_id'], ['agent_tool_calls.id'], name=op.f('fk_agent_tool_outputs_tool_call_id_agent_tool_calls')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_tool_outputs'))
    )
    op.create_index('ix_agent_tool_outputs_tool_call', 'agent_tool_outputs', ['tool_call_id'], unique=False)
    op.create_table('agent_workflow_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workflow_state_id', sa.UUID(), nullable=False),
    sa.Column('owner_user_id', sa.UUID(), nullable=False),
    sa.Column('thread_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=True),
    sa.Column('workflow_type', sa.String(length=120), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=120), nullable=False),
    sa.Column('from_revision', sa.Integer(), server_default='0', nullable=False),
    sa.Column('to_revision', sa.Integer(), nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], name=op.f('fk_agent_workflow_events_run_id_agent_runs')),
    sa.ForeignKeyConstraint(['thread_id'], ['agent_threads.id'], name=op.f('fk_agent_workflow_events_thread_id_agent_threads')),
    sa.ForeignKeyConstraint(['workflow_state_id'], ['agent_workflow_states.id'], name=op.f('fk_agent_workflow_events_workflow_state_id_agent_workflow_states')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_workflow_events')),
    sa.UniqueConstraint('workflow_state_id', 'sequence', name='uq_agent_workflow_events_state_sequence')
    )
    op.create_index('ix_agent_workflow_events_owner_type_created', 'agent_workflow_events', ['owner_user_id', 'workflow_type', 'created_at'], unique=False)
    op.create_index('ix_agent_workflow_events_run_created', 'agent_workflow_events', ['run_id', 'created_at'], unique=False)
    op.create_index('ix_agent_workflow_events_state_sequence', 'agent_workflow_events', ['workflow_state_id', 'sequence'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_agent_workflow_events_state_sequence', table_name='agent_workflow_events')
    op.drop_index('ix_agent_workflow_events_run_created', table_name='agent_workflow_events')
    op.drop_index('ix_agent_workflow_events_owner_type_created', table_name='agent_workflow_events')
    op.drop_table('agent_workflow_events')
    op.drop_index('ix_agent_tool_outputs_tool_call', table_name='agent_tool_outputs')
    op.drop_table('agent_tool_outputs')
    op.drop_index('uq_agent_workflow_states_owner_type_active', table_name='agent_workflow_states', postgresql_where=sa.text("workflow_type = 'pregnancy_plan' AND status IN ('collecting', 'ready', 'waiting', 'paused')"))
    op.drop_index('ix_agent_workflow_states_thread_status', table_name='agent_workflow_states')
    op.drop_index('ix_agent_workflow_states_run_created', table_name='agent_workflow_states')
    op.drop_index('ix_agent_workflow_states_owner_type_status', table_name='agent_workflow_states')
    op.drop_index('ix_agent_workflow_states_expires_at', table_name='agent_workflow_states')
    op.drop_table('agent_workflow_states')
    op.drop_index('ix_agent_tool_calls_run_tool', table_name='agent_tool_calls')
    op.drop_index('ix_agent_tool_calls_run_status', table_name='agent_tool_calls')
    op.drop_table('agent_tool_calls')
    op.drop_index('ix_agent_messages_thread_sequence', table_name='agent_messages')
    op.drop_index('ix_agent_messages_run_created', table_name='agent_messages')
    op.drop_table('agent_messages')
    op.drop_index('ix_agent_events_type_created', table_name='agent_events')
    op.drop_index('ix_agent_events_thread_created', table_name='agent_events')
    op.drop_index('ix_agent_events_run_sequence', table_name='agent_events')
    op.drop_table('agent_events')
    op.drop_index('ix_agent_eval_cases_suite_status', table_name='agent_eval_cases')
    op.drop_index('ix_agent_eval_cases_domain_status', table_name='agent_eval_cases')
    op.drop_table('agent_eval_cases')
    op.drop_table('agent_thread_context_heads')
    op.drop_index('uq_agent_context_compaction_jobs_active_idempotency', table_name='agent_context_compaction_jobs', postgresql_where=sa.text("status IN ('queued', 'retry_wait', 'running', 'completed')"))
    op.drop_index('ix_agent_context_compaction_jobs_thread_status', table_name='agent_context_compaction_jobs')
    op.drop_index('ix_agent_context_compaction_jobs_runnable', table_name='agent_context_compaction_jobs', postgresql_where=sa.text("status IN ('queued', 'retry_wait', 'running')"))
    op.drop_table('agent_context_compaction_jobs')
    op.drop_index('ix_agent_context_checkpoints_thread_cutoff', table_name='agent_context_checkpoints')
    op.drop_table('agent_context_checkpoints')
    op.drop_index('ix_agent_context_items_thread_sequence', table_name='agent_context_items')
    op.drop_index('ix_agent_context_items_run_sequence', table_name='agent_context_items')
    op.drop_table('agent_context_items')
    op.drop_index('ix_agent_artifacts_run_status', table_name='agent_artifacts')
    op.drop_index('ix_agent_artifacts_owner_type_created', table_name='agent_artifacts')
    op.drop_table('agent_artifacts')
    op.drop_index('ix_agent_actions_run_status', table_name='agent_actions')
    op.drop_index('ix_agent_actions_idempotency_key', table_name='agent_actions')
    op.drop_index('ix_agent_actions_confirmation_expiry', table_name='agent_actions', postgresql_where=sa.text("status = 'confirmation_required'"))
    op.drop_index('ix_agent_actions_actor_status', table_name='agent_actions')
    op.drop_table('agent_actions')
    op.drop_index('uq_agent_runs_thread_active', table_name='agent_runs', postgresql_where=sa.text("status IN ('queued', 'running', 'waiting_for_confirmation')"))
    op.drop_index('ix_agent_runs_trace_id', table_name='agent_runs')
    op.drop_index('ix_agent_runs_thread_started', table_name='agent_runs')
    op.drop_index('ix_agent_runs_agent_name', table_name='agent_runs')
    op.drop_index('ix_agent_runs_runnable_lease', table_name='agent_runs', postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.drop_index('ix_agent_runs_request_id', table_name='agent_runs')
    op.drop_index('ix_agent_runs_actor_status_started', table_name='agent_runs')
    op.drop_table('agent_runs')
    op.drop_index('ix_idempotency_keys_expires_at', table_name='idempotency_keys')
    op.drop_table('idempotency_keys')
    op.drop_index('ix_audit_logs_resource', table_name='audit_logs')
    op.drop_index('ix_audit_logs_request_id', table_name='audit_logs')
    op.drop_index('ix_audit_logs_actor_service', table_name='audit_logs')
    op.drop_index('ix_audit_logs_actor_created', table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index('ix_agent_threads_owner_updated', table_name='agent_threads')
    op.drop_index('ix_agent_threads_owner_status_updated', table_name='agent_threads')
    op.drop_table('agent_threads')
