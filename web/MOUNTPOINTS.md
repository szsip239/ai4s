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
