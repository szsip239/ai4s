# AI4S 看板插件

`@ai4s/dsh-widget` 在 Mosaic 注册 `ai4s.dashboard-launcher` 和应用页 `ai4s.dashboard`。悬停卡片后，右上角的 ↗ 通过 Host 打开 AI4S Web。应用页地址由插件客户端解析。

0.2.0 升至 0.3.0 时，Host 把同一位置的 Launcher 从 Widget Package `1.0.0` 切换到 `1.1.0`，保留尺寸、位置和读取绑定。升级后立即刷新一次。新添加的卡片默认占 4×6 格。

## 快照与展示

插件默认每 600 秒读取一次 Cleaner 快照。快照包括：

| 字段 | 含义 |
| --- | --- |
| `asOf`、`day` | RFC 3339 采集时间和 Asia/Shanghai 统计日期 |
| `periods.day`、`periods.week`、`periods.month` | 每组都需包含 `requests`、`attempts`、`failed`、`inputTokens`、`outputTokens`、`cachedTokens` 六个非负整数 |
| `channels[]` | `name`、`disabled`、`attempts` 和 `successRatePct`。成功率未测得时，快照传 `null` |

投递到 Host 时，`inputTokens`、`outputTokens`、`cachedTokens` 分别写为 `input`、`output`、`cached`。Host 为凭据保留含有 `token` 的持久化字段名。

- 今日、本周、本月各显示成功请求数、失败率和 Token 入／出／缓。失败率按 `failed ÷ attempts` 计算，零次尝试时显示「—」。大数使用与 AI4S Dashboard 一致的 K／M／B 单位，精确值可悬停查看。
- 渠道行按当日尝试次数排序。超过空间的活跃渠道、零次的空闲渠道和零次的停用渠道汇入虚线批注。有调用的停用渠道仍显示一行。卡片内容宽度不足 240 px 时，时段区仅显示今日。
- 启用渠道达到 `minAttempts` 且已测得的成功率低于 `minSuccessRatePct` 时进入待处理。两项默认值分别为 10 和 90。对应渠道的次数使用错误色，并附成功率批注。
- 采集时间用上海时间。跨日显示统计日期，采集满 1200 秒显示「未更新」。读失败时沿用上次可用的三时段数据。首次读取失败时显示原因。

## 在 dsh-dev 复验

使用 Node 24 和 Mosaic Host 0.1.31。候选包可从本仓 DSH widget Actions 下载，也可从相同提交打包。

```sh
npm --prefix packages/dsh-widget test
npm --prefix packages/dsh-widget run test:layout
npm pack ./packages/dsh-widget --pack-destination .
dsh-dev plugin --profile "$profile" add ./ai4s-dsh-widget-0.3.0.tgz
```

布局测试需要本机的 Ego Browser。在已加载 Mosaic Host 的隔离开发 profile 的 `cordis.patch.yml` 中指定 [虚构快照](fixtures/snapshot.json) 的绝对路径。

```yaml
- insert:
    - id: ai4s-dashboard
      name: '@ai4s/dsh-widget'
      config:
        snapshotPath: /absolute/path/to/snapshot.json
```

```sh
dsh-dev --profile "$profile" --host 127.0.0.1 --port 3399 --no-open
```

从看板组件库添加「AI4S 调用统计」。可设置 `readIntervalSeconds: 5` 加速复验。先加载虚构快照，再移走该文件即可复验读失败时保留上次数据。应用页嵌入使用 AI4S Web 允许的 DSH 宿主 origin。

内嵌的 Geist 和 Geist Mono Latin 字体遵循 [SIL Open Font License 1.1](widget/assets/fonts/OFL.txt)。
