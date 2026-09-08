import { createFileRoute } from '@tanstack/react-router';
import { RouteGuard } from '@/components/route-guard';
import RequestDetailGlobalPage from '@/features/requests/components/request-detail-global-page';

// issue #139：全局变体补 RouteGuard（此前裸挂载，无 read_requests 也可直访），
// 对齐 project 变体的 read_requests guard（scopeLevel=any；全局页无项目上下文，不套 ProjectGuard）
function ProtectedRequestDetailGlobal() {
  return (
    <RouteGuard requiredScopes={['read_requests']} scopeLevel="any">
      <RequestDetailGlobalPage />
    </RouteGuard>
  );
}

export const Route = createFileRoute('/_authenticated/requests/$requestId')({
  component: ProtectedRequestDetailGlobal,
});
