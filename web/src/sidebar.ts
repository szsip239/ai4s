import {
  IconLayoutDashboard,
  IconPackages,
  IconSettings,
  IconUsers,
  IconRobot,
  IconShield,
  IconKey,
  IconActivity,
  IconDatabase,
  IconAi,
  IconNote,
} from '@tabler/icons-react';
import { Command } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores/authStore';
import { useRoutePermissions } from '@/hooks/useRoutePermissions';
import { useMe } from '@/features/auth/data/auth';
import { type SidebarData, type NavGroup, type NavLink } from './components/layout/types';

export function useSidebarData(): SidebarData {
  const { t, i18n } = useTranslation();
  const { user: authUser } = useAuthStore((state) => state.auth);
  const { data: meData } = useMe();
  const { filterNavGroups } = useRoutePermissions();

  // Use data from me query if available, otherwise fall back to auth store
  const user = meData || authUser;

  // Generate user initials for avatar
  const getInitials = (firstName?: string, lastName?: string, email?: string) => {
    if (firstName && lastName) {
      const isZh = i18n.language?.startsWith('zh');
      const [first, second] = isZh ? [lastName, firstName] : [firstName, lastName];
      return `${first.charAt(0)}${second.charAt(0)}`.toUpperCase();
    }
    if (firstName) {
      return firstName.slice(0, 2).toUpperCase();
    }
    if (email) {
      return email.split('@')[0].slice(0, 2).toUpperCase();
    }
    return 'U';
  };

  // Generate user display name
  const getDisplayName = (firstName?: string, lastName?: string, email?: string) => {
    if (firstName && lastName) {
      const isZh = i18n.language?.startsWith('zh');
      return isZh ? `${lastName} ${firstName}` : `${firstName} ${lastName}`;
    }
    if (firstName) {
      return firstName;
    }
    if (email) {
      const username = email.split('@')[0];
      return username.charAt(0).toUpperCase() + username.slice(1);
    }
    return 'User';
  };

  // 原始导航组配置（issue #54 侧栏归并 17→11：同域页面合并为入口+页内 Tab，
  // 被合并路由（/models、/roles、/project/usage-stats、/project/traces、/project/threads、
  // /project/roles）保持可达，经各页 PageTabs 切换；
  // issue #65 人员域再归并：项目「成员」并入管理组「人员」（/users 入口），
  // /project/users、/project/roles 经 people Tab 组可达；
  // issue #65 评审 P2：项目组加回「人员」（/project/users）——与管理组 /users 同名不同组，
  // 经 filterNavGroups/checkRouteAccess 按各自 scopeLevel 过滤（/users=system 组，/project/users=any 组））
  const rawNavGroups: NavGroup[] = [
    {
      title: t('sidebar.groups.admin'),
      key: 'admin',
      items: [
        {
          title: t('sidebar.items.dashboard'),
          url: '/',
          icon: IconLayoutDashboard,
        } as NavLink,
        {
          title: t('sidebar.items.promptProtectionRules'),
          url: '/prompt-protection-rules',
          icon: IconShield,
        } as NavLink,
        {
          title: t('sidebar.items.accessManagement'),
          url: '/channels',
          icon: IconAi,
        } as NavLink,
        {
          title: t('sidebar.items.projects'),
          url: '/projects',
          icon: IconPackages,
        } as NavLink,
        {
          title: t('sidebar.items.people'),
          url: '/users',
          icon: IconUsers,
        } as NavLink,
        {
          title: t('sidebar.items.dataStorages'),
          url: '/data-storages',
          icon: IconDatabase,
        } as NavLink,
        // {
        //   title: 'Permission Demo',
        //   url: '/permission-demo',
        //   icon: IconSettings,
        // } as NavLink,
      ],
    },
    {
      title: t('sidebar.groups.project'),
      key: 'project',
      items: [
        {
          title: t('sidebar.items.apiKeys'),
          url: '/project/api-keys',
          icon: IconKey,
        } as NavLink,
        {
          title: t('sidebar.items.observability'),
          url: '/project/requests',
          icon: IconActivity,
        } as NavLink,
        {
          title: t('sidebar.items.prompts'),
          url: '/project/prompts',
          icon: IconNote,
        } as NavLink,
        {
          title: t('sidebar.items.playground'),
          url: '/project/playground',
          icon: IconRobot,
        } as NavLink,
        {
          title: t('sidebar.items.people'),
          url: '/project/users',
          icon: IconUsers,
        } as NavLink,
        // {
        //   title: t('sidebar.items.usageLogs'),
        //   url: '/project/usage-logs',
        //   icon: IconActivityHeartbeat,
        // } as NavLink,
      ],
    },
    {
      title: t('sidebar.groups.settings'),
      key: 'settings',
      items: [
        {
          title: t('sidebar.items.system'),
          url: '/system',
          icon: IconSettings,
          mobileOnly: true,
        } as NavLink,
        // {
        //   title: 'Account',
        //   url: '/settings/account',
        //   icon: IconTool,
        // } as NavLink,
        // {
        //   title: 'Appearance',
        //   url: '/settings/appearance',
        //   icon: IconPalette,
        // } as NavLink,
        // {
        //   title: 'Notifications',
        //   url: '/settings/notifications',
        //   icon: IconNotification,
        // } as NavLink,
      ],
    },
  ];

  // 使用权限过滤导航组
  const filteredNavGroups = filterNavGroups(rawNavGroups);

  return {
    user: {
      name: getDisplayName(user?.firstName, user?.lastName, user?.email),
      email: user?.email || 'user@example.com',
      avatar: user?.avatar || getInitials(user?.firstName, user?.lastName, user?.email),
    },
    teams: [
      {
        name: t('sidebar.team.name'),
        logo: Command,
        description: '',
        // DO NOT USE THIS
        // plan: t('sidebar.team.plan'),
      },
    ],
    navGroups: filteredNavGroups,
  };
}
