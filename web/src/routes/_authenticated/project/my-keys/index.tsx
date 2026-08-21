import { createFileRoute } from '@tanstack/react-router';
import Ai4sMyKeysPage from '@/ai4s/pages/my-keys/Ai4sMyKeysPage';

// 员工「我的 Key」（issue #74）：所有登录用户可见（无 requiredScopes），
// 导航/路由可达性由 routeConfigs 同名条目保证（/project/my-keys，无 scope 门槛）。
export const Route = createFileRoute('/_authenticated/project/my-keys/')({
  component: Ai4sMyKeysPage,
});
