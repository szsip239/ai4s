# MOUNTPOINTS — 对上游文件的改动登记

> 规则见 docs/contracts/vendor-isolation.md：新增代码只进 `src/ai4s/`；改上游文件必须在此登记（文件+位置+改动+原因）；**高频变动区禁改**（provider 对接、协议转换、模型目录）。

| 文件 | 位置 | 改动 | 原因 | 日期 |
|---|---|---|---|---|
| `src/index.css` | 文件末尾 +2 行 | 追加 `@import './ai4s/theme/tokens.css'` | issue #10 设计 token 覆盖层入口（W/D 风格），同特异性后加载胜出，上游组件零修改 | 2026-08-01 |
| `package.json` / `pnpm-lock.yaml` | dependencies | 新增 `@fontsource/geist`、`@fontsource/geist-mono`（纯新增，未改上游依赖版本） | token 层字体自托管（Geist/Geist Mono），tokens.css 引用 | 2026-08-01 |
