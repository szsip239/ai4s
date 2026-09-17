# ai4s

## Agent skills

### Issue tracker

Issue 以 GitHub issues 管理，操作用 `gh` CLI；远端仓库待创建（ai4s，私有）。详见 `docs/agents/issue-tracker.md`。

### Triage labels

默认五角色词表：`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`。详见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局：根目录 `CONTEXT.md` + `docs/adr/`。详见 `docs/agents/domain.md`。

### Deployment topology

ai4s 全栈运行在 **lichunmac**，本机（licbot）已卸载 Docker，无法本地起栈。
边缘 8444/8445 由 Dify 的 `docker-nginx-1` 承载，重建需三个 compose 文件。
动部署、nginx、DNS 或数据前必读 `docs/agents/deployment.md`。
