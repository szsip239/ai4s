# AI4S 看板插件

`@ai4s/dsh-widget` 在 Mosaic 注册 Launcher `ai4s.dashboard-launcher` 和应用页 `ai4s.dashboard`。卡片的「打开应用页」通过 Mosaic 打开插件指定的 AI4S Web 内网 HTTPS 入口。Launcher 事件只携带空对象，页面地址取自插件客户端的受控配置。

升级到 0.2.0 后，在看板第 2 屏把旧卡 `ai4s.dashboard` 从原位置移除，并从组件库添加「AI4S 调用统计」到该位置。新卡会自行建立读取绑定，按最新的本机快照填充。日常桌面的部署另行授权。

## 在 dsh-dev 复验

环境使用 Node 24 和 Mosaic Host 0.1.31。可从本仓 DSH widget Actions 下载对应提交 SHA 的候选包，或在检出同一提交后运行。

```sh
npm --prefix packages/dsh-widget test
npm pack ./packages/dsh-widget --pack-destination .
```

选择已经加载 Mosaic Host 的隔离开发 profile，安装候选包。

```sh
dsh-dev plugin --profile "$profile" add ./ai4s-dsh-widget-0.2.0.tgz
```

在该 profile 的 `cordis.patch.yml` 加入下列配置。用仓库内的 [snapshot.json](fixtures/snapshot.json) 可复验跨日、未知费用和异常渠道。使用同一结构的当日虚构快照可以复验正常展示。

```yaml
- insert:
    - id: ai4s-dashboard
      name: '@ai4s/dsh-widget'
      config:
        snapshotPath: /absolute/path/to/snapshot.json
```

在未占用的端口启动隔离 profile。

```sh
dsh-dev --profile "$profile" --host 127.0.0.1 --port 3399 --no-open
```

打开命令打印的 Web UI，在 Mosaic 看板添加「AI4S 调用统计」。在亮色、暗色和不同窗口宽度下检查卡片。打开看板隐私模式可核对 Key 名遮蔽。更换或移走虚构快照可复验读取更新和上次数据。应用页嵌入使用 AI4S Web 允许的 DSH 宿主 origin，端口以实际放行配置为准。

本机安装 Ego Browser 后，可运行 `npm --prefix packages/dsh-widget run test:layout` 验证窄卡布局。150 CSS px 及以下的卡片优先显示渠道指标和失败列表，超出空间的条目以 `+N` 汇总。常用模型只在卡片宽度 100 CSS px 及以下隐藏。页面按钮固定在卡片底部，内容高度不足时可单独滚动。

## 刷新和展示口径

- 新卡添加后读取一次，之后由 Host 每 600 秒读取。可在添加卡片前设置 `readIntervalSeconds: 5` 加速复验。
- 渠道总数包括全部未归档渠道。可用数为其中启用的渠道，包括零流量渠道。近期失败率覆盖快照统计日截至采集时的尝试，以各渠道已测得成功率的尝试数加权。没有可计算的样本时显示「—」。
- 有失败的渠道按失败率从高到低列出，其中已停用的标注「已停用」。收件箱的待处理规则沿用原卡，收录所有停用渠道，以及至少 `minAttempts` 次且成功率低于 `minSuccessRatePct` 的渠道。两项默认值分别是 10 和 90。
- `day` 按 Asia/Shanghai 判断「今日」，跨日显示快照的统计日。标题时间采用上海时间。上次成功采集满 1200 秒显示「数据未更新」。缺失、损坏或不可读时显示错误，并复用 Host 保存的上次成功值（如有）。
- 费用使用快照币种，`cost: null` 显示「未知」。模型按调用数、Key 按费用各取前三。隐私模式下隐藏 Key 显示名。
- 服务端只读取配置的快照文件。卡片接收汇总和状态，文件路径及读取错误原文留在服务端。
