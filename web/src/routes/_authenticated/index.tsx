import { createFileRoute } from '@tanstack/react-router';
import { RouteGuard } from '@/components/route-guard';
import Dashboard from '@/features/dashboard';

// 恢复上游原生 dashboard（信息全量；M4 换皮撤销，C×W 主题经 token 层仍然生效）
function ProtectedDashboard() {
  return (
    <RouteGuard requiredScopes={['read_dashboard']} scopeLevel="system">
      <Dashboard />
    </RouteGuard>
  );
}

export const Route = createFileRoute('/_authenticated/')({
  component: ProtectedDashboard,
});
