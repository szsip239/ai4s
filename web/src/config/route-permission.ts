// 路由权限配置
export type ScopeLevel = 'system' | 'project' | 'any';

export interface RouteConfig {
  path: string;
  requiredScopes?: string[];
  scopeLevel?: ScopeLevel; // 权限级别：system 只检查系统级权限，project 只检查项目级权限，any 检查两者
  mode?: 'hidden' | 'disabled'; // 当没有权限时的处理方式
  children?: RouteConfig[];
  requireProjectOwner?: boolean; // 是否需要 project owner (或 system owner)
}

export interface RouteGroup {
  title: string;
  key?: string; // 稳定匹配键（issue #69 P3：filterNavGroups 不再按翻译后标题匹配英文配置字面量）
  scopeLevel?: ScopeLevel; // 路由组的默认权限级别
  routes: RouteConfig[];
}

// 定义所有路由的权限配置
export const routeConfigs: RouteGroup[] = [
  {
    title: 'Admin',
    key: 'admin',
    scopeLevel: 'system', // Admin 路由组只能通过 system-level 权限访问
    routes: [
      {
        path: '/',
        requiredScopes: ['read_dashboard'],
        mode: 'hidden',
      },
      {
        path: '/projects',
        requiredScopes: ['read_projects'],
        mode: 'hidden',
      },
      {
        path: '/users',
        requiredScopes: ['read_users'],
        mode: 'hidden',
      },
      {
        path: '/roles',
        requiredScopes: ['read_roles'],
        mode: 'hidden',
      },
      {
        path: '/channels',
        requiredScopes: ['read_channels'],
        mode: 'hidden',
      },
      {
        path: '/models',
        requiredScopes: ['read_channels'],
        mode: 'hidden',
      },
      {
        path: '/prompt-protection-rules',
        requiredScopes: ['read_channels'],
        mode: 'hidden',
      },
      {
        path: '/key-requests',
        // Key 审批（issue #79）：控制台申请通道的管理员点批页，与词表/规则页同门槛
        requiredScopes: ['read_channels'],
        mode: 'hidden',
      },
      {
        path: '/smart-routing',
        // 智能路由（issue #120）：shim settings routing 节配置 + router 层决策日志观测，读级与 shim 对齐
        requiredScopes: ['read_channels'],
        mode: 'hidden',
      },
      {
        path: '/data-storages',
        requiredScopes: ['read_data_storages'],
        mode: 'hidden',
      },
      {
        path: '/api-keys',
        requiredScopes: ['read_api_keys'],
        mode: 'hidden',
      },
      {
        path: '/system',
        requiredScopes: ['read_settings'],
        mode: 'hidden',
      },
      {
        path: '/permission-demo',
        // 权限演示页面所有用户都可以访问
      },
    ],
  },
  {
    title: 'Project',
    key: 'project',
    scopeLevel: 'any', // Project 路由组可以通过 system-level 或 project-level 权限访问
    routes: [
      {
        path: '/project/my-keys',
        // 我的 Key（issue #74）：所有登录用户可见（员工 users.scopes=[] 是 #68 后常态），
        // 数据面由 shim /self/keys 服务端按本人过滤，页面本身无 scope 门槛
      },
      {
        path: '/project/api-keys',
        requiredScopes: ['read_api_keys'],
        mode: 'hidden',
      },
      {
        path: '/project/prompts',
        requiredScopes: ['read_prompts'],
        mode: 'hidden',
      },
      {
        path: '/project/requests',
        requiredScopes: ['read_requests'],
        mode: 'hidden',
      },
      {
        path: '/project/usage-logs',
        requiredScopes: ['read_requests'],
        mode: 'hidden',
      },
      {
        path: '/project/usage-stats',
        requiredScopes: ['read_requests'],
        mode: 'hidden',
        requireProjectOwner: true,
      },
      {
        path: '/project/threads',
        requiredScopes: ['read_requests'],
        mode: 'hidden',
      },
      {
        path: '/project/roles',
        requiredScopes: ['read_roles'],
        mode: 'hidden',
      },
      {
        path: '/project/playground',
        // issue #69 P2-E：与路由实际 RouteGuard（write_requests/read_channels，any 级）对齐，
        // 否则登录落点/导航过滤会把无这两个 scope 的用户导向 403
        requiredScopes: ['write_requests', 'read_channels'],
        mode: 'hidden',
      },
    ],
  },
  {
    title: 'Settings',
    key: 'settings',
    routes: [
      {
        path: '/settings',
        // Profile 设置所有用户都可以访问
      },
      {
        path: '/settings/profile',
        // Profile 设置所有用户都可以访问
      },
      {
        path: '/settings/appearance',
        // Appearance 设置所有用户都可以访问
      },
      {
        path: '/settings/notifications',
        // Notifications 设置所有用户都可以访问
      },
    ],
  },
];

// 获取路由配置的辅助函数
export function getRouteConfig(path: string): RouteConfig | undefined {
  for (const group of routeConfigs) {
    for (const route of group.routes) {
      if (route.path === path) {
        return route;
      }
      if (route.children) {
        const childConfig = route.children.find((child) => child.path === path);
        if (childConfig) return childConfig;
      }
    }
  }
  return undefined;
}

// 检查用户是否有访问路由的权限
export function hasRouteAccess(userScopes: string[], routeConfig: RouteConfig): boolean {
  if (!routeConfig.requiredScopes || routeConfig.requiredScopes.length === 0) {
    return true;
  }

  // 如果用户有通配符权限，则拥有所有权限
  if (userScopes.includes('*')) {
    return true;
  }

  // 检查用户是否拥有所需的任一权限
  return routeConfig.requiredScopes.some((scope) => userScopes.includes(scope));
}

// 检查用户是否有访问路由组的权限
export function hasGroupAccess(userScopes: string[], group: RouteGroup): boolean {
  return group.routes.some((route) => hasRouteAccess(userScopes, route));
}

// issue #69 P2-E：登录落点 / 403 页「返回」的兜底路径——按候选顺序取第一个当前用户可用的页面，
// 不再写死 /project/playground（无 write_requests/read_channels 的用户落上去即 403，Go Back 回 / 又 403 成死路）。
// 前提：候选路径均不带 requireProjectOwner 标记（当前候选无一需要）；日后加入带该标记的候选须先扩展本函数判定维度。
// issue #74 评审 P2：/project/my-keys 排在候选末位（/settings/profile 兜底之前）——
// 排序语义=首个可达候选胜出：管理员 '/'（read_dashboard）先命中不受影响；
// 零 scope 员工前面候选全不达，落「我的 Key」（无 requiredScopes）而非个人资料页。
const LANDING_CANDIDATE_PATHS = [
  '/',
  '/project/api-keys',
  '/project/requests',
  '/project/prompts',
  '/project/playground',
  '/project/my-keys',
];

export function resolveLandingPath({
  isOwner = false,
  systemScopes = [],
  projectScopes = [],
}: {
  isOwner?: boolean;
  systemScopes?: string[];
  projectScopes?: string[];
}): string {
  if (isOwner) {
    return '/';
  }
  for (const path of LANDING_CANDIDATE_PATHS) {
    for (const group of routeConfigs) {
      const route = group.routes.find((r) => r.path === path);
      if (!route) continue;
      const scopeLevel = route.scopeLevel || group.scopeLevel || 'any';
      const pool = scopeLevel === 'system' ? systemScopes : scopeLevel === 'project' ? projectScopes : [...systemScopes, ...projectScopes];
      if (!route.requiredScopes || route.requiredScopes.length === 0) {
        return path;
      }
      if (pool.includes('*') || route.requiredScopes.some((scope) => pool.includes(scope))) {
        return path;
      }
    }
  }
  // 全部候选都不可用（零 scope 用户）：落个人资料设置页（无 scope 要求，全用户可用）
  return '/settings/profile';
}
