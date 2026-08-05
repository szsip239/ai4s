# ai4s 阶段 0 部署：全链路 tracer bullet

链路：员工 curl/CLI → **agentgateway**（:3000，入口反代/DLP 执行点）→ **axonhub**（:8090，控制面）→ Claude/Codex OAuth 订阅上游。

## 组件与版本（pin 定，升级走评审）

| 组件 | 镜像 | 说明 |
|---|---|---|
| agentgateway | `cr.agentgateway.dev/agentgateway:v1.4.1`（digest `sha256:efd79355…`） | 最新稳定版（2026-07-29 发布） |
| axonhub | `looplj/axonhub:v1.0.0-beta6`（digest `sha256:d41f3ca1…`） | pin 定 beta，不跟 latest/unstable |
| PostgreSQL | `postgres:16-alpine`（digest `sha256:57c72fd2…`，实为 16.14） | axonhub 官方 compose 同款主版本 |
| casdoor | `casbin/casdoor:3.133.0` | SSO 枢纽（issue #14）：飞书 OAuth → 标准 OIDC |
| shim | 本地构建 `../shim`（python:3.12-alpine） | DLP 词表/PII 适配 + 飞书告警适配 `/feishu-alert`（issue #17） |
| alert-poller | 本地构建 `../alert-poller`（python:3.12-alpine） | 告警巡检（issue #17）：DLP fail-open 探活 + 上游/员工额度轮询，30s 间隔 |
| mock-upstream（可选） | `python:3.12-alpine` | 仅无 OAuth 凭据时验证链路用 |

## 快速开始

```bash
cp .env.example .env       # 填 DB_PASSWORD 与管理面账号；OAuth 凭据可后补
docker compose up -d
./scripts/bootstrap.sh     # 初始化管理账号 + 渠道 + 测试 API key（幂等）
./scripts/smoke-test.sh    # curl 经 agentgateway 完成一次 chat completion
python3 scripts/apply-pricing.py  # credit 价格表落库（pricing.json：官方原价×渠道倍率，issue #18）
./scripts/assign-default-project.sh  # JIT 新员工补进 Default 项目（幂等，可加 cron）
python3 scripts/dlp-regression.py    # DLP 对抗回归（issue #20）：改词表/规则后必跑
python3 scripts/edm-add.py <文件>    # EDM 商密文档指纹入库（issue #29，指纹库 gitignored）
```

- 管理面：http://localhost:8090 ，用 `.env` 中的 `AXONHUB_ADMIN_EMAIL` / `AXONHUB_ADMIN_PASSWORD` 登录（本地账号；阶段 1 切 飞书 OAuth→Casdoor→OIDC）。
- 员工入口：`http://localhost:3000/v1`（OpenAI 兼容），唯一对员工的端口。
- SSO（issue #14 已上线）：员工在 http://localhost:3000/sign-in 点"Casdoor SSO（飞书）"登录，JIT 自动建号。axonhub 无 JIT 默认项目机制，首登后运行 `./scripts/assign-default-project.sh`（幂等，可加 cron）把新员工补进 Default 项目。

## OAuth 渠道凭据

`bootstrap.sh` 按 `.env` 自动选择渠道形态：

- `CODEX_OAUTH_JSON` 有值 → 建 `codex` 类型 OAuth 渠道。凭据取自本机 `codex login` 后的 `~/.codex/auth.json`（整个 JSON 一行）。
- `CLAUDECODE_OAUTH_JSON` 有值 → 建 `claudecode` 类型 OAuth 渠道。凭据取自 `~/.claude/.credentials.json`。
- 两者都为空 → 自动拉起 `mock-upstream`（compose `mock` profile）并建占位渠道，先验证 agentgateway→axonhub→上游 链路可达；拿到凭据后填 `.env` 重跑 `bootstrap.sh` 即可换真实渠道。

也可在管理面手工配置：Channels → New Channel → 选 codex/claudecode → 走页面 OAuth 流程（`/admin/codex/oauth/start` 同款）。

## PostgreSQL 日备

```bash
./scripts/pg-backup.sh   # pg_dump → backups/axonhub-<时间戳>.sql.gz，保留最近 14 份
```

crontab 每日 03:00：`0 3 * * * cd <repo>/deploy && ./scripts/pg-backup.sh >> backups/cron.log 2>&1`

恢复参考：`gunzip -c backups/<文件>.sql.gz | docker compose exec -T postgres psql -U axonhub -d axonhub`

## 密钥纪律

- 所有口令/OAuth 凭据只放 `deploy/.env`（已 gitignore）；`.env.example` 仅占位。
- `deploy/.local/`（JWT、测试 API key）与 `deploy/backups/` 均已 gitignore。

## 停启

```bash
docker compose down        # 停容器，数据卷保留
docker compose down -v     # 连数据卷一起删（慎用）
```
