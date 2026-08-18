# MOUNTPOINTS — 对上游文件的改动登记

> 规则见 docs/contracts/vendor-isolation.md：新增代码只进 `src/ai4s/`；改上游文件必须在此登记（文件+位置+改动+原因）；**高频变动区禁改**（provider 对接、协议转换、模型目录）。

| 文件 | 位置 | 改动 | 原因 | 日期 |
|---|---|---|---|---|
| `src/index.css` | 文件末尾 +2 行 | 追加 `@import './ai4s/theme/tokens.css'` | issue #10 设计 token 覆盖层入口（W/D 风格），同特异性后加载胜出，上游组件零修改 | 2026-08-01 |
| `package.json` / `pnpm-lock.yaml` | dependencies | 新增 `@fontsource/geist`、`@fontsource/geist-mono`（纯新增，未改上游依赖版本） | token 层字体自托管（Geist/Geist Mono），tokens.css 引用 | 2026-08-01 |
| `src/authenticated-layout.tsx` | 全文重构 | 移除 AppSidebar/侧边栏逻辑，改为 AppHeader + Ai4sTopNavBar 两行固定头部；保留 SidebarProvider（AppHeader 的 useSidebar 依赖） | issue #11 C 结构顶部导航；新组件在 src/ai4s/layout/ | 2026-08-01 |
| `src/routes/_authenticated/index.tsx` | import 与组件 | Dashboard 换为 `@/ai4s/pages/dashboard/Ai4sDashboard` | issue #11 C 结构高密度看板 + 右侧信息面板；上游 Dashboard 保留于 features/dashboard 未动 | 2026-08-01 |
| `src/components/layout/app-header.tsx` | 布局左区 | 删除 SidebarTrigger（无侧边栏后的死按钮）及其 import | issue #11 配套清理 | 2026-08-01 |
| `src/lib/i18n.ts` | 顶部 +1 行 import、zhTranslation merge 参数 +1 | 引入 `src/ai4s/locales/zh-CN/ai4s-patch.json` 补键包（现 72 键：初版 61 个上游 zh-CN 缺失键 profile/security/common 等，后角色扩展为 ai4s 功能键载体，如 #64 batchTier.*） | issue #12 中文补键；补键包本体在隔离区 | 2026-08-02 |
| `src/routes/_authenticated/project/requests/index.tsx` | import 与组件 | 审计日志换 `@/ai4s/pages/requests/Ai4sRequestsPage`（元数据抽屉 + warn 提示条，不展示原文） | issue #13 审计原则 | 2026-08-02 |
| `src/routes/_authenticated/project/requests/$requestId.tsx` | 全文 | 深链统一 redirect 回 `/project/requests`（上游详情页含原文，禁用） | issue #13 配套 | 2026-08-02 |
| `src/routes/_authenticated/prompt-protection-rules/index.tsx` | import 与组件 | 脱敏规则换 `@/ai4s/pages/rules/Ai4sRulesPage`（link-ai 式，类型/优先级展示层派生） | issue #13 | 2026-08-02 |
| `src/features/settings/appearance/appearance-form.tsx` | colorScheme 字段 | 移除 colorScheme 选择器（9 个上游 scheme 与 ai4s 定稿主题冲突；tokens.css 已全类覆盖，明暗设置与显示强制一致） | 配色设置与显示不符 bug（2026-08-02 用户反馈） | 2026-08-02 |
| `src/routes/_authenticated/index.tsx` | import 与组件 | **恢复上游原生 dashboard**（M4 换皮撤销；信息全量，C×W 主题经 token 层生效） | 用户反馈：信息不应缩减（2026-08-02） | 2026-08-02 |
| `src/routes/_authenticated/project/requests/index.tsx` + `$requestId.tsx` | import 与组件 | **恢复上游原生审计日志与详情**（M7/M8 撤销；用户拍板"审计日志不应该拿走"） | 同上 | 2026-08-02 |
| `src/components/layout/app-header.tsx` | 右侧控件区末尾 + import 1 行 | 挂载 `Ai4sUserMenu`（顶栏用户菜单：账户信息/个人资料/退出登录） | C 结构移除侧边栏后上游 NavUser 失挂载，用户无退出入口（2026-08-03 用户反馈）；组件本体在 `src/ai4s/components/` | 2026-08-03 |
| `src/sidebar.ts` | rawNavGroups 全文 + 图标 import | 侧栏数据 17→11 项：管理组 channels+models→「接入管理」、users+roles→「用户与角色」，项目组 requests/usageStats/traces/threads→「观测」、project-users+project-roles→「成员」；删 IconAB2/IconBaselineDensityMedium/IconChartBar | issue #54 侧栏归并；该数据驱动 ⌘K 命令面板分组（UI 导航本体是 src/ai4s/ Ai4sTopNavBar） | 2026-08-11 |
| `src/features/{channels,models,users,roles,requests,usage-statistics,traces,threads,proejct-users,project-roles}/index.tsx` | import 区 +2 行、`<Main>` 首子元素 +1 行 | 各页挂 `<Ai4sPageTabs>` 页内 Tab 栏（同域页互跳，useRoutePermissions 过滤，≤1 可见项不渲染） | issue #54 页内 Tab；组件与 Tab 组定义在 `src/ai4s/components/Ai4sPageTabs.tsx` / `page-tab-groups.ts` | 2026-08-11 |
| `src/locales/zh-CN/base.json` / `src/locales/en/base.json` | `sidebar.items.projects` 行后各 +4 键 | 新增 `sidebar.items.accessManagement`（接入管理/Access Management）、`usersAndRoles`（用户与角色/Users & Roles）、`observability`（观测/Observability）、`members`（成员/Members） | issue #54 新侧栏标题；Tab label 复用既有 sidebar.items.* 键 | 2026-08-11 |
| `index.html` | head 区 | title/meta title/og:title 改 `Ai-4S-infra`；description/og:description 改写为企业级 AI 基础设施定位（#58 初版为 AI for Science，随 4S 含义修正同步改）；favicon 换 `<link rel="icon" type="image/svg+xml" href="/logo.svg">` + `/favicon.ico` 回退（修复原 `href="/favicon"` 经 SPA fallback 返回 HTML 的问题） | issue #58 品牌改造；4S 修正（#58 后续） | 2026-08-18 |
| `src/locales/zh-CN/base.json` / `src/locales/en/base.json` | 3 键改值 + `auth.brand.description` 行后各 +10 键 | `sidebar.team.name`、`initialization.description`、`auth.signIn.title` 改 Ai-4S-infra；新增 `ai4s.brand.*`（name/tagline/四支柱 name+desc，中英双语；cardDescription 未接入 UI 成死键，issue #59 已删；4S 含义修正后 pillars 键为 security/smart/sustainability/success，desc 均为中文注释，tagline 改 企业级 AI 基础设施/Enterprise AI Infrastructure） | issue #58 更名 + 4S 说明文案；4S 修正（#58 后续） | 2026-08-18 |
| `src/locales/zh-CN/system.json` / `src/locales/en/system.json` | 各 9 键改值 | 用户可见文案 AxonHub→Ai-4S-infra：brand.titleDescription、onboarding.description/welcome.title/brandLogo.description、about.title、userAgentPassThrough.helpText、quota.description/enabled.description/mode.description | issue #58 更名 | 2026-08-18 |
| `src/routes/__root.tsx` | DocumentTitleSync | document.title fallback `'AxonHub'`→`'Ai-4S-infra'` | issue #58 浏览器标签页标题 | 2026-08-18 |
| `src/features/auth/auth-layout.tsx` | 顶栏品牌位 | logo.jpg→logo.svg、alt 与标题改 Ai-4S-infra，渐变文字换暖橙（应 token terracotta 主色） | issue #58 品牌位换新 | 2026-08-18 |
| `src/features/auth/components/two-column-auth.tsx` | 左栏品牌区 | 原 h1/h2/p 品牌块换 `<Ai4sBrandPanel />`（logo+名称+4S 四支柱）；移除不再使用的 useTranslation | issue #58 登录页 4S 说明；组件本体在 `src/ai4s/components/Ai4sBrandPanel.tsx` | 2026-08-18 |
| `src/features/auth/sign-in/components/auto-router-diagram.tsx` | 弧形 textPath | 文案 AxonHub→Ai-4S-infra | issue #58 更名（登录页图示用户可见） | 2026-08-18 |
| `src/components/layout/app-header.tsx` | Logo 区 | 品牌名 fallback 改 Ai-4S-infra；默认 logo 与 onError 回退 logo.jpg→logo.svg | issue #58 顶栏品牌位换新 | 2026-08-18 |
| `src/components/layout/team-switcher.tsx` | 品牌名 fallback + 4 处默认 logo | 同上（logo.jpg→logo.svg，fallback 改 Ai-4S-infra） | issue #58 配套 | 2026-08-18 |
| `src/features/dashboard/index.tsx` | import 区 +1 行、`<Header />` 后 +1 块 | 挂 `<Ai4sBrandCard />` 品牌说明卡（名称+4S 含义，低调横卡） | issue #58 首页 4S 说明；组件本体在 `src/ai4s/components/Ai4sBrandCard.tsx` | 2026-08-18 |
| `src/features/errors/not-found-error.tsx` | suggestedPages[0].description | 'Overview of your AxonHub instance'→'…Ai-4S-infra instance' | issue #58 用户可见文案 | 2026-08-18 |
| `src/features/channels/components/channels-system-settings-dialog.tsx` | DEFAULT_TEST_USER_PROMPT 常量 | 渠道测试默认 prompt 中 AxonHub→Ai-4S-infra（单行常量，渠道页高频区最小侵入） | issue #58 用户可见默认文案 | 2026-08-18 |
| `src/features/apikeys/components/apikeys-view-dialog.tsx` | codex 配置示例 display/real 两段 | 用户侧指引品牌化：`AXONHUB_API_KEY`→`AI4S_API_KEY`、`axonhub-responses`→`ai4s-responses`、注释与 provider name 改 Ai-4S-infra（片段自闭合，仓库内无其他引用）；currentOrigin SSR 兜底 `http://localhost:8090`→`:3000`（issue #60 宿主口收拢配套） | issue #58 用户可见接入指引 | 2026-08-18 |
| `src/features/system/data/system.ts` | exportBackup onSuccess | 备份下载文件名 `axonhub-backup-*`→`ai4s-infra-backup-*` | issue #58 用户可见产物名 | 2026-08-18 |
| `public/logo.svg` | 新增文件（上游区域） | 新品牌主 logo：扁平几何盾牌+「4S」字标，terracotta 渐变取 token 层 #ea580c/#c2410c；引用点：index.html favicon link、auth-layout、app-header、team-switcher、Ai4sBrandPanel/Ai4sBrandCard | issue #58 新 logo；issue #59 补登记 | 2026-08-18 |
| `public/logo.jpg` | 删除 | 旧 AxonHub logo；全部引用点换 logo.svg 后删除 | issue #58；issue #59 补登记 | 2026-08-18 |
| `public/favicon.ico` | 替换 | 从 logo.svg 经 rsvg-convert+PIL 重渲打包 16/32/48 三层，与 index.html `sizes="16x16 32x32 48x48"` 声明一致（#58 初版漏 48 层，issue #59 补齐） | issue #58 favicon 回退；issue #59 补 48 层+补登记 | 2026-08-18 |
| `vite.config.ts` | server.proxy `/admin`、`/oauth`、`/v1` 三处 target 默认值 | dev 代理默认 `http://localhost:8090`→`http://localhost:3000` | issue #60 宿主调试口收拢：宿主侧默认一律走 :3000 网关反代 | 2026-08-18 |
| `playwright.config.ts` | AXONHUB_API_URL 默认值 + webServer.env.VITE_API_URL 兜底 | 默认 `http://localhost:8099`→`http://localhost:3000` | 同上（issue #60） | 2026-08-18 |
| `src/features/apikeys/index.tsx` | import 区 +1 行、Header 右侧按钮区包一层 div 内 +1 行 | 挂载 `<Ai4sBatchTierDialog />` 批量换档入口（筛选→预览→逐条执行回报；组件内部按 write_api_keys 自隐藏）；组件与数据层/纯逻辑在 `src/ai4s/apikeys/` | issue #64 存量 key 批量调档 | 2026-08-18 |
| `src/sidebar.ts` + `src/features/{users,roles,proejct-users,project-roles}/index.tsx` | sidebar.ts 项目组删「成员」项、管理组「用户与角色」改「人员」（/users）；四页 Ai4sPageTabs 入参 usersRoles/members → people | issue #65 人员域合并：⌘K 归并单域、四页统一 Tab 组（组定义在 `src/ai4s/components/page-tab-groups.ts`） | 2026-08-18 |
| `src/locales/zh-CN/base.json` / `src/locales/en/base.json` | `sidebar.items.members` 行后各 +3 键 | 新增 `sidebar.items.people`（人员/People）、`projectMembers`（项目成员/Project Members）、`projectRoles`（项目角色/Project Roles） | issue #65 人员域合并新标签；users/roles 复用既有键 | 2026-08-18 |
| `package.json` | scripts.test:unit | glob 加引号 `node --test "src/**/*.test.mjs"` 交 node 展开（shell 不带 globstar 时 `**` 退化为单层，漏收 `src/features/channels/data/` 等嵌套测试） | issue #64/#65 评审 P2 | 2026-08-18 |
| `src/locales/zh-CN/base.json` / `src/locales/en/base.json` | 各删 2 键 | 删除死键 `sidebar.items.usersAndRoles`、`sidebar.items.members`（#65 后代码零引用，全仓含 web/tests grep 确认） | issue #64/#65 评审 P2 | 2026-08-18 |
| `src/sidebar.ts` | 项目组 playground 后 +1 项 + 注释 | 加回「人员」（`sidebar.items.people` → /project/users）：与管理组 /users 同名不同组，靠 filterNavGroups/checkRouteAccess 按各自 scopeLevel 过滤（/users=system 组，/project/users=any 组）；顶栏人员入口同步按 hasSystemScope('read_users') 回落（组件在 `src/ai4s/layout/Ai4sTopNavBar.tsx`，隔离区内不另登记） | issue #64/#65 评审 P2：无系统级 read_users 的用户人员入口可达性 | 2026-08-18 |
| `src/features/apikeys/index.tsx` | 文件顶部 +4 行开关常量、Tabs 块包条件渲染 | `SHOW_TYPE_TABS = false` 使类型 Tab 条（全部/项目级/个人/服务账号）整体不渲染，默认停 'all'；activeTab 状态与 whereClause 过滤逻辑保留，表格「类型」列不动；恢复时开关改回 true | issue #66 去 Tab 化（列表行内类型列承载分类） | 2026-08-18 |
| `src/features/proejct-users/index.tsx` / `src/features/roles/index.tsx` | import 区各 -2 行、`<Main>` 内各 -1 行（+1 注释） | 摘除 #54/#65 挂载的 `<Ai4sPageTabs tabs={pageTabGroups.people(t)} />`，两页恢复无 Tab 裸页直访；/users、/project/roles 保持挂 people 组（组定义缩为两项，在 `src/ai4s/components/page-tab-groups.ts`，隔离区） | issue #66 人员 Tab 组四→二 | 2026-08-18 |
| `src/locales/zh-CN/base.json` / `src/locales/en/base.json` | 各删 2 键 | 删 `sidebar.items.projectMembers`、`sidebar.items.projectRoles`（#65 新增、#66 四→二后零引用，grep 全仓确认；`projectRoles.*` 页面键与 `sidebar.items.people` 仍在用不动） | issue #66 配套清死键 | 2026-08-18 |
