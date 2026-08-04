import { Link } from '@tanstack/react-router';
import { BadgeCheck, ChevronsUpDown, LogOut } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useSignOut } from '@/features/auth/data/auth';
import { useAuthStore } from '@/stores/authStore';

/**
 * Ai4sUserMenu —— 顶部导航用户菜单（账户信息 + 个人资料 + 退出登录）。
 * 背景：C 结构（issue #11）移除侧边栏后，上游 NavUser（挂在 AppSidebar）失挂载，
 * 用户无退出入口（2026-08-03 用户反馈）。本组件为顶栏适配版，逻辑对齐上游 nav-user.tsx。
 */
export function Ai4sUserMenu() {
  const { t } = useTranslation();
  const user = useAuthStore((state) => state.auth.user);
  const signOut = useSignOut();

  if (!user) return null;

  const name = [user.firstName, user.lastName].filter(Boolean).join(' ') || user.email;
  const avatar = user.avatar || '';
  const isAvatarUrl = avatar.startsWith('http') || avatar.startsWith('/') || avatar.startsWith('data:');
  const avatarFallback = isAvatarUrl ? name.charAt(0).toUpperCase() : avatar || name.charAt(0).toUpperCase();

  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <button
          type='button'
          className='hover:bg-accent focus-visible:ring-ring flex items-center gap-2 rounded-lg px-2 py-1.5 transition-colors focus-visible:ring-2 focus-visible:outline-none'
          aria-label={t('sidebar.userMenu.account', 'Account')}
        >
          <Avatar className='h-8 w-8 rounded-lg'>
            {isAvatarUrl && <AvatarImage src={avatar} alt={name} />}
            <AvatarFallback className='rounded-lg'>{avatarFallback}</AvatarFallback>
          </Avatar>
          <ChevronsUpDown className='text-muted-foreground size-4' />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className='min-w-56 rounded-lg' side='bottom' align='end' sideOffset={4}>
        <DropdownMenuLabel className='p-0 font-normal'>
          <div className='flex items-center gap-2 px-1 py-1.5 text-left text-sm'>
            <Avatar className='h-8 w-8 rounded-lg'>
              {isAvatarUrl && <AvatarImage src={avatar} alt={name} />}
              <AvatarFallback className='rounded-lg'>{avatarFallback}</AvatarFallback>
            </Avatar>
            <div className='grid flex-1 text-left text-sm leading-tight'>
              <span className='truncate font-semibold'>{name}</span>
              <span className='text-muted-foreground truncate text-xs'>{user.email}</span>
            </div>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem asChild>
            <Link to='/settings/profile'>
              <BadgeCheck />
              {t('sidebar.userMenu.account')}
            </Link>
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={signOut}>
          <LogOut />
          {t('sidebar.userMenu.logOut')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
