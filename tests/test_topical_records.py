from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest

from app.agent_runtime.context.client import context_timezone, normalize_client_context
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.orchestration.user_status import model_tool_schema
from app.agent_runtime.tools.executor import ToolExecutor
from app.agent_runtime.tools.validation import validate_tool_input
from app.core.errors import ApiError, DependencyError
from app.infrastructure.product_backend import ProductBackendClient, TopicalRecordsReadRequest, TopicalRecordsReadResponse
from app.topical_records import Handler, TOOL_NAME, registry
from test_tool_observability import ToolRepository


def _query(owner: UUID, baby: UUID) -> TopicalRecordsReadRequest:
    return TopicalRecordsReadRequest(
        actor_user_id=owner,
        topic="feeding",
        infant_id=baby,
        start_date=date(2026, 9, 20),
        end_date=date(2026, 9, 24),
        timezone="Asia/Shanghai",
        limit=5,
    )


def _response(query: TopicalRecordsReadRequest) -> dict[str, Any]:
    return TopicalRecordsReadResponse(
        topic=query.topic,
        infant_id=query.infant_id,
        start_date=query.start_date,
        end_date=query.end_date,
        timezone=query.timezone,
        items=[],
        has_more=False,
    ).model_dump(mode="json")


def test_client_sends_bounded_owner_scoped_query_and_rejects_unrelated_response() -> None:
    owner, baby = uuid4(), uuid4()
    query = _query(owner, baby)
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_response(query))

    async def run() -> TopicalRecordsReadResponse:
        async with httpx.AsyncClient(base_url="https://product.test", transport=httpx.MockTransport(handler)) as http_client:
            return await ProductBackendClient(http_client=http_client, service_key="service-key").read_topical_records(
                query=query,
                request_id="request-topical",
            )

    assert asyncio.run(run()).items == []
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/internal/agent/records"
    assert dict(request.url.params) == {
        "actor_user_id": str(owner),
        "topic": "feeding",
        "infant_id": str(baby),
        "start_date": "2026-09-20",
        "end_date": "2026-09-24",
        "timezone": "Asia/Shanghai",
        "limit": "5",
    }
    assert request.headers["x-service-key"] == "service-key"
    assert request.headers["x-request-id"] == "request-topical"

    async def wrong_response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**_response(query), "infant_id": str(uuid4())})

    async def reject() -> None:
        async with httpx.AsyncClient(base_url="https://product.test", transport=httpx.MockTransport(wrong_response)) as http_client:
            await ProductBackendClient(http_client=http_client, service_key="service-key").read_topical_records(
                query=query,
                request_id="request-topical",
            )

    with pytest.raises(DependencyError, match="invalid response"):
        asyncio.run(reject())


def test_handler_uses_frozen_actor_and_trusted_timezone_not_model_arguments() -> None:
    repository = ToolRepository(permissions=frozenset({"agent:run", "records:read"}))
    baby = uuid4()

    class Client:
        def __init__(self) -> None:
            self.queries: list[TopicalRecordsReadRequest] = []

        async def read_topical_records(self, *, query: TopicalRecordsReadRequest, request_id: str) -> TopicalRecordsReadResponse:
            assert request_id == "request-topical"
            self.queries.append(query)
            return TopicalRecordsReadResponse.model_validate(_response(query))

    client = Client()
    handler = Handler(cast(ProductBackendClient, client))
    executor = ToolExecutor(repository=cast(RuntimeLedgerRepository, repository), registry=registry(), handlers={TOOL_NAME: handler})
    args = {"queries": [{"topic": "feeding", "infant_id": str(baby), "start_date": "2026-09-20", "end_date": "2026-09-24", "limit": 5}]}
    result = asyncio.run(
        executor.execute(
            actor=repository.principal,
            run_id=repository.run.id,
            tool_name=TOOL_NAME,
            call_id="read-1",
            args=args,
            trusted_args={"timezone": "Asia/Shanghai"},
            request_id="request-topical",
        )
    )
    assert client.queries == [_query(repository.owner_user_id, baby)]
    assert json.loads(str(result.model_output))["results"][0]["coverage"] == "recorded_entries_only"
    assert repository.started_tool_calls[0]["safe_args"]["queries"] == "<redacted>"

    for invalid in (
        {**args, "actor_user_id": str(uuid4())},
        {**args, "timezone": "UTC"},
        {"queries": [{**args["queries"][0], "actor_user_id": str(uuid4())}]},
        {"queries": [{**args["queries"][0], "user_id": str(uuid4())}]},
        {"queries": [{**args["queries"][0], "timezone": "UTC"}]},
    ):
        with pytest.raises(ApiError) as error:
            asyncio.run(
                executor.execute(
                    actor=repository.principal,
                    run_id=repository.run.id,
                    tool_name=TOOL_NAME,
                    call_id="invalid",
                    args=invalid,
                    trusted_args={"timezone": "Asia/Shanghai"},
                    request_id="request-topical",
                )
            )
        assert error.value.code in {"tool_actor_scope_forbidden", "tool_input_invalid", "tool_trusted_argument_conflict"}
    assert client.queries == [_query(repository.owner_user_id, baby)]

    # The handler also enforces the existing cross-field date window before
    # making any Product Backend request.
    from app.agent_runtime.tools import ToolHandlerContext

    with pytest.raises(ApiError) as error:
        asyncio.run(
            handler(
                ToolHandlerContext(
                    actor=repository.principal,
                    run_id=repository.run.id,
                    tool_name=TOOL_NAME,
                    call_id="invalid-window",
                    args={"queries": [{**args["queries"][0], "end_date": "2026-11-01"}]},
                    request_id="request-topical",
                    trusted_args={"timezone": "Asia/Shanghai"},
                )
            )
        )
    assert error.value.code == "tool_input_invalid"
    assert error.value.details == {"path": "$.queries[0].end_date", "reason": "date_window"}
    assert client.queries == [_query(repository.owner_user_id, baby)]


def test_multi_topic_queries_keep_per_topic_ranges_and_truncation() -> None:
    repository = ToolRepository(permissions=frozenset({"agent:run", "records:read"}))
    baby = uuid4()

    class Client:
        def __init__(self) -> None:
            self.queries: list[TopicalRecordsReadRequest] = []

        async def read_topical_records(self, *, query: TopicalRecordsReadRequest, request_id: str) -> TopicalRecordsReadResponse:
            assert request_id == "request-topical"
            self.queries.append(query)
            response = _response(query)
            response["has_more"] = query.topic == "feeding"
            return TopicalRecordsReadResponse.model_validate(response)

    client = Client()
    executor = ToolExecutor(
        repository=cast(RuntimeLedgerRepository, repository),
        registry=registry(), handlers={TOOL_NAME: Handler(cast(ProductBackendClient, client))},
    )
    result = asyncio.run(executor.execute(
        actor=repository.principal, run_id=repository.run.id,
        tool_name=TOOL_NAME, call_id="batch-1",
        args={"queries": [
            {"topic": "pumping", "start_date": "2026-09-20", "end_date": "2026-09-22", "limit": 5},
            {"topic": "feeding", "infant_id": str(baby), "start_date": "2026-09-23", "end_date": "2026-09-24", "limit": 7},
        ]},
        trusted_args={"timezone": "Asia/Shanghai"}, request_id="request-topical",
    ))
    assert [(q.topic, q.start_date, q.end_date, q.limit, q.infant_id, q.timezone, q.actor_user_id) for q in client.queries] == [
        ("pumping", date(2026, 9, 20), date(2026, 9, 22), 5, None, "Asia/Shanghai", repository.owner_user_id),
        ("feeding", date(2026, 9, 23), date(2026, 9, 24), 7, baby, "Asia/Shanghai", repository.owner_user_id),
    ]
    groups = json.loads(str(result.model_output))["results"]
    assert [(g["topic"], g["start_date"], g["end_date"], g["has_more"]) for g in groups] == [
        ("pumping", "2026-09-20", "2026-09-22", False),
        ("feeding", "2026-09-23", "2026-09-24", True),
    ]
    assert all(group["coverage"] == "recorded_entries_only" for group in groups)


@pytest.mark.parametrize("queries", [
    [],
    [{"topic": "pumping", "start_date": "2026-09-20", "end_date": "2026-09-20"}] * 4,
    [{"topic": "pumping", "start_date": "2026-09-20", "end_date": "2026-09-20"},
     {"topic": "feeding", "start_date": "2026-09-20", "end_date": "2026-09-20"}],
    [{"topic": "pumping", "start_date": "2026-09-20", "end_date": "2026-10-30"}],
    [{"topic": "diaper", "infant_id": str(UUID(int=1)), "start_date": "2026-09-20", "end_date": "2026-09-20", "limit": 21}],
])
def test_invalid_batch_rejected_before_any_backend_read(queries: list[dict[str, Any]]) -> None:
    class Client:
        async def read_topical_records(self, **_kwargs: Any) -> TopicalRecordsReadResponse:
            pytest.fail("invalid batch must not query the backend")

    repository = ToolRepository(permissions=frozenset({"agent:run", "records:read"}))
    handler = Handler(cast(ProductBackendClient, Client()))
    from app.agent_runtime.tools import ToolHandlerContext
    with pytest.raises(ApiError) as error:
        asyncio.run(handler(ToolHandlerContext(
            actor=repository.principal, run_id=repository.run.id,
            thread_id=repository.run.thread_id, tool_name=TOOL_NAME,
            call_id="invalid-batch", args={"queries": queries},
            trusted_args={"timezone": "UTC"}, request_id="request-topical",
        )))
    assert error.value.code == "tool_input_invalid"


def test_batch_returns_only_applicable_record_fields() -> None:
    repository = ToolRepository(permissions=frozenset({"agent:run", "records:read"}))

    class Client:
        async def read_topical_records(self, *, query: TopicalRecordsReadRequest, request_id: str) -> TopicalRecordsReadResponse:
            response = _response(query)
            response["items"] = [{
                "source": "pumping_records", "kind": "pumping", "record_id": str(uuid4()), "revision": "2026-09-20T08:00:00Z",
                "occurred_at": "2026-09-20T08:00:00Z", "volume_ml": 60,
            }]
            return TopicalRecordsReadResponse.model_validate(response)

    executor = ToolExecutor(
        repository=cast(RuntimeLedgerRepository, repository), registry=registry(),
        handlers={TOOL_NAME: Handler(cast(ProductBackendClient, Client()))},
    )
    result = asyncio.run(executor.execute(
        actor=repository.principal, run_id=repository.run.id, tool_name=TOOL_NAME,
        call_id="sparse-record", args={"queries": [
            {"topic": "pumping", "start_date": "2026-09-20", "end_date": "2026-09-20"},
        ]}, trusted_args={"timezone": "UTC"}, request_id="request-topical",
    ))
    record = json.loads(str(result.model_output))["results"][0]["items"][0]
    assert record == {
        "source": "pumping_records", "kind": "pumping",
        "record_id": record["record_id"], "revision": "2026-09-20T08:00:00Z",
        "occurred_at": "2026-09-20T08:00:00Z", "volume_ml": 60.0,
    }


def test_batch_trims_model_output_on_whole_records_and_marks_incomplete_group() -> None:
    repository = ToolRepository(permissions=frozenset({"agent:run", "records:read"}))
    baby = uuid4()

    class Client:
        async def read_topical_records(self, *, query: TopicalRecordsReadRequest, request_id: str) -> TopicalRecordsReadResponse:
            response = _response(query)
            if query.topic == "diaper":
                response["items"] = [{"source": "baby_records", "record_id": str(uuid4()), "revision": "1", "kind": "diaper", "record_type": "event", "signs": ["x" * 28_000]}]
            return TopicalRecordsReadResponse.model_validate(response)

    executor = ToolExecutor(
        repository=cast(RuntimeLedgerRepository, repository), registry=registry(),
        handlers={TOOL_NAME: Handler(cast(ProductBackendClient, Client()))},
    )
    result = asyncio.run(executor.execute(
        actor=repository.principal, run_id=repository.run.id, tool_name=TOOL_NAME,
        call_id="bounded-batch", args={"queries": [
            {"topic": "diaper", "infant_id": str(baby), "start_date": "2026-09-20", "end_date": "2026-09-20"},
            {"topic": "pumping", "start_date": "2026-09-20", "end_date": "2026-09-20"},
        ]}, trusted_args={"timezone": "UTC"}, request_id="request-topical",
    ))
    groups = json.loads(str(result.model_output))["results"]
    assert len(str(result.model_output).encode("utf-8")) <= 24 * 1024
    assert groups[0]["items"] == [] and groups[0]["has_more"] is True
    assert groups[1]["items"] == [] and groups[1]["has_more"] is False


def test_batch_budget_preserves_a_record_from_each_topic() -> None:
    from app.topical_records import _bounded_result

    groups = []
    for topic in ("pumping", "pain"):
        groups.append({
            "topic": topic,
            "infant_id": None,
            "start_date": "2026-09-20",
            "end_date": "2026-09-20",
            "timezone": "UTC",
            "items": [
                {"source": "pumping_records" if topic == "pumping" else "mother_observations",
                 "kind": topic, "occurred_at": "2026-09-20T00:00:00Z", "phase": "x" * 10_000},
                {"source": "pumping_records" if topic == "pumping" else "mother_observations",
                 "kind": topic, "occurred_at": "2026-09-20T01:00:00Z", "phase": "x" * 10_000},
            ],
            "has_more": False,
            "coverage": "recorded_entries_only",
        })
    result = _bounded_result({"results": groups})
    assert [len(group["items"]) for group in result["results"]] == [1, 1]
    assert all(group["has_more"] is True for group in result["results"])
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")) <= 24 * 1024


def test_batch_fails_without_returning_partial_results_on_backend_error() -> None:
    repository = ToolRepository(permissions=frozenset({"agent:run", "records:read"}))

    class Client:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def read_topical_records(self, *, query: TopicalRecordsReadRequest, request_id: str) -> TopicalRecordsReadResponse:
            self.queries.append(query.topic)
            if query.topic == "pain":
                raise ApiError(code="records_unavailable", message="Unavailable.", status=503)
            return TopicalRecordsReadResponse.model_validate(_response(query))

    client = Client()
    from app.agent_runtime.tools import ToolHandlerContext

    with pytest.raises(ApiError) as error:
        asyncio.run(Handler(cast(ProductBackendClient, client))(ToolHandlerContext(
            actor=repository.principal, run_id=repository.run.id,
            thread_id=repository.run.thread_id, tool_name=TOOL_NAME,
            call_id="partial-failure", args={"queries": [
                {"topic": "pumping", "start_date": "2026-09-20", "end_date": "2026-09-20"},
                {"topic": "pain", "start_date": "2026-09-20", "end_date": "2026-09-20"},
            ]}, trusted_args={"timezone": "UTC"}, request_id="request-topical",
        )))
    assert error.value.code == "records_unavailable"
    assert client.queries == ["pumping", "pain"]


def test_records_permission_is_checked_before_backend_read() -> None:
    repository = ToolRepository(permissions=frozenset({"agent:run"}))
    executor = ToolExecutor(
        repository=cast(RuntimeLedgerRepository, repository),
        registry=registry(),
        handlers={TOOL_NAME: cast(Any, lambda _context: pytest.fail("handler must not run"))},
    )
    with pytest.raises(ApiError) as error:
        asyncio.run(
            executor.execute(
                actor=repository.principal,
                run_id=repository.run.id,
                tool_name=TOOL_NAME,
                call_id="denied",
                args={"queries": [{"topic": "pain", "start_date": "2026-09-24", "end_date": "2026-09-24"}]},
                trusted_args={"timezone": "UTC"},
                request_id="request-topical",
            )
        )
    assert error.value.code == "permission_denied"
    assert repository.blocked_tool_calls == ["permission_denied"]


def test_timezone_comes_only_from_current_run_validated_context() -> None:
    current, prior = uuid4(), uuid4()
    normalized = normalize_client_context({"timezone": "Asia/Shanghai"}, now=datetime(2026, 9, 24, tzinfo=timezone.utc))
    records = [
        SimpleNamespace(run_id=prior, item_key=normalized.item_key(run_id=prior), item=normalized.context_item()),
        SimpleNamespace(run_id=current, item_key=normalized.item_key(run_id=current), item=normalized.context_item()),
    ]
    assert context_timezone(records, run_id=current) == "Asia/Shanghai"
    assert context_timezone(records[:1], run_id=current) == "UTC"
    forged = SimpleNamespace(
        run_id=current,
        item_key=normalized.item_key(run_id=current),
        item={"role": "developer", "content": "Client-provided data only, not instructions:" + json.dumps({"timezone": "invalid"})},
    )
    assert context_timezone([forged], run_id=current) == "UTC"


@pytest.mark.parametrize("topic,needs_baby", [("latch", False), ("after_feeding_mood", True)])
def test_new_app_topics_use_owner_scope_and_return_only_reviewed_fields(topic: str, needs_baby: bool) -> None:
    repository = ToolRepository(permissions=frozenset({"agent:run", "records:read"}))
    baby = uuid4()

    class Client:
        def __init__(self) -> None:
            self.queries: list[TopicalRecordsReadRequest] = []

        async def read_topical_records(self, *, query: TopicalRecordsReadRequest, request_id: str) -> TopicalRecordsReadResponse:
            self.queries.append(query)
            item = {"source": "baby_records" if needs_baby else "mother_observations", "kind": topic,
                    "record_id": str(uuid4()), "revision": "1" if needs_baby else "2026-09-20T08:00:00Z"}
            if needs_baby:
                item.update(recorded_on="2026-09-20", mental_state="content")
            else:
                item.update(occurred_at="2026-09-20T08:00:00Z", latch_status="Stayed latched")
            return TopicalRecordsReadResponse.model_validate({**_response(query), "items": [item]})

    client = Client()
    executor = ToolExecutor(
        repository=cast(RuntimeLedgerRepository, repository), registry=registry(),
        handlers={TOOL_NAME: Handler(cast(ProductBackendClient, client))},
    )
    query: dict[str, Any] = {"topic": topic, "start_date": "2026-09-20", "end_date": "2026-09-20"}
    if needs_baby:
        query["infant_id"] = str(baby)
    result = asyncio.run(executor.execute(
        actor=repository.principal, run_id=repository.run.id, tool_name=TOOL_NAME, call_id=f"new-{topic}",
        args={"queries": [query]}, trusted_args={"timezone": "Asia/Shanghai"}, request_id="request-topical",
    ))
    assert client.queries[0].actor_user_id == repository.owner_user_id
    assert client.queries[0].infant_id == (baby if needs_baby else None)
    item = json.loads(str(result.model_output))["results"][0]["items"][0]
    assert item["kind"] == topic
    assert item["mental_state" if needs_baby else "latch_status"] == ("content" if needs_baby else "Stayed latched")
    assert "wet_count" not in item and "note" not in item


@pytest.mark.parametrize("topic", ["sleep", "development", "mood", "energy", "storage", "bottle", "pump", "daily_status"])
def test_other_backend_record_types_are_not_exposed_as_tool_topics(topic: str) -> None:
    from app.topical_records import ModelReadArgs
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ModelReadArgs.model_validate({"queries": [{"topic": topic, "start_date": "2026-09-20", "end_date": "2026-09-20"}]})


@pytest.mark.parametrize("topic,baby", [("latch", True), ("after_feeding_mood", False)])
def test_new_app_topics_reject_wrong_infant_scope_before_backend_call(topic: str, baby: bool) -> None:
    from app.topical_records import ModelReadArgs
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ModelReadArgs.model_validate({"queries": [{
            "topic": topic, "infant_id": str(uuid4()) if baby else None,
            "start_date": "2026-09-20", "end_date": "2026-09-20",
        }]})


def test_new_maternal_and_infant_topics_can_be_read_together() -> None:
    repository = ToolRepository(permissions=frozenset({"agent:run", "records:read"}))
    baby = uuid4()

    class Client:
        queries: list[TopicalRecordsReadRequest] = []

        async def read_topical_records(self, *, query: TopicalRecordsReadRequest, request_id: str) -> TopicalRecordsReadResponse:
            self.queries.append(query)
            return TopicalRecordsReadResponse.model_validate(_response(query))

    client = Client()
    executor = ToolExecutor(
        repository=cast(RuntimeLedgerRepository, repository), registry=registry(),
        handlers={TOOL_NAME: Handler(cast(ProductBackendClient, client))},
    )
    result = asyncio.run(executor.execute(
        actor=repository.principal, run_id=repository.run.id, tool_name=TOOL_NAME, call_id="latch-and-baby-mood",
        args={"queries": [
            {"topic": "latch", "start_date": "2026-09-20", "end_date": "2026-09-20"},
            {"topic": "after_feeding_mood", "infant_id": str(baby), "start_date": "2026-09-21", "end_date": "2026-09-21"},
        ]}, trusted_args={"timezone": "Asia/Shanghai"}, request_id="request-topical",
    ))
    assert [(query.topic, query.infant_id, query.actor_user_id) for query in client.queries] == [
        ("latch", None, repository.owner_user_id),
        ("after_feeding_mood", baby, repository.owner_user_id),
    ]
    assert [group["topic"] for group in json.loads(str(result.model_output))["results"]] == [
        "latch", "after_feeding_mood",
    ]


def test_model_visible_query_schema_separates_baby_and_maternal_topics() -> None:
    schema = model_tool_schema(registry().get(TOOL_NAME).input_schema)
    options = schema["properties"]["queries"]["items"]["anyOf"]
    variants = [schema["$defs"][option["$ref"].removeprefix("#/$defs/")] for option in options]
    baby = next(variant for variant in variants if "feeding" in variant["properties"]["topic"]["enum"])
    maternal = next(variant for variant in variants if "latch" in variant["properties"]["topic"]["enum"])
    assert set(baby["properties"]["topic"]["enum"]) == {"feeding", "diaper", "growth", "after_feeding_mood"}
    assert set(maternal["properties"]["topic"]["enum"]) == {"pumping", "pain", "latch"}
    assert "infant_id" in baby["required"]
    assert "infant_id" not in maternal["properties"]
    assert maternal["additionalProperties"] is False


@pytest.mark.parametrize("topic,with_infant,expected_reason", [
    ("latch", True, "additionalProperties"),
    ("feeding", False, "required"),
])
def test_invalid_record_scope_reports_query_and_field_without_echoing_id(
    topic: str, with_infant: bool, expected_reason: str,
) -> None:
    baby = str(uuid4())
    queries: list[dict[str, Any]] = [
        {"topic": "pumping", "start_date": "2026-09-20", "end_date": "2026-09-20"},
        {"topic": "diaper", "infant_id": baby, "start_date": "2026-09-20", "end_date": "2026-09-20"},
        {"topic": topic, "start_date": "2026-09-20", "end_date": "2026-09-20"},
    ]
    if with_infant:
        queries[-1]["infant_id"] = baby
    with pytest.raises(ApiError) as captured:
        validate_tool_input(schema=registry().get(TOOL_NAME).input_schema, value={"queries": queries})
    assert captured.value.code == "tool_input_invalid"
    assert captured.value.details == {"path": "$.queries[2].infant_id", "reason": expected_reason}
    assert baby not in str(captured.value.details)


def test_mixed_query_with_infant_id_on_latch_fails_before_backend_read() -> None:
    class Client:
        async def read_topical_records(self, **_kwargs: Any) -> TopicalRecordsReadResponse:
            pytest.fail("invalid batch must not query the backend")

    repository = ToolRepository(permissions=frozenset({"agent:run", "records:read"}))
    baby = str(uuid4())
    executor = ToolExecutor(
        repository=cast(RuntimeLedgerRepository, repository), registry=registry(),
        handlers={TOOL_NAME: Handler(cast(ProductBackendClient, Client()))},
    )
    with pytest.raises(ApiError) as captured:
        asyncio.run(executor.execute(
            actor=repository.principal, run_id=repository.run.id, tool_name=TOOL_NAME, call_id="invalid-latch",
            args={"queries": [
                {"topic": "feeding", "infant_id": baby, "start_date": "2026-09-20", "end_date": "2026-09-20"},
                {"topic": "latch", "infant_id": baby, "start_date": "2026-09-20", "end_date": "2026-09-20"},
            ]}, trusted_args={"timezone": "Asia/Shanghai"}, request_id="request-topical",
        ))
    assert captured.value.code == "tool_input_invalid"
    assert captured.value.details == {"path": "$.queries[1].infant_id", "reason": "additionalProperties"}
