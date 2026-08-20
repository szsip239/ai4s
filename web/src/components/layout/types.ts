import { LinkProps } from '@tanstack/react-router';

interface User {
  name: string;
  email: string;
  avatar: string;
}

interface Team {
  name: string;
  logo: React.ElementType;
  description: string;
}

interface BaseNavItem {
  title: string;
  badge?: string;
  icon?: React.ElementType;
  isDisabled?: boolean;
}

type NavLink = BaseNavItem & {
  url: LinkProps['to'];
  items?: never;
  mobileOnly?: boolean;
};

type NavCollapsible = BaseNavItem & {
  items: (BaseNavItem & { url: LinkProps['to'] })[];
  url?: never;
};

type NavItem = NavCollapsible | NavLink;

interface NavGroup {
  title: string;
  key?: string; // 稳定匹配键，与 routeConfigs 组的 key 对应（issue #69 P3：不按翻译后标题匹配）
  items: NavItem[];
}

interface SidebarData {
  user: User;
  teams: Team[];
  navGroups: NavGroup[];
}

export type { SidebarData, NavGroup, NavItem, NavCollapsible, NavLink };
