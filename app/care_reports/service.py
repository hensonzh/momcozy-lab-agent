from app.agent_runtime.audit.service import AuditService
from .schemas import ReportSourceQuery, ReportSourcesRead
from .sources import ReportSourcesRepository


class ReportSourceService:
    def __init__(self, repository: ReportSourcesRepository, audit: AuditService) -> None:
        self.repository, self.audit = repository, audit

    async def read(self, query: ReportSourceQuery, request_id: str) -> ReportSourcesRead:
        result = await self.repository.read(query)
        await self.audit.record(actor_user_id=None, actor_type='service', actor_service='product-care-reports',
            action='care_reports.sources_read', resource_type='care_report_sources', resource_id=str(query.owner_user_id),
            request_id=request_id, details={'included_count': len(result.items), 'omitted_count': result.omitted_count})
        return result
