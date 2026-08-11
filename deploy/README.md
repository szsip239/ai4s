# ai4s 阶段 0 部署：全链路 tracer bullet

链路：员工 curl/CLI → **agentgateway**（:3000，入口反代/DLP 执行点）→ **axonhub**（:8090，控制面）→ Claude/Codex OAuth 订阅上游。

## 组件与版本（pin 定，升级走评审）

| 组件 | 镜像 | 说明 |
|---|---|---|
| agentgateway | `cr.agentgateway.dev/agentgateway:v1.4.1`（digest `sha256:efd79355…`） | 最新稳定版（2026-07-29 发布） |
| axonhub | `looplj/axonhub:v1.0.0-beta6`（digest `sha256:d41f3ca1…`） | pin 定 beta，不跟 latest/unstable |
| PostgreSQL | `postgres:16-alpine`（digest `sha256:57c72fd2…`，实为 16.14） | axonhub 官方 compose 同款主版本 |
| casdoor | `casbin/casdoor:3.133.0` | SSO 枢纽（issue #14）：飞书 OAuth → 标准 OIDC |
| shim | 本地构建 `../shim`（python:3.12-slim + apt tesseract-ocr/chi-sim/eng（issue #50 OCR，apt 层 +109MB、镜像总 526MB，2026-08 实测）；pip pin PyMuPDF/python-docx/openpyxl/python-pptx/pytesseract/Pillow） | DLP 词表/PII 适配 + 飞书告警适配 `/feishu-alert`（issue #17）+ 统一配置 admin 平面 `/dlp-admin/*`（issue #31–#36）+ 告警巡检 daemon 线程（issue #56 并入原 alert-poller：fail-open 探活/渠道与 key 额度轮询/提额审批同步，30s，与检测路径隔离） |
| mock-upstream（可选） | `python:3.12-alpine` | 仅无 OAuth 凭据时验证链路用 |

## 快速开始

```bash
cp .env.example .env       # 填 DB_PASSWORD 与管理面账号；OAuth 凭据可后补
docker compose up -d
./scripts/bootstrap.sh     # 初始化管理账号 + 渠道 + 测试 API key（幂等）
./scripts/smoke-test.sh    # curl 经 agentgateway 完成一次 chat completion
python3 scripts/apply-pricing.py  # credit 价格表落库（pricing.json：官方原价×渠道倍率，issue #18）
./scripts/assign-default-project.sh  # JIT 新员工补进 Default 项目（幂等，可加 cron）
python3 scripts/dlp-regression.py    # DLP 对抗回归（issue #20）：改词表/规则后必跑（含 EDM 段与 admin API 段）
python3 scripts/dlp-capability.py    # DLP 能力水位（issue #42）：词表/规则调优后与回归一起跑；gap 不 fail，负例误伤/开关矩阵失败才非零（公共部分在 dlp_testkit.py）
python3 scripts/edm-add.py <文件>    # EDM 商密文档指纹入库（issue #34 起为 admin API 薄壳，凭据见下）
cd ../shim && python3 -m unittest discover -s tests    # shim 单测；本机先在 shim/ 下 pip install -r requirements-dev.txt（EDM 解析库，issue #49）；OCR 用例另需系统 tesseract 二进制（无则对应用例自动 skip，见 requirements-dev.txt 注释，issue #50）
```

## DLP 统一配置（issue #31–#36）

- **唯一写入口 = admin API**：`/dlp-admin/*`（本机 `http://localhost:18080`，仅绑 127.0.0.1），Bearer 鉴权——token 透传 axonhub 内省，读需 `read_channels`、写需 `write_channels`（isOwner 直通）。凭据取 env `DLP_ADMIN_TOKEN`，缺省读 `deploy/.local/admin-jwt`（bootstrap 产物）。`web/`「脱敏规则」配置中心页是其前端；不直改 `dlp/`、`recognizers/`、`edm/` 下的配置文件（会绕过校验/原子写/渲染联动）。
- **edm-add.py 流程变更（issue #34）**：原直写指纹库逻辑已收编进 shim admin 平面，脚本只剩 CLI 薄壳（用法不变）：`POST /dlp-admin/edm/corpus` 入库、`DELETE /dlp-admin/edm/corpus/<name>` 移除（`--remove`）；同名重复入库 400，更新文档须先 `--remove` 再重新入库。
- **settings.json 优先于 env（issue #35）**：judge/edm/pg 开关与阈值三级取值 `deploy/dlp/settings.json` > env > 内置默认，shim 每请求重读热生效；维护走 `GET/PUT /dlp-admin/settings` 或配置中心页，`.env` 的 `JUDGE_*`/`EDM_*`/`PG_*` 仅作文件缺失时的回退层。凭据（`JUDGE_API_KEY`/`FEISHU_*`）永远只走 env，禁止写入 settings.json。
- **分层总开关（issue #40）**：settings.json 增 `l1`/`l2`/`response` 三段（单键 enabled，内置默认 true 保现网行为；env 回退层 `L1_ENABLED`/`L2_ENABLED`/`RESPONSE_ENABLED`，compose 默认透传 1）。l1 关=格式规则全族撤防（密钥拦截全敞口）且 config.yaml 标记区块联动渲染撤空；l2 关=词表/Presidio PII 整体跳过；response 关=响应侧整段放行。翻转 l1 经 `PUT /dlp-admin/settings` 自动联动渲染（失败回滚 settings 并 500）；手改 settings.json 后用 `POST /dlp-admin/format-rules/render` 兜底同步。
- **pg.normalize（issue #44）**：settings.json `pg` 段增 `normalize` 键（布尔，必填；内置默认 false 保现网行为，env 回退 `PG_NORMALIZE`）。true=PG 打分前置归一化（base64 内联解码/零宽清除/全角转半角，在 promptguard 服务内做单点，只改打分输入不改转发原文）；shim 经 `/guard` 请求体 `normalize` 字段透传。shadow/fail-open 语义不变。

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
