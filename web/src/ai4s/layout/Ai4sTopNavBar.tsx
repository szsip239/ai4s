import { Link, useRouterState } from '@tanstack/react-router';
import {
  IconDashboard,
  IconKey,
  IconRoute,
  IconShield,
  IconFileText,
  IconBoxModel,
  IconFolders,
  IconUsers,
  IconUsersGroup,
  IconDatabase,
  IconSettings,
} from '@tabler/icons-react';
import { cn } from '@/lib/utils';

/**
 * ai4s 顶部导航（C 结构，全量展示版）
 * 全部条目内联展示：图标 + 中文标签；激活项 = accent-soft 底 + accent 字（与 C×W token 联动）。
 * 新增代码按 vendor 隔离规则只落 src/ai4s/。
 */

const NAV_ITEMS = [
  { title: '仪表盘', href: '/', icon: IconDashboard },
  { title: '我的 Key', href: '/project/api-keys', icon: IconKey },
  { title: '渠道管理', href: '/channels', icon: IconRoute },
  { title: '脱敏规则', href: '/prompt-protection-rules', icon: IconShield },
  { title: '审计日志', href: '/project/requests', icon: IconFileText },
  { title: '模型', href: '/models', icon: IconBoxModel },
  { title: '项目', href: '/projects', icon: IconFolders },
  { title: '用户', href: '/users', icon: IconUsers },
  { title: '角色', href: '/roles', icon: IconUsersGroup },
  { title: '数据存储', href: '/data-storages', icon: IconDatabase },
  { title: '系统设置', href: '/system', icon: IconSettings },
] as const;

export function Ai4sTopNavBar() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  return (
    <div className='bg-background/95 supports-[backdrop-filter]:bg-background/60 fixed top-14 z-40 w-full border-b backdrop-blur'>
      <nav className='flex h-11 items-center gap-1 overflow-x-auto px-4'>
        {NAV_ITEMS.map(({ title, href, icon: Icon }) => {
          const active = href === '/' ? pathname === '/' : pathname.startsWith(href);
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
