import { Link, useRouterState } from '@tanstack/react-router';
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

/**
 * ai4s 顶部导航（C 结构）
 * 全部条目内联展示：图标 + 中文标签；激活项 = accent-soft 底 + accent 字（与 C×W token 联动）。
 * 新增代码按 vendor 隔离规则只落 src/ai4s/。
 * issue #54 归并：渠道管理+模型 →「接入管理」（页内 Tab 切换），用户+角色 →「用户与角色」；
 * 观测域的用量统计/追踪/线程、成员域的用户/角色同样经各页 Ai4sPageTabs 可达。
 * issue #55 收尾：「审计日志」统一为「观测」（与 ⌘K 域名一致）；补「成员」入口
 * （/project/users、/project/roles 直达时顶栏有激活项）。
 * issue #65：成员（项目域）+ 用户与角色（全局域）合并为单一「人员」入口（默认落全局用户页），
 * 四个人员域页面经统一 people Tab 组互跳；顶栏 10 项 → 9 项。
 */

const NAV_ITEMS = [
  { title: '仪表盘', href: '/', match: ['/'], icon: IconDashboard },
  { title: '我的 Key', href: '/project/api-keys', match: ['/project/api-keys'], icon: IconKey },
  { title: '接入管理', href: '/channels', match: ['/channels', '/models'], icon: IconRoute },
  { title: '脱敏规则', href: '/prompt-protection-rules', match: ['/prompt-protection-rules'], icon: IconShield },
  { title: '观测', href: '/project/requests', match: ['/project/requests', '/project/usage-stats', '/project/traces', '/project/threads'], icon: IconFileText },
  { title: '项目', href: '/projects', match: ['/projects'], icon: IconFolders },
  { title: '人员', href: '/users', match: ['/users', '/roles', '/project/users', '/project/roles'], icon: IconUsersGroup },
  { title: '数据存储', href: '/data-storages', match: ['/data-storages'], icon: IconDatabase },
  { title: '系统设置', href: '/system', match: ['/system'], icon: IconSettings },
] as const;

export function Ai4sTopNavBar() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  return (
    <div className='bg-background/95 supports-[backdrop-filter]:bg-background/60 fixed top-14 z-40 w-full border-b backdrop-blur'>
      <nav className='flex h-11 items-center gap-1 overflow-x-auto px-4'>
        {NAV_ITEMS.map(({ title, href, match, icon: Icon }) => {
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
              {title}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
