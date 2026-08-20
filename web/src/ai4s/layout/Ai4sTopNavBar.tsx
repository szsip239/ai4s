import { Link, useRouterState } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import {
  IconDashboard,
  IconKey,
  IconRoute,
  IconShield,
  IconFileText,
  IconFolders,
  IconUsersGroup,
  IconDatabase,
  IconSettings,
} from '@tabler/icons-react';
import { cn } from '@/lib/utils';
import { usePermissions } from '@/hooks/usePermissions';
import { useRoutePermissions } from '@/hooks/useRoutePermissions';

/**
 * ai4s 顶部导航（C 结构）
 * 全部条目内联展示：图标 + 中文标签；激活项 = accent-soft 底 + accent 字（与 C×W token 联动）。
 * 新增代码按 vendor 隔离规则只落 src/ai4s/。
 * issue #54 归并：渠道管理+模型 →「接入管理」（页内 Tab 切换），用户+角色 →「用户与角色」；
 * 观测域的用量统计/追踪/线程、成员域的用户/角色同样经各页 Ai4sPageTabs 可达。
 * issue #55 收尾：「审计日志」统一为「观测」（与 ⌘K 域名一致）；补「成员」入口
 * （/project/users、/project/roles 直达时顶栏有激活项）。
 * issue #65：成员（项目域）+ 用户与角色（全局域）合并为单一「人员」入口，
 * 四个人员域页面经统一 people Tab 组互跳；顶栏 10 项 → 9 项。
 * issue #65 评审 P2：人员入口按权限回落——有系统级 read_users 落全局 /users，
 * 否则落项目域 /project/users（route-permission：/users 属 system 组，/project/users 属 any 组）。
 * issue #69 P3：入口按 routeConfigs mode:'hidden' 语义做权限过滤（与侧栏/⌘K 的 filterNavItems 一致），
 * 无权限且 mode=hidden 的入口不再渲染（此前员工可见 9 个入口、其中 7 个点进去 Access Denied）。
 * issue #70：label 走 i18n（与 ⌘K 同源复用 sidebar.items.*；zh 文案与 ⌘K 不同的四项——
 * 仪表盘/我的 Key/脱敏规则/系统设置——用 ai4s.topnav.* 键，zh 保持顶栏既有文案不回退）。
 */

const NAV_ITEMS = [
  { labelKey: 'ai4s.topnav.dashboard', href: '/', match: ['/'], icon: IconDashboard },
  { labelKey: 'ai4s.topnav.myKeys', href: '/project/api-keys', match: ['/project/api-keys'], icon: IconKey },
  { labelKey: 'sidebar.items.accessManagement', href: '/channels', match: ['/channels', '/models'], icon: IconRoute },
  { labelKey: 'ai4s.topnav.promptProtectionRules', href: '/prompt-protection-rules', match: ['/prompt-protection-rules'], icon: IconShield },
  { labelKey: 'sidebar.items.observability', href: '/project/requests', match: ['/project/requests', '/project/usage-stats', '/project/traces', '/project/threads'], icon: IconFileText },
  { labelKey: 'sidebar.items.projects', href: '/projects', match: ['/projects'], icon: IconFolders },
  { labelKey: 'sidebar.items.people', href: '/users', match: ['/users', '/roles', '/project/users', '/project/roles'], icon: IconUsersGroup },
  { labelKey: 'sidebar.items.dataStorages', href: '/data-storages', match: ['/data-storages'], icon: IconDatabase },
  { labelKey: 'ai4s.topnav.system', href: '/system', match: ['/system'], icon: IconSettings },
] as const;

export function Ai4sTopNavBar() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { t } = useTranslation();
  const { hasSystemScope } = usePermissions();
  const { checkRouteAccess } = useRoutePermissions();
  const peopleHref = hasSystemScope('read_users') ? '/users' : '/project/users';
  // NAV_ITEMS 中仅「人员」href 为 /users，按权限解析实际落点
  // issue #69 P3：按 routeConfigs mode:'hidden' 语义过滤无权限入口（判定落在解析后的实际 href 上，
  // scopeLevel 由路由所属组决定——/users 走系统级、/project/users 走 any 级，与 RouteGuard 一致）
  const navItems = NAV_ITEMS.map((item) => (item.href === '/users' ? { ...item, href: peopleHref } : item)).filter(
    (item) => {
      const access = checkRouteAccess(item.href);
      return access.hasAccess || access.mode !== 'hidden';
    }
  );

  return (
    <div className='bg-background/95 supports-[backdrop-filter]:bg-background/60 fixed top-14 z-40 w-full border-b backdrop-blur'>
      <nav className='flex h-11 items-center gap-1 overflow-x-auto px-4'>
        {navItems.map(({ labelKey, href, match, icon: Icon }) => {
          const active =
            href === '/'
              ? pathname === '/'
              : match.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
          return (
            <Link
              key={href}
              to={href}
              className={cn(
                'flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm whitespace-nowrap transition-colors',
                active ? 'bg-accent font-medium text-accent-foreground' : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'
              )}
            >
              <Icon className='size-4' />
              {t(labelKey)}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
