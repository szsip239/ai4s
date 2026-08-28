import { createFileRoute } from '@tanstack/react-router';
import { RouteGuard } from '@/components/route-guard';
import Ai4sSmartRoutingPage from '@/ai4s/pages/smart-routing/Ai4sSmartRoutingPage';

// 智能路由管理页（issue #120）：shim settings routing 节配置 + router 层决策日志观测，读级与 shim 对齐
function ProtectedSmartRouting() {
  return (
    <RouteGuard requiredScopes={['read_channels']} scopeLevel="system">
      <Ai4sSmartRoutingPage />
    </RouteGuard>
  );
}

export const Route = createFileRoute('/_authenticated/smart-routing/')({
  component: ProtectedSmartRouting,
});
