# ai4s 阶段 0 部署：全链路 tracer bullet

链路：员工 curl/CLI → **agentgateway**（:3000，入口反代/DLP 执行点）→ **axonhub**（容器内网 :8090，控制面；宿主不暴露，issue #60）→ Claude/Codex OAuth 订阅上游。

## 组件与版本（pin 定，升级走评审）

| 组件 | 镜像 | 说明 |
|---|---|---|
| agentgateway | `cr.agentgateway.dev/agentgateway:v1.4.1`（digest `sha256:efd79355…`） | 最新稳定版（2026-07-29 发布） |
| axonhub | `looplj/axonhub:v1.0.0-beta6`（digest `sha256:d41f3ca1…`） | pin 定 beta，不跟 latest/unstable |
| PostgreSQL | `postgres:16-alpine`（digest `sha256:57c72fd2…`，实为 16.14） | axonhub 官方 compose 同款主版本 |
| casdoor | `casbin/casdoor:3.133.0` | SSO 枢纽（issue #14）：飞书 OAuth → 标准 OIDC |
| shim | 本地构建 `../shim`（python:3.12-slim + apt tesseract-ocr/chi-sim/eng（issue #50 OCR，apt 层 +109MB）；pip pin PyMuPDF/python-docx/openpyxl/python-pptx/pytesseract/Pillow + onnxruntime/transformers/numpy（issue #67 PG 进程内推理，版本 pin 自原 promptguard 容器实测；镜像总 900MB，2026-08-19 实测）+ psycopg（issue #72 key 归属 SQL 直改，函数级懒加载）） | DLP 词表/PII 适配 + PromptGuard 2 注入检测引擎 `pg_engine`（issue #67 并入进程内，原 promptguard 容器退役；函数级懒加载，pg.enabled=false 时零加载零开销；模型卷 `./.local/promptguard-model:/models/promptguard:ro` + `HF_HUB_OFFLINE=1`）+ 飞书告警适配 `/feishu-alert`（issue #17）+ 统一配置 admin 平面 `/dlp-admin/*`（issue #31–#36）+ 告警巡检 daemon 线程（issue #56 并入原 alert-poller：fail-open 探活/渠道与 key 额度轮询/审批同步 30s，与检测路径隔离；issue #19 提额 + issue #72 新建 Key 审批并存，新建通过→自动建 key 归申请人→挂体验档→私信交付明文） |
| mock-upstream（可选） | `python:3.12-alpine` | 仅无 OAuth 凭据时验证链路用 |

## 快速开始

```bash
cp .env.example .env       # 填 DB_PASSWORD 与管理面账号；OAuth 凭据可后补
docker compose up -d
./scripts/bootstrap.sh     # 初始化管理账号 + 渠道 + 测试 API key（幂等）
./scripts/smoke-test.sh    # curl 经 agentgateway 完成一次 chat completion
python3 scripts/apply-pricing.py  # credit 价格表落库（pricing.json：官方原价×渠道倍率，issue #18）
./scripts/assign-default-project.sh  # JIT 新员工补进 Default 项目（幂等；issue #73 起常规路径已由 shim 巡检自动覆盖，本脚本为手工兜底）
python3 scripts/dlp-regression.py    # DLP 对抗回归（issue #20）：改词表/规则后必跑（含 EDM 段与 admin API 段）
python3 scripts/dlp-capability.py    # DLP 能力水位（issue #42）：词表/规则调优后与回归一起跑；gap 不 fail，负例误伤/开关矩阵失败才非零（公共部分在 dlp_testkit.py）
python3 scripts/edm-add.py <文件>    # EDM 商密文档指纹入库（issue #34 起为 admin API 薄壳，凭据见下）
cd ../shim && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m unittest discover -s tests    # shim 单测（本机 venv 隔离装 EDM 解析库，issue #49）；OCR 用例另需系统 tesseract 二进制（无则对应用例自动 skip，见 requirements-dev.txt 注释，issue #50）
```

## DLP 统一配置（issue #31–#36）

- **唯一写入口 = admin API**：`/dlp-admin/*`（本机 `http://localhost:18080`，仅绑 127.0.0.1），Bearer 鉴权——token 透传 axonhub 内省，读需 `read_channels`、写需 `write_channels`（isOwner 直通）。凭据取 env `DLP_ADMIN_TOKEN`，缺省读 `deploy/.local/admin-jwt`（bootstrap 产物）。`web/`「脱敏规则」配置中心页是其前端；不直改 `dlp/`、`recognizers/`、`edm/` 下的配置文件（会绕过校验/原子写/渲染联动）。
- **edm-add.py 流程变更（issue #34）**：原直写指纹库逻辑已收编进 shim admin 平面，脚本只剩 CLI 薄壳（用法不变）：`POST /dlp-admin/edm/corpus` 入库、`DELETE /dlp-admin/edm/corpus/<name>` 移除（`--remove`）；同名重复入库 400，更新文档须先 `--remove` 再重新入库。
- **settings.json 优先于 env（issue #35）**：judge/edm/pg 开关与阈值三级取值 `deploy/dlp/settings.json` > env > 内置默认，shim 每请求重读热生效；维护走 `GET/PUT /dlp-admin/settings` 或配置中心页，`.env` 的 `JUDGE_*`/`EDM_*`/`PG_*` 仅作文件缺失时的回退层。凭据（`JUDGE_API_KEY`/`FEISHU_*`）永远只走 env，禁止写入 settings.json。
- **分层总开关（issue #40）**：settings.json 增 `l1`/`l2`/`response` 三段（单键 enabled，内置默认 true 保现网行为；env 回退层 `L1_ENABLED`/`L2_ENABLED`/`RESPONSE_ENABLED`，compose 默认透传 1）。l1 关=格式规则全族撤防（密钥拦截全敞口）且 config.yaml 标记区块联动渲染撤空；l2 关=词表/Presidio PII 整体跳过；response 关=响应侧整段放行。翻转 l1 经 `PUT /dlp-admin/settings` 自动联动渲染（失败回滚 settings 并 500）；手改 settings.json 后用 `POST /dlp-admin/format-rules/render` 兜底同步。
- **pg.normalize（issue #44）**：settings.json `pg` 段增 `normalize` 键（布尔，必填；内置默认 false 保现网行为，env 回退 `PG_NORMALIZE`）。true=PG 打分前置归一化（base64 内联解码/零宽清除/全角转半角，只改打分输入不改转发原文）；issue #67 起为 shim 进程内单点（`pg_engine.normalize_for_scoring`），原 promptguard 服务 `/guard` 透传随 PG_URL 一并退役。shadow/fail-open 语义不变。

- 管理面：http://localhost:3000 （经 agentgateway 反代；宿主不再单独暴露 axonhub 调试口，issue #60），用 `.env` 中的 `AXONHUB_ADMIN_EMAIL` / `AXONHUB_ADMIN_PASSWORD` 登录（本地账号；阶段 1 切 飞书 OAuth→Casdoor→OIDC）。
- 员工入口：`http://localhost:3000/v1`（OpenAI 兼容），唯一对员工的端口。
- SSO（issue #14 已上线）：员工在 http://localhost:3000/sign-in 点"Casdoor SSO（飞书）"登录，JIT 自动建号。axonhub 无 JIT 默认项目机制；**issue #73 起 shim 巡检线程 30s 级自动把新员工补进 Default 项目**（`auto_assign_project`，入项发飞书群通知），手工兜底 `./scripts/assign-default-project.sh`（幂等）。
- **公网访问（2026-08-22 起，替代 tailnet serve）**：宿主 `local-edge-nginx` 发布两条 example.com HTTPS 入口（模板 `sibling-project/.deploy/nginx-consolidation/local/templates/ai4s.conf.template`；iKuai dnat id=22/23 对齐 18999 模式）：
  - console+API：`https://example.com:8445`（→ host.docker.internal:3000；本机 localhost:3000 入口不受影响）
  - Casdoor：`https://example.com:8444`（→ host.docker.internal:8000）
  - tailnet serve 8444/8445 已取消（8443/9443 属其他服务，保留）。
  - SSO 规范名：`axonhub/config.yml` 的 public_url/redirect_url=example.com:8445、issuer_url=example.com:8444，`casdoor/app.conf` origin=example.com:8444（issuer 与 origin 必须一致）。
  - **前置依赖（飞书后台手工项）**：飞书开放平台应用（cli_xxxxxxxxxxxxxxxx）→ 安全设置 → 重定向 URL 须含 `https://example.com:8444/callback`，否则 SSO 最后一步报错误码 20029。
  - Casdoor 应用的 `redirect_uris` 追加项（含 :8445 回调）是运行时 DB 配置，`casdoor_data` volume 重建后需经 `/api/update-application` 重设（同上方 display_name 的恢复套路）。
  - **issuer 变更需迁移身份链接**（2026-08-22 已从 ts.net issuer 迁移到 example.com）：axonhub `oidc_identities` 按 (issuer, subject) 匹配既有用户；issuer 换名后旧链接失配，JIT 会撞邮箱唯一约束（`user_email_deleted_at` 23505）。恢复套路：`UPDATE oidc_identities SET issuer='<新 issuer>' WHERE subject='<sub>';`。
- **Casdoor 展示名（issue #58/#59）**：组织/应用的 `display_name`（当前均为 `Ai-4S-infra`）是运行时 DB 配置，`casdoor_data` volume 重建后会回退初始值，需手工重设：

  ```bash
  docker exec ai4s-postgres psql -U casdoor -d casdoor -c \
    "UPDATE application SET display_name='Ai-4S-infra' WHERE owner='admin' AND name='ai4s'; \
     UPDATE organization SET display_name='Ai-4S-infra' WHERE owner='admin' AND name='ai4s';"
  docker restart ai4s-casdoor
  ```

  只改 display_name；组织/应用的机器名（`ai4s`）与 Casdoor 产品名/域名不动。

## OAuth 渠道凭据

`bootstrap.sh` 按 `.env` 自动选择渠道形态：

- `CODEX_OAUTH_JSON` 有值 → 建 `codex` 类型 OAuth 渠道。凭据取自本机 `codex login` 后的 `~/.codex/auth.json`（整个 JSON 一行）。
- `CLAUDECODE_OAUTH_JSON` 有值 → 建 `claudecode` 类型 OAuth 渠道。凭据取自 `~/.claude/.credentials.json`。
- 两者都为空 → 自动拉起 `mock-upstream`（compose `mock` profile）并建占位渠道，先验证 agentgateway→axonhub→上游 链路可达；拿到凭据后填 `.env` 重跑 `bootstrap.sh` 即可换真实渠道。

也可在管理面手工配置：Channels → New Channel → 选 codex/claudecode → 走页面 OAuth 流程（`/admin/codex/oauth/start` 同款）。

## Key 限额档（issue #18/#19/#64）

- **模板语义**：限额档模板（apiKeyProfileTemplates：体验档/标准档/高档…）在 key 创建时**拷贝快照而非引用**——模板=新发默认档，改模板不回溯存量 key；存量调档走批量操作（控制台 key 管理页「批量换档」：项目/员工/当前档筛选 → 预览命中 → 逐条执行回报）或 issue #19 提额审批路径（shim alert_poller 同款换档语义）。
- **换档=profiles 整体替换**：批量换档与提额审批换档都以目标档模板**整体覆盖** key 的 profiles——key 原 profile 上的 channelIDs/modelIDs 等约束会被抹掉（与 shim alert_poller apply_tier 同语义）；带定制约束的 key 换档后需按需重建约束。
- **「项目≈部门」约定**：部门维度不靠 axonhub 新增实体，一部一项目承载；按部门调档=按项目筛选换档。告警轮询（issue #18 `apiKeyQuotaUsages`）按 key 当前档配额计算，换档后自动按新档生效。

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
