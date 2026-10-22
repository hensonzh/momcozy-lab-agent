"""Every future follow-up must re-resolve a Product-owned, synthetic bundle."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.core.errors import DependencyError
from app.infrastructure.product_backend import ProductBackendClient, StoolContextReadRequest
from app.infrastructure.product_backend.contracts import StoolContextReadResponse

JPEG = b'\xff\xd8\xff\xe0synthetic'


def command() -> StoolContextReadRequest:
    return StoolContextReadRequest(
        analysis_id=uuid4(), actor_user_id=uuid4(), session_id=uuid4(),
        baby_id=uuid4(), thread_id=uuid4(),
        token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )


def response_body(query: StoolContextReadRequest) -> dict[str, Any]:
    return {
        'analysis_id': str(query.analysis_id), 'owner_user_id': str(query.actor_user_id),
        'baby_id': str(query.baby_id), 'thread_id': str(query.thread_id),
        'status': 'completed', 'result_schema_version': 'stool.result.v1',
        'result': {'color': 'synthetic'}, 'images_b64': [base64.b64encode(JPEG).decode()],
    }


def test_product_client_makes_fresh_internal_request_with_session_scope() -> None:
    query = command()
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == '/v1/internal/agent/stool-analyses/context/resolve'
        assert request.method == 'POST'
        assert request.headers['X-Service-Key'] == 'runtime-service-key'
        assert request.headers['X-Request-ID'] == 'synthetic-request'
        assert f'"session_id":"{query.session_id}"' in request.content.decode()
        return httpx.Response(200, json=response_body(query))

    async def run() -> tuple[
        StoolContextReadResponse, StoolContextReadResponse,
    ]:
        async with httpx.AsyncClient(base_url='https://product.test', transport=httpx.MockTransport(handler)) as client:
            adapter = ProductBackendClient(http_client=client, service_key='runtime-service-key')
            first = await adapter.resolve_stool_context(command=query, request_id='synthetic-request')
            second = await adapter.resolve_stool_context(command=query, request_id='synthetic-request')
            return first, second

    first, second = asyncio.run(run())
    assert len(calls) == 2  # No cached authorization across model calls.
    assert first.analysis_id == second.analysis_id == query.analysis_id
    assert base64.b64decode(first.images_b64[0]) == JPEG


@pytest.mark.parametrize('bad', ['owner', 'image', 'extra'])
def test_product_client_rejects_mismatched_or_malformed_bundle(bad: str) -> None:
    query = command()

    async def handler(_request: httpx.Request) -> httpx.Response:
        result = response_body(query)
        if bad == 'owner':
            result['owner_user_id'] = str(uuid4())
        if bad == 'image':
            result['images_b64'] = ['not base64']
        if bad == 'extra':
            result['object_key'] = 'secret'
        return httpx.Response(200, json=result)

    async def run() -> None:
        async with httpx.AsyncClient(base_url='https://product.test', transport=httpx.MockTransport(handler)) as client:
            await ProductBackendClient(http_client=client, service_key='runtime-service-key').resolve_stool_context(
                command=query, request_id='synthetic-request')

    with pytest.raises(DependencyError) as error:
        asyncio.run(run())
    assert error.value.code == 'product_backend_invalid_response'


def test_agent_followup_materialization_reauthorizes_on_every_product_request() -> None:
    from app.agent_runtime.context.stool_followup import AgentStoolFollowupContext, ProductStoolFollowupResolver

    query = command()
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=response_body(query))
        return httpx.Response(409, json={'error': {
            'code': 'stool_analysis_context_unavailable', 'message': 'Analysis context is unavailable.',
        }})

    async def run() -> tuple[dict[str, Any], ...]:
        async with httpx.AsyncClient(base_url='https://product.test', transport=httpx.MockTransport(handler)) as client:
            product = ProductBackendClient(http_client=client, service_key='runtime-service-key')
            resolver = ProductStoolFollowupResolver(
                client=product, session_id=query.session_id, token_expires_at=query.token_expires_at,
            )
            assembler = AgentStoolFollowupContext(product_client=resolver)
            async def resolve_once() -> tuple[dict[str, Any], ...]:
                return await assembler.resolve_for_model(
                    analysis_id=query.analysis_id, actor_user_id=query.actor_user_id,
                    baby_id=query.baby_id, thread_id=query.thread_id, request_id='synthetic-request',
                )

            first = await resolve_once()
            with pytest.raises(DependencyError):
                await resolve_once()
            return first

    first = asyncio.run(run())
    assert calls == 2
    assert first[1]['content'][0]['type'] == 'input_text'
    assert first[1]['content'][1]['type'] == 'input_image'
