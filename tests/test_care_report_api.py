from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.care_reports.router import get_report_source_service
from app.care_reports.schemas import ReportSourceQuery, ReportSourcesRead
from app.core.settings import Settings
from app.factory import create_app


class FakeSourceService:
    calls = 0

    async def read(self, query: ReportSourceQuery, request_id: str) -> ReportSourcesRead:
        self.calls += 1
        return ReportSourcesRead(items=[], available_count=0, omitted_count=0, as_of=query.as_of)


def test_report_sources_require_the_dedicated_product_credential() -> None:
    key = 'isolated-care-report-credential-over-32-bytes'
    app = create_app(Settings(app_env='test', care_report_service_key=key))
    fake = FakeSourceService()
    app.dependency_overrides[get_report_source_service] = lambda: fake
    body = ReportSourceQuery(owner_user_id=uuid4(), threads=[], starts_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        ends_at=datetime(2026, 9, 9, tzinfo=timezone.utc), as_of=datetime(2026, 9, 8, 12, tzinfo=timezone.utc)).model_dump(mode='json')
    with TestClient(app) as client:
        for headers in [{}, {'Authorization': 'Bearer user-token'}, {'X-Service-Key': 'unrelated-key'}]:
            assert client.post('/v1/internal/care-reports/sources', json=body, headers=headers).status_code == 401
        assert fake.calls == 0
        response = client.post('/v1/internal/care-reports/sources', json=body, headers={'X-Service-Key': key})
        assert response.status_code == 200 and response.headers['cache-control'] == 'private, no-store'
        assert response.json()['items'] == [] and fake.calls == 1


def test_unconfigured_report_service_is_unavailable_and_secret_is_not_in_settings_repr() -> None:
    key = 'another-isolated-care-report-credential-32-bytes'
    assert key not in repr(Settings(app_env='test', care_report_service_key=key))
    app = create_app(Settings(app_env='test'))
    fake = FakeSourceService()
    app.dependency_overrides[get_report_source_service] = lambda: fake
    with TestClient(app) as client:
        response = client.post('/v1/internal/care-reports/sources', json={}, headers={'X-Service-Key': key})
        assert response.status_code == 503
        assert fake.calls == 0


def test_report_service_key_is_loaded_from_environment(monkeypatch: MonkeyPatch) -> None:
    key = 'environment-care-report-credential-over-32-bytes'
    monkeypatch.setenv('CARE_REPORT_SERVICE_KEY', key)
    assert Settings.from_env().care_report_service_key == key


def test_generation_is_private_and_audits_only_source_metadata() -> None:
    from typing import Any
    from app.care_reports.composition import get_report_generator
    from app.care_reports.router import get_report_audit_service
    from test_care_report_generation import FakeResponses, generator, report_input

    class FakeAudit:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
        async def record(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)
    key = 'isolated-generation-credential-over-32-bytes'
    app = create_app(Settings(app_env='test', care_report_service_key=key))
    responses = FakeResponses()
    service, _ = generator(responses)
    audit = FakeAudit()
    app.dependency_overrides[get_report_generator] = lambda: service
    app.dependency_overrides[get_report_audit_service] = lambda: audit
    body = report_input().model_dump(mode='json')
    # A valid report snapshot can exceed the public chat API's 64 KiB budget.
    body['sources'].extend({**body['sources'][0], 'id': f'lactation:long:{index}', 'content': 'x' * 14000} for index in range(5))
    with TestClient(app) as client:
        for headers in [{}, {'Authorization': 'Bearer ordinary-user'}, {'X-Service-Key': 'wrong'}]:
            assert client.post('/v1/internal/care-reports/generate', json=body, headers=headers).status_code == 401
        assert not responses.calls and not audit.calls
        response = client.post('/v1/internal/care-reports/generate', json=body, headers={'X-Service-Key': key})
        assert response.status_code == 200 and response.headers['cache-control'] == 'private, no-store'
        assert len(responses.calls) == 1 and len(audit.calls) == 1
        assert audit.calls[0]['action'] == 'care_reports.generated'
        assert body['sources'][0]['content'] not in str(audit.calls)


def test_report_endpoints_bound_the_raw_request_before_auth_or_json_parsing() -> None:
    app = create_app(Settings(app_env='test'))
    with TestClient(app) as client:
        for path, limit in [('generate', 128 * 1024), ('sources', 48 * 1024)]:
            response = client.post(f'/v1/internal/care-reports/{path}', content=b'x' * (limit + 1), headers={'Content-Type': 'application/json'})
            assert response.status_code == 413
            assert response.json()['error']['details']['max_body_bytes'] == limit
