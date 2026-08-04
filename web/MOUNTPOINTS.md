# MOUNTPOINTS — 对上游文件的改动登记

> 规则见 docs/contracts/vendor-isolation.md：新增代码只进 `src/ai4s/`；改上游文件必须在此登记（文件+位置+改动+原因）；**高频变动区禁改**（provider 对接、协议转换、模型目录）。

| 文件 | 位置 | 改动 | 原因 | 日期 |
|---|---|---|---|---|
| `src/index.css` | 文件末尾 +2 行 | 追加 `@import './ai4s/theme/tokens.css'` | issue #10 设计 token 覆盖层入口（W/D 风格），同特异性后加载胜出，上游组件零修改 | 2026-08-01 |
| `package.json` / `pnpm-lock.yaml` | dependencies | 新增 `@fontsource/geist`、`@fontsource/geist-mono`（纯新增，未改上游依赖版本） | token 层字体自托管（Geist/Geist Mono），tokens.css 引用 | 2026-08-01 |
| `src/authenticated-layout.tsx` | 全文重构 | 移除 AppSidebar/侧边栏逻辑，改为 AppHeader + Ai4sTopNavBar 两行固定头部；保留 SidebarProvider（AppHeader 的 useSidebar 依赖） | issue #11 C 结构顶部导航；新组件在 src/ai4s/layout/ | 2026-08-01 |
| `src/routes/_authenticated/index.tsx` | import 与组件 | Dashboard 换为 `@/ai4s/pages/dashboard/Ai4sDashboard` | issue #11 C 结构高密度看板 + 右侧信息面板；上游 Dashboard 保留于 features/dashboard 未动 | 2026-08-01 |
| `src/components/layout/app-header.tsx` | 布局左区 | 删除 SidebarTrigger（无侧边栏后的死按钮）及其 import | issue #11 配套清理 | 2026-08-01 |
| `src/lib/i18n.ts` | 顶部 +1 行 import、zhTranslation merge 参数 +1 | 引入 `src/ai4s/locales/zh-CN/ai4s-patch.json` 补键包（61 个 zh-CN 缺失键：profile/security/common 等） | issue #12 中文补键；补键包本体在隔离区 | 2026-08-02 |
| `src/routes/_authenticated/project/requests/index.tsx` | import 与组件 | 审计日志换 `@/ai4s/pages/requests/Ai4sRequestsPage`（元数据抽屉 + warn 提示条，不展示原文） | issue #13 审计原则 | 2026-08-02 |
| `src/routes/_authenticated/project/requests/$requestId.tsx` | 全文 | 深链统一 redirect 回 `/project/requests`（上游详情页含原文，禁用） | issue #13 配套 | 2026-08-02 |
| `src/routes/_authenticated/prompt-protection-rules/index.tsx` | import 与组件 | 脱敏规则换 `@/ai4s/pages/rules/Ai4sRulesPage`（link-ai 式，类型/优先级展示层派生） | issue #13 | 2026-08-02 |
| `src/features/settings/appearance/appearance-form.tsx` | colorScheme 字段 | 移除 colorScheme 选择器（9 个上游 scheme 与 ai4s 定稿主题冲突；tokens.css 已全类覆盖，明暗设置与显示强制一致） | 配色设置与显示不符 bug（2026-08-02 用户反馈） | 2026-08-02 |
| `src/routes/_authenticated/index.tsx` | import 与组件 | **恢复上游原生 dashboard**（M4 换皮撤销；信息全量，C×W 主题经 token 层生效） | 用户反馈：信息不应缩减（2026-08-02） | 2026-08-02 |
| `src/routes/_authenticated/project/requests/index.tsx` + `$requestId.tsx` | import 与组件 | **恢复上游原生审计日志与详情**（M7/M8 撤销；用户拍板"审计日志不应该拿走"） | 同上 | 2026-08-02 |
| `src/components/layout/app-header.tsx` | 右侧控件区末尾 + import 1 行 | 挂载 `Ai4sUserMenu`（顶栏用户菜单：账户信息/个人资料/退出登录） | C 结构移除侧边栏后上游 NavUser 失挂载，用户无退出入口（2026-08-03 用户反馈）；组件本体在 `src/ai4s/components/` | 2026-08-03 |
