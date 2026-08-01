import { createFileRoute } from '@tanstack/react-router';
import { ProjectGuard } from '@/components/project-guard';
import { RouteGuard } from '@/components/route-guard';
import Ai4sRequestsPage from '@/ai4s/pages/requests/Ai4sRequestsPage';

// 挂载点 M7：审计日志换 ai4s 元数据版（不展示请求/响应原文，issue #13）
function ProtectedProjectRequests() {
  return (
    <ProjectGuard>
      <RouteGuard requiredScopes={['read_requests']} scopeLevel="any">
        <Ai4sRequestsPage />
      </RouteGuard>
    </ProjectGuard>
  );
}

export const Route = createFileRoute('/_authenticated/project/requests/')({
  validateSearch: (search: Record<string, unknown>) => search,
  component: ProtectedProjectRequests,
});
