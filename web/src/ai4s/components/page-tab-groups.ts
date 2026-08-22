import { type LinkProps } from '@tanstack/react-router';

/**
 * 页内 Tab 项（issue #54 侧栏归并）：label 为已翻译文案，url 为目标路由（routeTree 强类型）。
 */
export interface PageTab {
  label: string;
  url: LinkProps['to'];
}

/**
 * 页内 Tab 组定义：同域页面共享一组，各页调用时传入 t() 取文案。
 * label 复用 sidebar.items.* 既有 locale 键（与侧栏同源，不产生新翻译负担）。
 */
export const pageTabGroups = {
  /** 接入管理：渠道 | 模型 */
  access: (t: (key: string) => string): PageTab[] => [
    { label: t('sidebar.items.channels'), url: '/channels' },
    { label: t('sidebar.items.models'), url: '/models' },
  ],
  /** 人员（issue #65 合并全局/项目两域，取代旧 usersRoles/members 两组；
      issue #66 去 Tab 化四→二：只留有内容的两页——用户（全局 /users）| 角色（项目角色页 /project/roles）；
      /project/users、/roles 从组里摘下恢复无 Tab 裸页，页面保留可直访） */
  people: (t: (key: string) => string): PageTab[] => [
    { label: t('sidebar.items.users'), url: '/users' },
    { label: t('sidebar.items.roles'), url: '/project/roles' },
  ],
  /** 日志（项目组，issue #80 改名，键名 observability 保留）：请求 | 用量统计 | 追踪 | 线程 */
  observability: (t: (key: string) => string): PageTab[] => [
    { label: t('sidebar.items.requests'), url: '/project/requests' },
    { label: t('sidebar.items.usageStats'), url: '/project/usage-stats' },
    { label: t('sidebar.items.traces'), url: '/project/traces' },
    { label: t('sidebar.items.threads'), url: '/project/threads' },
  ],
};
