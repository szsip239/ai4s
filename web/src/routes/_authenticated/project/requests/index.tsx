import { createFileRoute } from '@tanstack/react-router';
import { ProjectGuard } from '@/components/project-guard';
import { RouteGuard } from '@/components/route-guard';
import RequestsManagement from '@/features/requests';

// 恢复上游原生审计日志（全量详情；M7 元数据化撤销——2026-08-02 用户拍板：审计日志不应该拿走）
function ProtectedProjectRequests() {
  return (
    <ProjectGuard>
      <RouteGuard requiredScopes={['read_requests']} scopeLevel="any">
        <RequestsManagement />
      </RouteGuard>
    </ProjectGuard>
  );
}

export const Route = createFileRoute('/_authenticated/project/requests/')({
  validateSearch: (search: Record<string, unknown>) => search,
  component: ProtectedProjectRequests,
});
