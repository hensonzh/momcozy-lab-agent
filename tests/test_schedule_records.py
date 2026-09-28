"""Product schedule/record tools: frozen authority, immediate Action, and truthful receipts."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from app.agent_runtime.actions import ActionProposed
from app.agent_runtime.ledger.repository import RuntimeLedgerRepository
from app.agent_runtime.tools import ToolHandlerContext
from app.agent_runtime.tools.executor import ToolExecutor
from app.agent_runtime.tools.validation import validate_tool_input
from app.core.errors import ApiError
from app.infrastructure.product_backend.contracts import (
    AgentBatchResponse, AgentScheduleReadRequest, AgentScheduleReadResponse,
)
from app.schedule_records import BatchApplicator, ChangeHandler, ReadHandler, registry
from test_tool_observability import ToolRepository


def _context(repository: ToolRepository, name: str, args: dict[str, Any], *, timezone_name: str = 'Asia/Shanghai') -> ToolHandlerContext:
    return ToolHandlerContext(actor=repository.principal, run_id=repository.run.id,
        thread_id=repository.run.thread_id, tool_name=name, call_id='tool-call-1',
        args=args, request_id='request-1', trusted_args={'timezone': timezone_name})


def _schedule() -> dict[str, Any]:
    return {'operations': [{'op': 'create', 'fields': {
        'title': '复诊', 'date': '2026-09-28', 'start_time': '11:30', 'note': ''}}]}


def _records() -> dict[str, Any]:
    return {'operations': [{'op': 'create', 'topic': 'pumping', 'fields': {
        'occurred_at': '2026-09-23T10:00:00Z', 'side': 'Left side', 'volume_ml': 50}}]}


def test_schedule_read_restricts_owner_timezone_window_and_pagination() -> None:
    repo = ToolRepository(permissions=frozenset({'agent:run', 'plans:read'}))

    class Client:
        queries: list[AgentScheduleReadRequest] = []
        async def read_agent_schedule(self, *, query: AgentScheduleReadRequest, request_id: str) -> AgentScheduleReadResponse:
            assert request_id == 'request-1'
            self.queries.append(query)
            return AgentScheduleReadResponse(personal=[], server_time=datetime.now(timezone.utc), has_more=False)

    client = Client()
    executor = ToolExecutor(repository=cast(RuntimeLedgerRepository, repo), registry=registry(),
        handlers={'read_schedule': ReadHandler(client)})
    args = {'start_date': '2026-09-26', 'end_date': '2026-09-29', 'offset': 8, 'limit': 10}
    result = asyncio.run(executor.execute(actor=repo.principal, run_id=repo.run.id,
        tool_name='read_schedule', call_id='read-1', args=args,
        trusted_args={'timezone': 'Asia/Shanghai'}, request_id='request-1'))
    assert json.loads(str(result.model_output))['personal'] == []
    assert client.queries == [AgentScheduleReadRequest(actor_user_id=repo.owner_user_id,
        timezone='Asia/Shanghai', start_date=date(2026, 9, 26),
        end_date=date(2026, 9, 29), offset=8, limit=10)]
    for bad_args in ({**args, 'timezone': 'UTC'}, {**args, 'actor_user_id': str(uuid4())},
                     {**args, 'limit': 101}):
        with pytest.raises(ApiError):
            asyncio.run(executor.execute(actor=repo.principal, run_id=repo.run.id,
                tool_name='read_schedule', call_id='invalid', args=bad_args,
                trusted_args={'timezone': 'Asia/Shanghai'}, request_id='request-1'))
    with pytest.raises(ApiError) as error:
        asyncio.run(ReadHandler(client)(_context(repo, 'read_schedule', {**args, 'end_date': '2026-12-01'})))
    assert error.value.code == 'tool_input_invalid'
    assert error.value.details == {'path': '$.end_date', 'reason': 'date_window'}
    assert len(client.queries) == 1


@pytest.mark.parametrize('tool,operation,field', [
    ('change_records', {'op':'create','topic':'pumping','fields':{'occurred_at':'2026-09-23T10:00:00Z','volume_ml':60}}, 'side'),
    ('change_records', {'op':'create','topic':'pain','fields':{'occurred_at':'2026-09-23T10:00:00Z','pain_score':2}}, 'side'),
    ('change_records', {'op':'create','topic':'latch','fields':{'occurred_at':'2026-09-23T10:00:00Z'}}, 'latch_status'),
    ('change_schedule', {'op':'create','fields':{'title':'复诊','date':'2026-09-28'}}, 'start_time'),
    ('change_schedule', {'op':'update','fields':{'title':'复诊'}}, 'task_id'),
])
def test_incomplete_write_is_rejected_before_action(tool: str, operation: dict[str, Any], field: str) -> None:
    payload = {'operations':[operation]}
    with pytest.raises(ApiError) as error:
        validate_tool_input(schema=registry().get(tool).input_schema, value=payload)
    assert error.value.code == 'tool_input_invalid'
    repo = ToolRepository(permissions=frozenset({'agent:run','records:write','plans:write'}))

    class Proposer:
        called = False
        async def propose_action(self, _proposal: Any) -> Any:
            self.called = True
            pytest.fail('No action may be created for incomplete input')

    proposer = Proposer()
    with pytest.raises(ApiError) as received:
        asyncio.run(ChangeHandler(proposer, 'records.batch.change' if tool == 'change_records' else 'schedule.batch.change')(
            _context(repo, tool, payload)))
    assert received.value.code == 'tool_input_invalid'
    assert received.value.details['path'].startswith('$.operations[0]')
    assert field in received.value.details['path']
    assert not proposer.called


def test_all_record_create_variants_require_their_topic_fields_in_model_schema() -> None:
    infant = str(uuid4())
    time = '2026-09-23T10:00:00Z'
    invalid = [
        {'op':'create','topic':'feeding','infant_id':infant,'fields':{'occurred_at':time,'method':'breastfeeding'}},
        {'op':'create','topic':'diaper','infant_id':infant,'record_type':'event','fields':{'occurred_at':time}},
        {'op':'create','topic':'growth','infant_id':infant,'fields':{'recorded_on':'2026-09-23','metric':'weight'}},
        {'op':'create','topic':'after_feeding_mood','infant_id':infant,'fields':{'recorded_on':'2026-09-23'}},
        {'op':'create','topic':'pumping','fields':{'occurred_at':time,'volume_ml':60,'side':'both'}},
    ]
    schema = registry().get('change_records').input_schema
    for operation in invalid:
        with pytest.raises(ApiError) as error:
            validate_tool_input(schema=schema, value={'operations':[operation]})
        assert error.value.code == 'tool_input_invalid'


def test_invalid_pumping_side_reports_only_field_path_not_submitted_value() -> None:
    operation = {'op':'create','topic':'pumping','fields':{
        'occurred_at':'2026-09-23T10:00:00Z','side':'both','volume_ml':60}}
    with pytest.raises(ApiError) as error:
        validate_tool_input(schema=registry().get('change_records').input_schema, value={'operations':[operation]})
    assert error.value.details == {'path':'$.operations[0].fields.side','reason':'enum'}
    assert 'both' not in str(error.value.details)


def test_batch_preflight_reports_missing_fields_across_items_without_values() -> None:
    operations = [
        {'op':'create','topic':'pumping','fields':{'volume_ml':60}},
        {'op':'create','topic':'pain','fields':{'occurred_at':'2026-09-23T10:00:00Z','pain_score':2}},
    ]
    with pytest.raises(ApiError) as error:
        validate_tool_input(schema=registry().get('change_records').input_schema, value={'operations':operations})
    paths = {item['path'] for item in error.value.details['issues']}
    assert {'$.operations[0].fields.occurred_at','$.operations[0].fields.side',
            '$.operations[1].fields.side','$.operations[1].fields.phase',
            '$.operations[1].fields.impact'} <= paths
    assert '60' not in str(error.value.details)


def test_semantic_record_and_schedule_errors_never_propose_an_action() -> None:
    infant = str(uuid4())
    repo = ToolRepository(permissions=frozenset({'agent:run','records:write','plans:write'}))

    class Proposer:
        calls = 0
        async def propose_action(self, _proposal: Any) -> Any:
            self.calls += 1
            pytest.fail('Invalid input must not create an action')

    proposer = Proposer()
    invalid_records = [
        {'op':'create','topic':'diaper','infant_id':infant,'record_type':'daily_summary','fields':{'recorded_on':'2026-09-23'}},
        {'op':'create','topic':'growth','infant_id':infant,'fields':{'recorded_on':'2026-09-23','metric':'weight','value':51}},
        {'op':'create','topic':'diaper','infant_id':infant,'record_type':'event','fields':{'occurred_at':'2026-09-23T10:00:00Z','diaper_kind':'wet','color':'green'}},
        {'op':'update','topic':'pumping','record_source':'baby_records','record_id':str(uuid4()),'revision':'1','fields':{'volume_ml':60}},
        {'op':'update','topic':'pumping','record_source':'pumping_records','record_id':str(uuid4()),'revision':'1','fields':{'latch_status':'含得稳'}},
        {'op':'update','topic':'pumping','record_source':'pumping_records','record_id':str(uuid4()),'revision':'1','fields':{'side':None}},
    ]
    for operation in invalid_records:
        with pytest.raises(ApiError) as error:
            asyncio.run(ChangeHandler(proposer, 'records.batch.change')(_context(repo, 'change_records', {'operations':[operation]})))
        assert error.value.code == 'tool_input_invalid'
    for operation in (
        {'op':'create','fields':{'title':'复诊','date':'2026-09-28','start_time':'9am'}},
        {'op':'create','fields':{'title':'   ','date':'2026-09-28','start_time':'09:00'}},
    ):
        with pytest.raises(ApiError):
            asyncio.run(ChangeHandler(proposer, 'schedule.batch.change')(_context(repo, 'change_schedule', {'operations':[operation]})))
    assert proposer.calls == 0


def test_future_record_time_is_rejected_without_action() -> None:
    repo = ToolRepository(permissions=frozenset({'agent:run','records:write'}))
    class Proposer:
        async def propose_action(self, _proposal: Any) -> Any:
            pytest.fail('Future-time record must not create an action')
    operation = {'op':'create','topic':'pumping','fields':{
        'occurred_at':'2099-09-23T10:00:00Z','side':'左侧','volume_ml':60}}
    with pytest.raises(ApiError) as error:
        asyncio.run(ChangeHandler(Proposer(), 'records.batch.change')(_context(repo, 'change_records', {'operations':[operation]})))
    assert error.value.details == {'path':'$.operations[0].fields.occurred_at','reason':'future_time'}


def test_all_record_topics_and_schedule_update_have_valid_model_shapes() -> None:
    from app.schedule_records import RecordChanges, ScheduleChanges
    baby, record, task = str(uuid4()), str(uuid4()), str(uuid4())
    occurred = '2026-09-23T10:00:00Z'
    operations = [
        {'op':'create','topic':'feeding','infant_id':baby,'fields':{'occurred_at':occurred,'method':'breastfeeding','side':'left'}},
        {'op':'create','topic':'feeding','infant_id':baby,'fields':{'occurred_at':occurred,'method':'formula','volume_ml':60}},
        {'op':'create','topic':'pumping','fields':{'occurred_at':occurred,'side':'左侧','volume_ml':60}},
        {'op':'create','topic':'diaper','infant_id':baby,'record_type':'event','fields':{'occurred_at':occurred,'diaper_kind':'wet'}},
        {'op':'create','topic':'diaper','infant_id':baby,'record_type':'daily_summary','fields':{'recorded_on':'2026-09-23','wet_count':2}},
        {'op':'create','topic':'pain','fields':{'occurred_at':occurred,'pain_score':2,'side':'左侧','phase':'泵奶时','impact':'可以继续喂'}},
        {'op':'create','topic':'latch','fields':{'occurred_at':occurred,'latch_status':'含得稳'}},
        {'op':'create','topic':'growth','infant_id':baby,'fields':{'recorded_on':'2026-09-23','metric':'weight','value':4.5}},
        {'op':'create','topic':'after_feeding_mood','infant_id':baby,'fields':{'recorded_on':'2026-09-23','mental_state':'content'}},
        {'op':'update','topic':'pumping','record_source':'pumping_records','record_id':record,'revision':'1',
         'fields':{'side':'右侧'}},
    ]
    payload = {'operations':operations}
    validate_tool_input(schema=registry().get('change_records').input_schema, value=payload)
    assert len(RecordChanges.model_validate(payload).operations) == 10
    schedule = {'operations':[{'op':'update','task_id':task,'expected_updated_at':occurred,'fields':{'title':'复诊'}}]}
    validate_tool_input(schema=registry().get('change_schedule').input_schema, value=schedule)
    assert ScheduleChanges.model_validate(schedule).operations[0].op == 'update'


def test_change_handlers_propose_owner_scoped_durable_batch_and_report_only_applied() -> None:
    repo = ToolRepository(permissions=frozenset({'agent:run', 'records:write', 'plans:write'}))

    class Proposer:
        proposals: list[Any] = []
        status = 'applied'
        async def propose_action(self, proposal: Any) -> ActionProposed:
            self.proposals.append(proposal)
            return ActionProposed(id=uuid4(), action_type=proposal.action_type,
                status=self.status, requires_confirmation=False, error_code='version_conflict' if self.status == 'failed' else '')
        async def get_action(self, *, owner_user_id: Any, action_id: Any) -> Any:
            assert owner_user_id == repo.owner_user_id
            return SimpleNamespace(result_payload={'details': {'batch_id': str(action_id), 'items': [
                {'op': 'create', 'resource_id': str(uuid4()), 'revision': '1'}]}})

    proposer = Proposer()
    for name, action_type, args in (
        ('change_records', 'records.batch.change', _records()),
        ('change_schedule', 'schedule.batch.change', _schedule()),
    ):
        handler = ChangeHandler(proposer, action_type)
        context = _context(repo, name, args)
        applied = asyncio.run(handler(context)).canonical_output
        assert applied['ok'] is True and applied['action_status'] == 'applied'
        proposal = proposer.proposals[-1]
        assert proposal.actor_user_id == repo.owner_user_id and proposal.run_id == repo.run.id
        assert proposal.action_type == action_type and proposal.apply_payload['operations'] == args['operations']
        assert proposal.idempotency_key
        asyncio.run(handler(context))
        assert proposal.idempotency_key == proposer.proposals[-1].idempotency_key
        if name == 'change_records':
            assert proposal.apply_payload['timezone'] == 'Asia/Shanghai'
        else:
            assert 'timezone' not in proposal.apply_payload
        proposer.status = 'failed'
        failed = asyncio.run(handler(context)).canonical_output
        assert failed['ok'] is False and failed['action_status'] == 'failed'
        assert failed['error_code'] == 'version_conflict' and failed['batch_id'] is None
        proposer.status = 'applied'


def test_change_handlers_reject_missing_trusted_timezone_and_invalid_payload_before_action() -> None:
    repo = ToolRepository()
    class Proposer:
        calls = 0
        async def propose_action(self, proposal: Any) -> None:
            self.calls += 1
    proposer = Proposer()
    with pytest.raises(ApiError, match='timezone'):
        asyncio.run(ChangeHandler(proposer, 'records.batch.change')(_context(repo, 'change_records', _records(), timezone_name='')))
    with pytest.raises(ApiError, match='invalid'):
        asyncio.run(ChangeHandler(proposer, 'records.batch.change')(_context(repo, 'change_records',
            {'operations': [{**_records()['operations'][0], 'topic': 'sleep'}]})))
    assert proposer.calls == 0


def test_change_tool_run_permission_prevents_any_action() -> None:
    repo = ToolRepository(permissions=frozenset({'agent:run'}))
    class Proposer:
        calls = 0
        async def propose_action(self, proposal: Any) -> None:
            self.calls += 1
    proposer = Proposer()
    executor = ToolExecutor(repository=cast(RuntimeLedgerRepository, repo), registry=registry(), handlers={
        'change_records': ChangeHandler(proposer, 'records.batch.change'),
        'change_schedule': ChangeHandler(proposer, 'schedule.batch.change'),
    })
    for name, args in (('change_records', _records()), ('change_schedule', _schedule())):
        with pytest.raises(ApiError) as error:
            asyncio.run(executor.execute(actor=repo.principal, run_id=repo.run.id,
                tool_name=name, call_id='forbidden', args=args,
                trusted_args={'timezone': 'Asia/Shanghai'}, request_id='request-1'))
        assert error.value.code == 'permission_denied'
    assert proposer.calls == 0


def test_batch_applicators_forward_durable_action_identity_and_receipt() -> None:
    owner, action_id, run_id = uuid4(), uuid4(), uuid4()
    action = SimpleNamespace(actor_user_id=owner, id=action_id, run_id=run_id,
        apply_payload={'timezone': 'Asia/Shanghai', 'operations': _records()['operations']})
    class Client:
        calls: list[Any] = []
        async def write_agent_records(self, *, command: Any, idempotency_key: str, request_id: str) -> AgentBatchResponse:
            self.calls.append((command, idempotency_key, request_id))
            return AgentBatchResponse.model_validate({'batch_id': action_id, 'items': [{
                'op': 'create', 'resource_id': str(uuid4()), 'revision': '1'}]})
    client = Client()
    result = asyncio.run(BatchApplicator(client, 'records')(action))
    assert result.resource_id == str(action_id) and len(result.details['items']) == 1
    command, key, request = client.calls[0]
    assert command.actor_user_id == owner and command.timezone == 'Asia/Shanghai'
    assert key == str(action_id) and request == str(run_id)



@pytest.mark.parametrize(('domain', 'operations', 'expected_tabs'), [
    ('records', [
        {'op': 'create', 'topic': 'pumping', 'fields': {'occurred_at': '2026-09-23T10:00:00Z', 'side': 'Left side', 'volume_ml': 50}},
    ], ['me']),
    ('records', [
        {'op': 'create', 'topic': 'feeding', 'infant_id': str(uuid4()), 'fields': {'occurred_at': '2026-09-23T10:00:00Z', 'method': 'breastfeeding', 'side': 'left'}},
    ], ['baby']),
    ('records', [
        {'op': 'create', 'topic': 'pain', 'fields': {'occurred_at': '2026-09-23T10:00:00Z', 'pain_score': 2, 'side': 'Left side', 'phase': 'When latching', 'impact': 'Could continue'}},
        {'op': 'create', 'topic': 'diaper', 'infant_id': str(uuid4()), 'record_type': 'event', 'fields': {'occurred_at': '2026-09-23T10:00:00Z', 'diaper_kind': 'wet'}},
    ], ['me', 'baby']),
    ('records', [
        {'op': 'update', 'topic': 'pumping', 'record_source': 'pumping_records',
         'record_id': str(uuid4()), 'revision': '1', 'fields': {'volume_ml': 70}},
    ], ['me']),
    ('records', [
        {'op': 'update', 'topic': 'growth', 'infant_id': str(uuid4()), 'record_source': 'growth_records',
         'record_id': str(uuid4()), 'revision': '1', 'fields': {'weight_kg': 5.1}},
    ], ['baby']),
    ('schedule', _schedule()['operations'], ['schedule']),
    ('schedule', [
        {'op': 'update', 'task_id': str(uuid4()), 'expected_updated_at': '2026-09-23T10:00:00Z',
         'fields': {'title': '复诊改期'}},
    ], ['schedule']),
])
def test_applied_batch_emits_only_affected_primary_tabs(domain: str, operations: list[dict[str, Any]], expected_tabs: list[str]) -> None:
    owner, action_id, run_id = uuid4(), uuid4(), uuid4()
    action = SimpleNamespace(actor_user_id=owner, id=action_id, run_id=run_id,
        apply_payload={'timezone': 'Asia/Shanghai', 'operations': operations})

    class Client:
        async def write_agent_records(self, *, command: Any, idempotency_key: str, request_id: str) -> AgentBatchResponse:
            return AgentBatchResponse.model_validate({'batch_id': action_id, 'items': [
                {'op': operation.op, 'resource_id': str(uuid4()), 'revision': '1'} for operation in command.operations
            ]})

        async def write_agent_schedule(self, *, command: Any, idempotency_key: str, request_id: str) -> AgentBatchResponse:
            return AgentBatchResponse.model_validate({'batch_id': action_id, 'items': [
                {'op': operation.op, 'resource_id': str(uuid4()), 'revision': '1'} for operation in command.operations
            ]})

    result = asyncio.run(BatchApplicator(Client(), domain)(action))
    assert result.application_events == ({'type': 'product.tabs.updated', 'payload': {'tabs': expected_tabs}},)
    assert 'volume_ml' not in str(result.application_events)

def test_schedule_read_truncates_only_model_view_and_reports_has_more() -> None:
    repo = ToolRepository(permissions=frozenset({'agent:run', 'plans:read'}))
    entries = [
        {'id': str(uuid4()), 'title': f'Event {index}', 'date': '2026-09-28',
         'start_time': '10:00', 'note': 'A' * 120, 'updated_at': '2026-09-27T10:00:00Z'}
        for index in range(100)
    ]

    class Client:
        async def read_agent_schedule(self, *, query: AgentScheduleReadRequest, request_id: str) -> AgentScheduleReadResponse:
            return AgentScheduleReadResponse.model_validate({
                'personal': entries, 'server_time': '2026-09-27T12:00:00Z', 'has_more': False,
            })

    result = asyncio.run(ReadHandler(Client())(_context(repo, 'read_schedule', {
        'start_date': '2026-09-27', 'end_date': '2026-09-29', 'limit': 100,
    })))
    assert len(result.canonical_output['personal']) == 100
    assert result.canonical_output['has_more'] is False
    assert 0 < len(result.model_output['personal']) < 100
    assert result.model_output['has_more'] is True
    assert len(json.dumps(result.model_output, ensure_ascii=False, separators=(',', ':')).encode()) <= 24 * 1024


def test_product_client_preserves_only_explicit_fields_and_maps_failed_write() -> None:
    import httpx

    from app.core.errors import DependencyError
    from app.infrastructure.product_backend import ProductBackendClient
    from app.infrastructure.product_backend.contracts import AgentRecordBatchRequest, AgentScheduleBatchRequest

    owner, batch_id = uuid4(), uuid4()
    sent: list[httpx.Request] = []

    async def success(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        operation = json.loads(request.content)['operations'][0]
        if request.url.path.endswith('records/batch'):
            assert set(operation) == {'op', 'topic', 'fields'}
            assert operation['fields'] == _records()['operations'][0]['fields']
        else:
            assert set(operation) == {'op', 'fields'}
            assert operation['fields'] == _schedule()['operations'][0]['fields']
        assert request.headers['Idempotency-Key'] == str(batch_id)
        return httpx.Response(200, json={'batch_id': str(batch_id), 'items': [{
            'op': 'create', 'resource_id': str(uuid4()), 'revision': '1'}]})

    async def run() -> None:
        async with httpx.AsyncClient(base_url='https://product.test', transport=httpx.MockTransport(success)) as http:
            client = ProductBackendClient(http_client=http, service_key='service-key')
            result = await client.write_agent_records(command=AgentRecordBatchRequest(
                actor_user_id=owner, timezone='Asia/Shanghai', operations=_records()['operations']),
                idempotency_key=str(batch_id), request_id='run-1')
            assert result.batch_id == batch_id
            result = await client.write_agent_schedule(command=AgentScheduleBatchRequest(
                actor_user_id=owner, operations=_schedule()['operations']),
                idempotency_key=str(batch_id), request_id='run-1')
            assert result.batch_id == batch_id
    asyncio.run(run())
    assert [request.url.path for request in sent] == [
        '/v1/internal/agent/records/batch', '/v1/internal/agent/schedule/batch',
    ]

    async def conflict(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={'error': {'code': 'version_conflict', 'message': 'Record changed.'}})

    async def fail() -> None:
        async with httpx.AsyncClient(base_url='https://product.test', transport=httpx.MockTransport(conflict)) as http:
            client = ProductBackendClient(http_client=http, service_key='service-key')
            await client.write_agent_schedule(command=AgentScheduleBatchRequest(
                actor_user_id=owner, operations=_schedule()['operations']),
                idempotency_key=str(batch_id), request_id='run-1')
    with pytest.raises(DependencyError) as exc:
        asyncio.run(fail())
    assert exc.value.code == 'version_conflict'


def test_write_actions_do_not_open_a_confirmation_card() -> None:
    from app.agent_runtime.actions import ActionPolicy
    from app.schedule_records import SCHEDULE_RECORDS_CAPABILITY

    policy = ActionPolicy(rules=SCHEDULE_RECORDS_CAPABILITY.action_policy_rules)
    for action_type, permission in (
        ('records.batch.change', 'records:write'), ('schedule.batch.change', 'plans:write'),
    ):
        rule = policy.rules[action_type]
        assert rule.required_permissions == frozenset({permission})
        assert rule.blocking_policy == 'must_wait' and rule.requires_confirmation is False
        assert rule.idempotency_required and rule.audit_required


def test_applied_action_requires_a_complete_matching_receipt_before_reporting_success() -> None:
    repo = ToolRepository()

    class Proposer:
        def __init__(self, details: dict[str, Any]) -> None:
            self.details = details
            self.action_id = uuid4()

        async def propose_action(self, proposal: Any) -> ActionProposed:
            return ActionProposed(id=self.action_id, action_type=proposal.action_type,
                status='applied', requires_confirmation=False)

        async def get_action(self, *, owner_user_id: Any, action_id: Any) -> Any:
            return SimpleNamespace(result_payload={'details': self.details})

    for details in ({}, {'batch_id': str(uuid4()), 'items': [{
        'op': 'create', 'resource_id': str(uuid4()), 'revision': '1'}]},
        {'batch_id': str(uuid4()), 'items': []}):
        proposer = Proposer(details)
        with pytest.raises(ApiError) as error:
            asyncio.run(ChangeHandler(proposer, 'records.batch.change')(_context(repo, 'change_records', _records())))
        assert error.value.code == 'tool_result_invalid'

    target = uuid4()
    operation = {'operations': [{'op': 'update', 'topic': 'pumping', 'record_source': 'pumping_records',
        'record_id': str(target), 'revision': '2026-09-23T10:00:00Z', 'fields': {'volume_ml': 70}}]}
    proposer = Proposer({})
    proposer.details = {'batch_id': str(proposer.action_id), 'items': [{
        'op': 'update', 'resource_id': str(uuid4()), 'revision': '2026-09-23T11:00:00Z'}]}
    with pytest.raises(ApiError) as error:
        asyncio.run(ChangeHandler(proposer, 'records.batch.change')(_context(repo, 'change_records', operation)))
    assert error.value.code == 'tool_result_invalid'


def test_real_action_service_executes_batch_once_and_reuses_receipt_on_tool_retry() -> None:
    from app.agent_runtime.actions import ActionExecutor, ActionPolicy, RuntimeActionService
    from app.schedule_records import SCHEDULE_RECORDS_CAPABILITY
    from test_runtime_actions import FakeActionRepository

    owner, run_id = uuid4(), uuid4()
    repository = FakeActionRepository(owner_id=owner, run_id=run_id)

    class Client:
        calls: list[tuple[Any, str, str]] = []

        async def write_agent_records(self, *, command: Any, idempotency_key: str, request_id: str) -> AgentBatchResponse:
            self.calls.append((command, idempotency_key, request_id))
            return AgentBatchResponse.model_validate({'batch_id': idempotency_key, 'items': [{
                'op': 'create', 'resource_id': str(uuid4()), 'revision': '1',
            }]})

    client = Client()
    policy = ActionPolicy(rules=SCHEDULE_RECORDS_CAPABILITY.action_policy_rules)
    service = RuntimeActionService(repository=cast(RuntimeLedgerRepository, repository), executor=ActionExecutor(
        repository=cast(RuntimeLedgerRepository, repository),
        applicators={'records.batch.change': BatchApplicator(client, 'records')}, policy=policy))
    from app.auth import RuntimePrincipal
    principal = RuntimePrincipal.from_authorization_context(repository.run.authorization_context)
    context = ToolHandlerContext(actor=principal, run_id=run_id, thread_id=repository.run.thread_id,
        tool_name='change_records', call_id='same-tool-call', args=_records(),
        trusted_args={'timezone': 'Asia/Shanghai'}, request_id='request-1')
    first = asyncio.run(ChangeHandler(service, 'records.batch.change')(context)).canonical_output
    second = asyncio.run(ChangeHandler(service, 'records.batch.change')(context)).canonical_output
    assert first == second and first['ok'] is True and first['action_status'] == 'applied'
    assert len(client.calls) == 1 and repository.event_types == ['action.proposed', 'action.applied', 'product.tabs.updated']
    assert repository.action is not None and client.calls[0][1] == str(repository.action.id)
    assert repository.committed_action_id == repository.action.id


def test_real_action_service_exposes_rejected_batch_without_claiming_success() -> None:
    from app.agent_runtime.actions import ActionExecutor, ActionPolicy, RuntimeActionService
    from app.auth import RuntimePrincipal
    from app.core.errors import DependencyError
    from app.schedule_records import SCHEDULE_RECORDS_CAPABILITY
    from test_runtime_actions import FakeActionRepository

    owner, run_id = uuid4(), uuid4()
    repository = FakeActionRepository(owner_id=owner, run_id=run_id)

    class Client:
        calls = 0
        async def write_agent_schedule(self, *, command: Any, idempotency_key: str, request_id: str) -> None:
            self.calls += 1
            raise DependencyError(code='version_conflict', message='Changed.', status=409, retryable=False)

    client = Client()
    service = RuntimeActionService(repository=cast(RuntimeLedgerRepository, repository), executor=ActionExecutor(
        repository=cast(RuntimeLedgerRepository, repository),
        applicators={'schedule.batch.change': BatchApplicator(client, 'schedule')},
        policy=ActionPolicy(rules=SCHEDULE_RECORDS_CAPABILITY.action_policy_rules)))
    context = ToolHandlerContext(actor=RuntimePrincipal.from_authorization_context(repository.run.authorization_context),
        run_id=run_id, thread_id=repository.run.thread_id, tool_name='change_schedule',
        call_id='schedule-call', args=_schedule(), request_id='request-1')
    first = asyncio.run(ChangeHandler(service, 'schedule.batch.change')(context)).canonical_output
    second = asyncio.run(ChangeHandler(service, 'schedule.batch.change')(context)).canonical_output
    assert first == second and first['ok'] is False and first['error_code'] == 'version_conflict'
    assert first['batch_id'] is None and client.calls == 1
    assert repository.event_types == ['action.proposed', 'action.failed']


def test_failed_batch_preserves_only_safe_backend_issue_and_replays_it() -> None:
    from app.agent_runtime.actions import ActionExecutor, ActionPolicy, RuntimeActionService
    from app.auth import RuntimePrincipal
    from app.core.errors import DependencyError
    from app.schedule_records import SCHEDULE_RECORDS_CAPABILITY
    from test_runtime_actions import FakeActionRepository

    owner, run_id = uuid4(), uuid4()
    repository = FakeActionRepository(owner_id=owner, run_id=run_id)

    class Client:
        calls = 0
        async def write_agent_records(self, **_kwargs: Any) -> Any:
            self.calls += 1
            raise DependencyError(code='validation_failed', message='Do not display this backend text',
                status=422, retryable=False, issue={
                    'operation_index': 0, 'field_path': 'fields.side', 'reason': 'required',
                })

    client = Client()
    service = RuntimeActionService(repository=cast(RuntimeLedgerRepository, repository), executor=ActionExecutor(
        repository=cast(RuntimeLedgerRepository, repository),
        applicators={'records.batch.change': BatchApplicator(client, 'records')},
        policy=ActionPolicy(rules=SCHEDULE_RECORDS_CAPABILITY.action_policy_rules)))
    context = ToolHandlerContext(actor=RuntimePrincipal.from_authorization_context(repository.run.authorization_context),
        run_id=run_id, thread_id=repository.run.thread_id, tool_name='change_records',
        call_id='failed-batch', args=_records(), trusted_args={'timezone': 'Asia/Shanghai'}, request_id='request-1')
    first = asyncio.run(ChangeHandler(service, 'records.batch.change')(context)).canonical_output
    second = asyncio.run(ChangeHandler(service, 'records.batch.change')(context)).canonical_output
    assert first == second
    assert first['ok'] is False and first['action_status'] == 'failed'
    assert first['failure'] == {'operation_index': 0, 'field_path': 'fields.side', 'reason': 'required'}
    assert 'Do not display' not in str(first)
    assert client.calls == 1


def test_client_forwards_only_allowlisted_batch_error_fields() -> None:
    import httpx
    from app.core.errors import DependencyError
    from app.infrastructure.product_backend import ProductBackendClient
    from app.infrastructure.product_backend.contracts import AgentRecordBatchRequest

    owner = uuid4()
    async def failed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={'error': {'code': 'validation_failed', 'message': 'Private note',
            'details': {'operation_index': 0, 'field_path': 'fields.side', 'reason': 'required',
                'secret': 'must never reach the model'}}})

    async def run() -> None:
        async with httpx.AsyncClient(base_url='https://product.test', transport=httpx.MockTransport(failed)) as http:
            await ProductBackendClient(http_client=http, service_key='service').write_agent_records(
                command=AgentRecordBatchRequest(actor_user_id=owner, timezone='UTC',
                    operations=_records()['operations']), idempotency_key=str(uuid4()), request_id='test')

    with pytest.raises(DependencyError) as error:
        asyncio.run(run())
    assert error.value.details['issue'] == {'operation_index': 0, 'field_path': 'fields.side', 'reason': 'required'}
    assert 'secret' not in str(error.value.details)
