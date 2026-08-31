import { useAi4sNavItems, isAi4sNavActive } from './useAi4sNavItems';

/**
 * ai4s 顶部导航（C 结构）
 * 桌面端（md+）：全部条目内联展示：图标 + 中文标签；激活项 = accent-soft 底 + accent 字（与 C×W token 联动）。
 * 移动端（<md）：横滚条隐藏，导航收进 AppHeader 左侧汉堡抽屉（Ai4sMobileNav）。
 * 新增代码按 vendor 隔离规则只落 src/ai4s/。
 * issue #54 归并：渠道管理+模型 →「接入管理」（页内 Tab 切换），用户+角色 →「用户与角色」；
 * 日志域的用量统计/追踪/线程、成员域的用户/角色同样经各页 Ai4sPageTabs 可达。
 * issue #55 收尾：「审计日志」统一为「观测」（与 ⌘K 域名一致）；补「成员」入口
 * （/project/users、/project/roles 直达时顶栏有激活项）。
 * issue #80：「观测」改名「日志」（zh）/ Logs（en），键名 sidebar.items.observability 与路由不变。
 * issue #65：成员（项目域）+ 用户与角色（全局域）合并为单一「人员」入口，
 * 四个人员域页面经统一 people Tab 组互跳；顶栏 10 项 → 9 项。
 * issue #65 评审 P2：人员入口按权限回落——有系统级 read_users 落全局 /users，
 * 否则落项目域 /project/users（route-permission：/users 属 system 组，/project/users 属 any 组）。
 * issue #69 P3：入口按 routeConfigs mode:'hidden' 语义做权限过滤（与侧栏/⌘K 的 filterNavItems 一致），
 * 无权限且 mode=hidden 的入口不再渲染（此前员工可见 9 个入口、其中 7 个点进去 Access Denied）。
 * issue #70：label 走 i18n（与 ⌘K 同源复用 sidebar.items.*；zh 文案与 ⌘K 不同的四项——
 * 仪表盘/我的 Key/脱敏规则/系统设置——用 ai4s.topnav.* 键，zh 保持顶栏既有文案不回退）。
 * issue #74：新增员工自助「我的 Key」入口（/project/my-keys，label 复用 sidebar.items.myKeys）——
 * 无 scope 门槛，零 scope 员工经 routeConfigs 同名条目 + checkRouteAccess 过滤后可见；
 * 管理员同时看到本项与「Key 管理」（/project/api-keys），语义分别为「我的」与「全量管理」。
 * issue #74 评审 P2：「我的 Key」图标改用 IconId（身份语义），与「Key 管理」的 IconKey 区分。
 * issue #79：新增管理组「Key 审批」入口（/key-requests，label 复用 sidebar.items.keyRequests）——
 * 控制台申请通道的点批页；审批卡 link 按钮落点同此页。
 * issue #113：Playground 接入「接入管理」页内 Tab 组（/project/playground），
 * match 同步加 '/project/playground'——直达 Playground 时顶栏高亮「接入管理」。
 * issue #120：新增管理组「智能路由」入口（/smart-routing，label 复用 sidebar.items.smartRouting）——
 * shim routing 节配置 + router 层决策日志观测页；权限过滤走 routeConfigs 同名条目（read_channels）。
 */

import { Link, useRouterState } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';

export function Ai4sTopNavBar() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { t } = useTranslation();
  const navItems = useAi4sNavItems();

  return (
    // 移动端导航由 Ai4sMobileNav 汉堡抽屉承担，横滚条仅桌面展示
    <div className='bg-background/95 supports-[backdrop-filter]:bg-background/60 fixed top-14 z-40 hidden w-full border-b backdrop-blur md:block'>
      <nav className='flex h-11 items-center gap-1 overflow-x-auto px-4'>
        {navItems.map(({ labelKey, href, match, icon: Icon }) => {
          const active = isAi4sNavActive({ href, match }, pathname);
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
