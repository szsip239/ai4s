import { createFileRoute } from '@tanstack/react-router';
import { RouteGuard } from '@/components/route-guard';
import Ai4sDashboard from '@/ai4s/pages/dashboard/Ai4sDashboard';

// 挂载点 M4：dashboard 换成 ai4s C 结构看板（原上游 Dashboard 见 features/dashboard）
function ProtectedDashboard() {
  return (
    <RouteGuard requiredScopes={['read_dashboard']} scopeLevel="system">
      <Ai4sDashboard />
    </RouteGuard>
  );
}

export const Route = createFileRoute('/_authenticated/')({
  component: ProtectedDashboard,
});
