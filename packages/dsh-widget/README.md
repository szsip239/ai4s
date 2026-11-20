# AI4S 看板插件

`@ai4s/dsh-widget` 通过 Mosaic Widget Library 注册 `ai4s.dashboard`。Host 调用服务端的只读 Capability，将结果经 Automation Binding 投递到 `/main`。卡片通过 Runtime Bridge 接收 Data、主题和隐私模式，并用普通链接打开 Placement Inspector 中配置的控制台地址。

## 在 dsh-dev 复验

环境使用 Node 24 和 Mosaic Host 0.1.31。可从本仓 DSH widget Actions 下载对应提交 SHA 的候选包，或在检出同一提交后运行：

```sh
npm --prefix packages/dsh-widget test
npm pack ./packages/dsh-widget --pack-destination .
```

选择已经加载 Mosaic Host 的隔离开发 profile，安装候选包：

```sh
dsh-dev plugin --profile "$profile" add ./ai4s-dsh-widget-0.1.0.tgz
```

在该 profile 的 `cordis.patch.yml` 加入以下配置，将 `snapshotPath` 替换成虚构快照的绝对路径。用仓库内 [snapshot.json](fixtures/snapshot.json) 可复验跨日、未知费用和异常渠道。也可用同一结构的当日虚构快照复验正常展示。

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

打开命令打印的 Web UI，在 Mosaic 看板添加「AI4S」。在卡片设置中填写控制台 HTTP(S) 地址。打开看板隐私模式可核对 Key 名遮蔽，切换浅色、深色并调整窗口宽度可核对 4×8 卡片的适配。先换成另一份成功快照验证下一次读取，再临时移走或损坏开发 fixture，最后恢复成功文件。真实数据与系统浏览器打开控制台留待获准部署后的官方 App 验收。

## 刷新和展示口径

- 新卡片首次显示时请求一次读取。Host 按每张卡的 Automation 周期在后台读取，默认 600 秒。添加卡片前可在隔离 profile 设 `readIntervalSeconds: 5` 加速验收。已有卡片的 Automation 由 Host 保存。
- `day` 是上游统计日，以 Asia/Shanghai 的日期判断「今日」。跨日时显示快照的统计日。标题时间是 `asOf` 转成上海时间。每次读取判定新鲜度，距上次成功采集满 1200 秒即标记「数据未更新」，与开发 profile 的加速读取设置无关。
- 缺失、损坏或不可读只发布固定错误状态。存在上次成功值时复用 Host 保存的 Data 和 `asOf`，包括重启后。首次读取失败时显示原因，成功读取后恢复正常显示。
- 币种来自快照 `currency`，格式按该币种的小数位显示。文件的 `cost: null` 映射成 Data 中缺省的费用，卡面显示「未知」。零值原样保留。无成功率的渠道不列入测量行，停用的渠道仍列入异常。
- 只有停用的渠道，或至少 `minAttempts` 次尝试且成功率低于 `minSuccessRatePct` 的渠道进入 `summary.pending`。两项默认值分别是 10 和 90。读取错误与过期提示沿用上次已知渠道状态。模型按调用数，Key 按费用，各取前三。Key 显示名声明为身份字段，隐私模式下卡片显示「＊＊＊」。
- 服务端只读取配置的快照文件。Data 只发布约定的汇总字段和卡片状态，Widget 没有网络权限。文件路径和错误原文留在服务端，插件无业务数据日志。
