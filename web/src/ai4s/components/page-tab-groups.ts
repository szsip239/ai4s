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
  /** 接入管理：渠道 | 模型 | Playground（issue #113，label 复用 sidebar.items.playground） */
  access: (t: (key: string) => string): PageTab[] => [
    { label: t('sidebar.items.channels'), url: '/channels' },
    { label: t('sidebar.items.models'), url: '/models' },
    { label: t('sidebar.items.playground'), url: '/project/playground' },
  ],
  /** 人员（issue #65 合并全局/项目两域，取代旧 usersRoles/members 两组；
      issue #66 去 Tab 化四→二：只留有内容的两页——用户（全局 /users）| 角色（项目角色页 /project/roles）；
      后续 /project/users 整页删除，项目成员管理收归 /users 页内对话框） */
  people: (t: (key: string) => string): PageTab[] => [
    { label: t('sidebar.items.users'), url: '/users' },
    { label: t('sidebar.items.roles'), url: '/project/roles' },
  ],
  /** 日志（项目组，issue #80 改名，键名 observability 保留）：请求 | 用量统计 | 拦截
      （issue #132：追踪/线程上游无数据摘下；后续 traces 路由与特征整删，
      threads 保留可直访并吸收原 traces 的 segments 时间线共享件） */
  observability: (t: (key: string) => string): PageTab[] => [
    { label: t('sidebar.items.requests'), url: '/project/requests' },
    { label: t('sidebar.items.usageStats'), url: '/project/usage-stats' },
    { label: t('sidebar.items.blocks'), url: '/project/blocks' },
  ],
};
