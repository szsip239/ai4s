import { createFileRoute } from '@tanstack/react-router';
import { RouteGuard } from '@/components/route-guard';
import Ai4sKeyRequestsPage from '@/ai4s/pages/key-requests/Ai4sKeyRequestsPage';

// 管理员「Key 审批」（issue #79）：与词表/规则页同门槛（read_channels，system 组）；
// 员工侧发起入口在 /project/my-keys（无 scope 门槛），点批是管理行为。
function ProtectedKeyRequests() {
  return (
    <RouteGuard requiredScopes={['read_channels']} scopeLevel="system">
      <Ai4sKeyRequestsPage />
    </RouteGuard>
  );
}

export const Route = createFileRoute('/_authenticated/key-requests/')({
  component: ProtectedKeyRequests,
});
