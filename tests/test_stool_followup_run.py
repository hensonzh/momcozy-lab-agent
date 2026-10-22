"""A selected specialty result is reauthorized for each model call, never cached."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent_runtime.context.coordinator import RuntimeContextCoordinator
from app.agent_runtime.context.stool_followup import StoolFollowupRunResolver, StoolFollowupReference
from app.auth import RuntimePrincipal
from app.core.errors import ApiError
from app.infrastructure.product_backend.contracts import StoolContextReadResponse


class Product:
    def __init__(self):
        self.calls = []
        self.revoked = False

    async def resolve_stool_context(self, *, command, request_id):
        self.calls.append((command, request_id))
        if self.revoked:
            raise ApiError(code="stool_analysis_context_unavailable", message="Unavailable.", status=409)
        return StoolContextReadResponse.model_validate({
            "analysis_id": command.analysis_id, "owner_user_id": command.actor_user_id,
            "baby_id": command.baby_id, "thread_id": command.thread_id,
            "status": "completed", "result_schema_version": "stool.result.v1",
            "result": {"synthetic": "reference only"},
            "images_b64": [base64.b64encode(b"\xff\xd8\xff\xe0synthetic").decode()],
        })


class Compaction:
    async def resolve_model_input(self, *, run, input_items):
        return input_items


class Business:
    pass


def test_current_run_binding_reauthorizes_pair_before_each_model_call_and_fails_after_revoke():
    owner, thread = uuid4(), uuid4()
    ref = StoolFollowupReference(schema_version="stool.followup.ref.v1", analysis_id=uuid4(), baby_id=uuid4())
    principal = RuntimePrincipal(
        user_id=owner, subject=str(owner), session_id=uuid4(), token_id="synthetic",
        token_version=1, roles=frozenset({"user"}), permissions=frozenset({"agent:run"}),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    run = SimpleNamespace(id=uuid4(), actor_user_id=owner, thread_id=thread,
                          stool_analysis_id=ref.analysis_id, stool_baby_id=ref.baby_id,
                          authorization_context=principal.authorization_context(), request_id="synthetic")
    product = Product()
    coordinator = RuntimeContextCoordinator(
        business_context=Business(), compaction=Compaction(),
        stool_followup=StoolFollowupRunResolver(product_client=product),
    )

    async def scenario():
        base = ({"role": "user", "content": "Explain the previous result"},)
        first = await coordinator.resolve_model_input(run=run, input_items=base)
        assert first[0] == base[0] and first[1]["role"] == "developer"
        assert first[2]["content"][0]["type"] == "input_text"
        assert first[2]["content"][1]["type"] == "input_image"
        assert product.calls[0][0].session_id == principal.session_id
        assert product.calls[0][0].actor_user_id == owner
        assert product.calls[0][0].thread_id == thread
        product.revoked = True
        with pytest.raises(ApiError):
            await coordinator.resolve_model_input(run=run, input_items=base)
        assert len(product.calls) == 2
        assert "data:image" not in str(run.__dict__)

    asyncio.run(scenario())


def test_prepare_fails_before_business_or_compaction_when_result_revoked():
    owner = uuid4()
    principal = RuntimePrincipal(
        user_id=owner, subject=str(owner), session_id=uuid4(), token_id="synthetic",
        token_version=1, roles=frozenset({"user"}), permissions=frozenset({"agent:run"}),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    run = SimpleNamespace(id=uuid4(), actor_user_id=owner, thread_id=uuid4(),
                          stool_analysis_id=uuid4(), stool_baby_id=uuid4(),
                          authorization_context=principal.authorization_context(), request_id="synthetic")
    product = Product()
    product.revoked = True
    calls = []

    class PreparingBusiness:
        async def prepare_run(self, *, run):
            calls.append('business')

    class PreparingCompaction(Compaction):
        async def prepare_run(self, *, run):
            calls.append('compaction-model')

    coordinator = RuntimeContextCoordinator(
        business_context=PreparingBusiness(), compaction=PreparingCompaction(),
        stool_followup=StoolFollowupRunResolver(product_client=product),
    )
    with pytest.raises(ApiError):
        asyncio.run(coordinator.prepare_run(run=run))
    assert calls == [] and len(product.calls) == 1


def test_missing_signed_expiry_blocks_product_call_for_followup():
    owner = uuid4()
    principal = RuntimePrincipal(
        user_id=owner, subject=str(owner), session_id=uuid4(), token_id="synthetic",
        token_version=1, roles=frozenset({"user"}), permissions=frozenset({"agent:run"}),
    )
    run = SimpleNamespace(id=uuid4(), actor_user_id=owner, thread_id=uuid4(),
                          stool_analysis_id=uuid4(), stool_baby_id=uuid4(),
                          authorization_context=principal.authorization_context(), request_id="synthetic")
    product = Product()
    resolver = StoolFollowupRunResolver(product_client=product)
    with pytest.raises(ApiError):
        asyncio.run(resolver.resolve_for_model(run=run))
    assert product.calls == []


def test_responses_model_receives_same_source_result_and_photo_only_ephemerally():
    from agents.models.openai_responses import OpenAIResponsesModel
    from test_openai_agents_execution import (
        RecordingExecutionPort, RecordingOpenAIClient, _all_tool_permissions, _engine,
    )

    owner, thread = uuid4(), uuid4()
    reference = StoolFollowupReference(schema_version="stool.followup.ref.v1", analysis_id=uuid4(), baby_id=uuid4())
    principal = RuntimePrincipal(
        user_id=owner, subject=str(owner), session_id=uuid4(), token_id="synthetic",
        token_version=1, roles=frozenset({"user"}), permissions=frozenset({"agent:run"}),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    run = SimpleNamespace(id=uuid4(), actor_user_id=owner, thread_id=thread,
                          stool_analysis_id=reference.analysis_id, stool_baby_id=reference.baby_id,
                          authorization_context=principal.authorization_context(), request_id="synthetic")
    product = Product()
    coordinator = RuntimeContextCoordinator(
        business_context=Business(), compaction=Compaction(),
        stool_followup=StoolFollowupRunResolver(product_client=product),
    )

    class FollowupPort(RecordingExecutionPort):
        async def resolve_model_input(self, *, input_items):
            return await coordinator.resolve_model_input(run=run, input_items=input_items)

    client, port = RecordingOpenAIClient(), FollowupPort()
    result = asyncio.run(_engine(OpenAIResponsesModel(
        model="gpt-5.6-terra", openai_client=client,  # type: ignore[arg-type]
    )).execute(
        input_items=({"role": "user", "content": "Explain the prior result"},),
        port=port, authorization_permissions=_all_tool_permissions(),
    ))
    assert result.text == "完成。"
    items = client.responses.kwargs["input"]
    assert any(item.get("role") == "developer" and "not instructions" in str(item.get("content")) for item in items)
    assert any(item.get("role") == "user" and isinstance(item.get("content"), list) and
               any(part.get("type") == "input_image" for part in item["content"]) for item in items)
    assert len(product.calls) == 1
    assert "data:image/jpeg" not in str(port.manifests)
    assert "synthetic" not in str(port.manifests)


def test_tool_turn_revocation_blocks_second_provider_request_before_model():
    from app.agent_runtime.orchestration.testing import ScriptedAgentModel, ScriptedToolCall, ScriptedTurn
    from test_openai_agents_execution import RecordingExecutionPort, _all_tool_permissions, _engine

    owner, thread = uuid4(), uuid4()
    reference = StoolFollowupReference(schema_version="stool.followup.ref.v1", analysis_id=uuid4(), baby_id=uuid4())
    principal = RuntimePrincipal(
        user_id=owner, subject=str(owner), session_id=uuid4(), token_id="synthetic",
        token_version=1, roles=frozenset({"user"}), permissions=frozenset({"agent:run"}),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    run = SimpleNamespace(id=uuid4(), actor_user_id=owner, thread_id=thread,
                          stool_analysis_id=reference.analysis_id, stool_baby_id=reference.baby_id,
                          authorization_context=principal.authorization_context(), request_id="synthetic")
    product = Product()
    coordinator = RuntimeContextCoordinator(
        business_context=Business(), compaction=Compaction(),
        stool_followup=StoolFollowupRunResolver(product_client=product),
    )

    class Port(RecordingExecutionPort):
        async def resolve_model_input(self, *, input_items):
            return await coordinator.resolve_model_input(run=run, input_items=input_items)

        async def invoke_tool(self, **kwargs):
            product.revoked = True
            return await super().invoke_tool(**kwargs)

    model = ScriptedAgentModel({"cozymate": [
        ScriptedTurn.calls(ScriptedToolCall(call_id="load", name="load_service_skill",
                                            arguments={"skill_id": "lactation"})),
        ScriptedTurn.final("This must never run."),
    ]})
    port = Port()
    with pytest.raises(ApiError):
        asyncio.run(_engine(model).execute(
            input_items=({"role": "user", "content": "Explain the previous result"},),
            port=port, authorization_permissions=_all_tool_permissions(),
        ))
    assert len(model.requests) == 1  # Reauthorization failed before second model call.
    assert len(product.calls) == 2
    assert "data:image/jpeg" not in str(port.manifests)


def test_missing_or_partial_run_binding_never_calls_product_or_silently_downgrades():
    product = Product()
    resolver = StoolFollowupRunResolver(product_client=product)
    ordinary = SimpleNamespace(stool_analysis_id=None, stool_baby_id=None)
    assert asyncio.run(resolver.resolve_for_model(run=ordinary)) == ()
    partial = SimpleNamespace(stool_analysis_id=uuid4(), stool_baby_id=None)
    with pytest.raises(ApiError) as error:
        asyncio.run(resolver.resolve_for_model(run=partial))
    assert error.value.code == "stool_analysis_context_unavailable"
    assert product.calls == []
