from __future__ import annotations

from hmac import compare_digest

from fastapi import APIRouter, Depends, Request, Response, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.agent_runtime.audit.repository import RuntimeAuditRepository
from app.agent_runtime.audit.service import AuditService
from app.infrastructure.db import get_session
from .schemas import ReportSourceQuery, ReportSourcesRead
from .sources import ReportSourcesRepository
from .service import ReportSourceService
from .composition import get_report_generator
from .generation import CareReportGenerator
from .generation_schemas import ReportGenerationInput, ReportGenerationResult

report_key = APIKeyHeader(name='X-Service-Key', scheme_name='ProductReportServiceKey', auto_error=False)


async def require_product_report_service(request: Request, key: str | None = Security(report_key)) -> None:
    configured = request.app.state.settings.care_report_service_key
    if not configured:
        raise ApiError(code='care_reports_unavailable', message='Care report service is not configured.', status=503)
    if key is None or not compare_digest(key, configured):
        raise ApiError(code='authentication_required', message='Product report service authentication is required.', status=401)


router = APIRouter(prefix='/internal/care-reports', tags=['internal-care-reports'], dependencies=[Depends(require_product_report_service)])


def get_report_source_service(session: AsyncSession = Depends(get_session)) -> ReportSourceService:
    return ReportSourceService(ReportSourcesRepository(session), AuditService(repository=RuntimeAuditRepository(session)))


def get_report_audit_service(session: AsyncSession = Depends(get_session)) -> AuditService:
    return AuditService(repository=RuntimeAuditRepository(session))


@router.post('/sources', response_model=ReportSourcesRead)
async def sources(body: ReportSourceQuery, request: Request, response: Response, service: ReportSourceService = Depends(get_report_source_service)) -> ReportSourcesRead:
    response.headers['Cache-Control'] = 'private, no-store'
    return await service.read(body, str(getattr(request.state, 'request_id', '')))


@router.post('/generate', response_model=ReportGenerationResult)
async def generate(body: ReportGenerationInput, request: Request, response: Response,
    generator: CareReportGenerator = Depends(get_report_generator), audit: AuditService = Depends(get_report_audit_service)) -> ReportGenerationResult:
    result = await generator.generate(body)
    await audit.record(actor_user_id=None, actor_type='service',
        actor_service='product-care-reports', action='care_reports.generated', resource_type='care_episode',
        resource_id=str(body.episode_id), request_id=str(getattr(request.state, 'request_id', '')),
        details={'input_hash': result.input_hash, 'source_count': len(body.sources), 'model': result.model,
            'prompt_version': result.prompt_version, 'schema_version': result.schema_version})
    response.headers['Cache-Control'] = 'private, no-store'
    return result
