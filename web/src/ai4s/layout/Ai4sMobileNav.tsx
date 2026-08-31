import { useState } from 'react';
import { Link, useRouterState } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { IconMenu2 } from '@tabler/icons-react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useAi4sNavItems, isAi4sNavActive } from './useAi4sNavItems';

/**
 * ai4s 移动端导航抽屉（PWA/手机端适配）：
 * 汉堡按钮挂在 AppHeader 左侧（仅 <md 渲染），点击左侧滑出全量导航列表。
 * 导航项与桌面顶栏同源（useAi4sNavItems），权限过滤语义一致；
 * 触控目标 h-11（44px，符合移动端可点区域规范），点击后自动收起。
 */
export function Ai4sMobileNav() {
  const [open, setOpen] = useState(false);
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { t } = useTranslation();
  const navItems = useAi4sNavItems();

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant='ghost' size='icon' className='size-9 md:hidden' aria-label={t('ai4s.topnav.openMenu', '打开导航菜单')}>
          <IconMenu2 className='size-5' />
        </Button>
      </SheetTrigger>
      <SheetContent side='left' className='w-72 p-0'>
        <SheetHeader className='border-b px-4 py-3'>
          <SheetTitle className='text-left text-sm'>{t('ai4s.topnav.menuTitle', '导航')}</SheetTitle>
        </SheetHeader>
        <nav className='flex flex-col gap-0.5 overflow-y-auto p-2'>
          {navItems.map(({ labelKey, href, match, icon: Icon }) => {
            const active = isAi4sNavActive({ href, match }, pathname);
            return (
              <Link
                key={href}
                to={href}
                onClick={() => setOpen(false)}
                className={cn(
                  'flex h-11 items-center gap-3 rounded-md px-3 text-sm transition-colors',
                  active ? 'bg-accent font-medium text-accent-foreground' : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'
                )}
              >
                <Icon className='size-5 shrink-0' />
                {t(labelKey)}
              </Link>
            );
          })}
        </nav>
      </SheetContent>
    </Sheet>
  );
}
