import { Link, useRouterState } from '@tanstack/react-router';
import { IconDots } from '@tabler/icons-react';
import { TopNav } from '@/components/layout/top-nav';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';

/**
 * ai4s 顶部导航（C 结构，issue #11）
 * 五个主入口固定；其余管理页收进「更多」。与 AppHeader（h-14）组合为两行固定头部。
 * 新增代码按 vendor 隔离规则只落 src/ai4s/。
 */

const MAIN_LINKS = [
  { title: '仪表盘', href: '/' },
  { title: '我的 Key', href: '/project/api-keys' },
  { title: '渠道管理', href: '/channels' },
  { title: '脱敏规则', href: '/prompt-protection-rules' },
  { title: '审计日志', href: '/project/requests' },
] as const;

const MORE_LINKS = [
  { title: '模型', href: '/models' },
  { title: '项目', href: '/projects' },
  { title: '用户', href: '/users' },
  { title: '角色', href: '/roles' },
  { title: '数据存储', href: '/data-storages' },
  { title: '系统设置', href: '/system' },
] as const;

export function Ai4sTopNavBar() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const links = MAIN_LINKS.map((l) => ({
    ...l,
    isActive: l.href === '/' ? pathname === '/' : pathname.startsWith(l.href),
  }));

  return (
    <div className='bg-background/95 supports-[backdrop-filter]:bg-background/60 fixed top-14 z-40 w-full border-b backdrop-blur'>
      <div className='flex h-11 items-center justify-between px-6'>
        <TopNav links={links} />
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <Button variant='ghost' size='sm' className='gap-1'>
              <IconDots className='size-4' />
              更多
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side='bottom' align='end'>
            {MORE_LINKS.map((l) => (
              <DropdownMenuItem key={l.href} asChild>
                <Link to={l.href}>{l.title}</Link>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
