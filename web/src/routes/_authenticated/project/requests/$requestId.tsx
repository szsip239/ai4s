import { createFileRoute } from '@tanstack/react-router';
import { ProjectGuard } from '@/components/project-guard';
import { RouteGuard } from '@/components/route-guard';
import RequestDetailPage from '@/features/requests/components/request-detail-page';

// 恢复上游原生请求详情页（M8 redirect 撤销，配套审计全量恢复）
function ProtectedRequestDetail() {
  return (
    <ProjectGuard>
      <RouteGuard requiredScopes={['read_requests']} scopeLevel="any">
        <RequestDetailPage />
      </RouteGuard>
    </ProjectGuard>
  );
}

export const Route = createFileRoute('/_authenticated/project/requests/$requestId')({
  component: ProtectedRequestDetail,
});
