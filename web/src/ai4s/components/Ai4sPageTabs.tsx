import { Link, useLocation } from '@tanstack/react-router';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useRoutePermissions } from '@/hooks/useRoutePermissions';
import { type NavLink } from '@/components/layout/types';
import { type PageTab } from './page-tab-groups';

/**
 * 页内 Tab 栏（issue #54 侧栏归并）：被合并路由保持可达，Tab 栏只是页面级导航——
 * 按当前路由高亮（含子路径前缀匹配），Link 导航；可见性走 useRoutePermissions 同款
 * 过滤（hidden 模式无权限不显示，disabled 模式置灰）。仅剩 ≤1 个可见 Tab 时不渲染。
 * 用法：页面 `<Main>` 内第一个子元素 `<Ai4sPageTabs tabs={pageTabGroups.access(t)} />`。
 */
export function Ai4sPageTabs({ tabs }: { tabs: PageTab[] }) {
  const { filterNavItems } = useRoutePermissions();
  const pathname = useLocation({ select: (location) => location.pathname });
  const visible = filterNavItems(tabs.map((tab) => ({ title: tab.label, url: tab.url }) as NavLink));

  if (visible.length <= 1) {
    return null;
  }

  const activeUrl =
    (visible.find((tab) => {
      const url = tab.url as string;
      return pathname === url || pathname.startsWith(`${url}/`);
    })?.url as string) ?? (visible[0].url as string);

  return (
    <Tabs value={activeUrl} className='mb-4 flex-shrink-0'>
      <TabsList>
        {visible.map((tab) => (
          <TabsTrigger key={tab.url as string} value={tab.url as string} asChild disabled={tab.isDisabled}>
            <Link to={tab.url}>{tab.title}</Link>
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
