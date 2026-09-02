import { createFileRoute } from '@tanstack/react-router';
import { RouteGuard } from '@/components/route-guard';
import BlocksPage from '@/features/blocks';

// 拦截审计页（issue #132）：shim block 层内容阻断记录，数据源为 DLP 管理面，system 级鉴权同智能路由页
function ProtectedProjectBlocks() {
  return (
    <RouteGuard requiredScopes={['read_channels']} scopeLevel="system">
      <BlocksPage />
    </RouteGuard>
  );
}

export const Route = createFileRoute('/_authenticated/project/blocks/')({
  component: ProtectedProjectBlocks,
});
