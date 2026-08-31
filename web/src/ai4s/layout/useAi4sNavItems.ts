import { usePermissions } from '@/hooks/usePermissions';
import { useRoutePermissions } from '@/hooks/useRoutePermissions';
import {
  IconDashboard,
  IconId,
  IconKey,
  IconRoute,
  IconShield,
  IconFileText,
  IconFolders,
  IconUsersGroup,
  IconDatabase,
  IconSettings,
  IconClipboardCheck,
  IconArrowsShuffle,
} from '@tabler/icons-react';

/**
 * ai4s 顶栏导航项定义与权限过滤（供桌面顶栏 Ai4sTopNavBar 与移动端抽屉 Ai4sMobileNav 共用）。
 * 各项语义与沿革注释见 Ai4sTopNavBar.tsx 头注（issue #54/#55/#65/#69/#70/#74/#79/#113/#120）。
 */

export const AI4S_NAV_ITEMS = [
  { labelKey: 'ai4s.topnav.dashboard', href: '/', match: ['/'], icon: IconDashboard },
  { labelKey: 'ai4s.topnav.myKeys', href: '/project/api-keys', match: ['/project/api-keys'], icon: IconKey },
  { labelKey: 'sidebar.items.myKeys', href: '/project/my-keys', match: ['/project/my-keys'], icon: IconId },
  { labelKey: 'sidebar.items.accessManagement', href: '/channels', match: ['/channels', '/models', '/project/playground'], icon: IconRoute },
  { labelKey: 'ai4s.topnav.promptProtectionRules', href: '/prompt-protection-rules', match: ['/prompt-protection-rules'], icon: IconShield },
  { labelKey: 'sidebar.items.keyRequests', href: '/key-requests', match: ['/key-requests'], icon: IconClipboardCheck },
  { labelKey: 'sidebar.items.smartRouting', href: '/smart-routing', match: ['/smart-routing'], icon: IconArrowsShuffle },
  { labelKey: 'sidebar.items.observability', href: '/project/requests', match: ['/project/requests', '/project/usage-stats', '/project/traces', '/project/threads'], icon: IconFileText },
  { labelKey: 'sidebar.items.projects', href: '/projects', match: ['/projects'], icon: IconFolders },
  { labelKey: 'sidebar.items.people', href: '/users', match: ['/users', '/roles', '/project/users', '/project/roles'], icon: IconUsersGroup },
  { labelKey: 'sidebar.items.dataStorages', href: '/data-storages', match: ['/data-storages'], icon: IconDatabase },
  { labelKey: 'ai4s.topnav.system', href: '/system', match: ['/system'], icon: IconSettings },
] as const;

export function useAi4sNavItems() {
  const { hasSystemScope } = usePermissions();
  const { checkRouteAccess } = useRoutePermissions();
  const peopleHref = hasSystemScope('read_users') ? '/users' : '/project/users';
  // NAV_ITEMS 中仅「人员」href 为 /users，按权限解析实际落点
  // issue #69 P3：按 routeConfigs mode:'hidden' 语义过滤无权限入口（判定落在解析后的实际 href 上，
  // scopeLevel 由路由所属组决定——/users 走系统级、/project/users 走 any 级，与 RouteGuard 一致）
  return AI4S_NAV_ITEMS.map((item) => (item.href === '/users' ? { ...item, href: peopleHref } : item)).filter((item) => {
    const access = checkRouteAccess(item.href);
    return access.hasAccess || access.mode !== 'hidden';
  });
}

export function isAi4sNavActive(item: { href: string; match: readonly string[] }, pathname: string): boolean {
  return item.href === '/' ? pathname === '/' : item.match.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}
