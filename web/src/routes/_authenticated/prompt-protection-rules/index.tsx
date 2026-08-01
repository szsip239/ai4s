import { createFileRoute } from '@tanstack/react-router';
import { RouteGuard } from '@/components/route-guard';
import Ai4sRulesPage from '@/ai4s/pages/rules/Ai4sRulesPage';

// 挂载点 M9：脱敏规则换 ai4s link-ai 式规则表（类型/优先级展示层派生，issue #13）
function ProtectedPromptProtectionRules() {
  return (
    <RouteGuard requiredScopes={['read_channels']} scopeLevel="system">
      <Ai4sRulesPage />
    </RouteGuard>
  );
}

export const Route = createFileRoute('/_authenticated/prompt-protection-rules/')({
  component: ProtectedPromptProtectionRules,
});
