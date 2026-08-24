# ai4s

企业 AI 接入及安全网关（员工统一、安全、可治理的 LLM API 出口）。本文件只收项目特有的领域术语。

## Language

### DLP 分层

**全局层（Global Layer）**:
管理员持有的强制脱敏/阻断规则，对所有员工与项目生效，无例外。含 agentgateway L1 Secrets regex、shim 商密词表、PII recognizer。
_Avoid_: 管理员规则、公司规则

**个人层（Personal Layer）**:
员工自写、仅对本人请求生效的脱敏规则。2026-08-03 评审后**明确不建**（员工无动力自配，参考项目仅 link-ai 一家且疑似闲置功能）。
_Avoid_: 用户规则、user scope 规则

**组织差异化（Org Differentiation）**:
按部门/项目适配不同严格度的词表。暂缓建设——飞书租户无子部门，无真实场景；有部门需求时再立项。

**可逆脱敏（Reversible Masking）**:
请求打码时留映射、响应还原为原文，员工看到完整内容而上游只见掩码。当前全部为**不可逆**固定替换词；可逆评估挂账待 #23。
_Avoid_: 脱敏还原、映射恢复

### 语义层

**语义层（Semantic Layer）**:
DLP 检测链中 shim 内嵌 LLM judge 的一层，负责词表层覆盖不了的商密语义（谐音/拼音/拆字/暗示性指代）。路线已定案为外部 API（ADR-0006，supersede ADR-0002 的 LangExtract/本地 Ollama 描述）；本地/内网路线（Ollama 1.5b、GLiNER、zero-shot NLI）实测全部出局。
_Avoid_: LangExtract 路线、内网 7b+ 路线（均已废止）

**judge**:
语义层的 LLM 判定器（shim 内 `judge_text`），现网 gpt-5.6-luna 经 axonhub 中转；响应定稿后异步判定，只记 verdict 不含原文。action 四档：off / shadow / warn / reject。issue #105 起兼**注入判定第二职责**（#100 路线③生产落点）：专用注入 prompt（`judge.inject_prompt_system/inject_prompt_fewshot`，单一源=settings.json，原文直用不过 .format）+ `judge.inject_enabled` 开关（默认 false 进场 shadow），与商密判定同一次采样/并发预算门槛内第二次调用（judge API 总预算语义，调用量 ×2），结果落 shadow_log 独立层 `judge_inject`（带 attack_type 类型标签）——永不阻断、永不 warn（告警走规则层/PG 既有通道），观测价值只在 shadow 水位统计。直测通道：`/judge-test` 带 `duty="inject"`。

**shadow（影子模式）**:
judge 默认档：照常判定、照常记录，对链路零动作。判定持久化 `alert-state/shadow-verdicts.jsonl`。

**warn（告警试点档）**:
judge action 第三档（issue #101）：confidential 且 confidence ≥ `judge.threshold` 时落 `warned=True` 条，alert_poller 巡检项 6 游标消费发飞书（卡片字段全脱敏；项目字段因链路无项目标识恒为「未知（请求链路无项目标识）」），**只告警不拦截**。观察期终态的前奏形态。

**永不阻断（Never-block Discipline）**:
语义层纪律（ADR-0006 维持结论）：judge 延迟秒级且误判不可归零，只能 shadow/warn/异步审计，永不阻断链路；reject 档在 schema 存在但不消费（按 shadow 处理，UI 灰置）。

**观测闭环（Observability Loop）**:
shadow 层判定的三件套（issue #92）：持久化（shadow-verdicts.jsonl）+ judge 可用率巡检 + 查询/误报对账出口（`/dlp-admin/shadow-verdicts`）。

**注入规则层（Injection Rules Layer）**:
#100 路线② 的生产落点（issue #104）：shim 内嵌 `inject_rules.rule_match`（纯 stdlib 正则，µs 级），`/request` 词表/EDM 451 之后、PG 阻断段之前同步判定；16 个语义模式组覆盖 PG 中文盲区（提取系统提示词/覆盖指令/虚假授权/情感操纵/分隔符伪装，中英日韩 + 拼音变体），并闭合 nested_encoding（迭代 base64 解码探针，深度 2）与 invisible（不可见字符扩充清除）盲区。布尔命中无分数：`rules.enabled`=shadow 只记不拦（shadow_log 层名 `rules`，落条只存命中模式组名），`rules.block`=命中即 451（code `rules.injection`）；默认双关，先进场 shadow 观察。

**前置脱敏（Pre-egress Masking）**:
judge 外发硬性纪律（issue #93）：判定输入一律取 L1/L2 掩码后文本（masked_msgs），secret/PII 原文不进 judge prompt；已收敛为部署 checklist 硬性项。
_Avoid_: 原文外发

### 统一配置

**统一配置中心（Unified Config Center）**:
DLP 全部配置面（词表/识别器/格式规则/EDM 语料/开关阈值）的单一维护入口：shim admin API `/dlp-admin/*` 为权威写入口，web「脱敏规则」页是其前端。
_Avoid_: 配置文件直改、env 调参

**admin 平面（Admin Plane）**:
shim 内与检测路径完全隔离的 `/dlp-admin/*` 管理面：Bearer token 经 axonhub me 内省鉴权（读 read_channels / 写 write_channels scope），fail-closed（内省不可达 503），不适用检测链 fail-open 分级。
_Avoid_: 管理接口、后台 API

**self 平面（Self Plane）**:
shim 内员工自助面 `/self/*`（issue #74 `GET /self/keys` 查本人 Key；issue #79 `GET/POST /self/key-requests` 控制台发起新建/提额申请与查本人申请状态；issue #80 `POST /self/key-requests/<id>/cancel` 撤回本人 pending 申请）：与 admin 平面同款内省鉴权但无 scope 门槛（任何有效登录用户），服务端按本人身份过滤，响应白名单塑形；issue #81 起 `/self/keys` 对本人下发 key 明文（唯一闸门=服务端 userID=me.id 过滤，他人/未登录拿不到）。
_Avoid_: 员工 API、自助接口

**单一源（Single Source of Truth）**:
每类配置只有一个权威存储（词表/规则/settings JSON、EDM 指纹库），渲染产物（agentgateway config.yaml 标记区块）由它派生；EDM 入库与检测同一算法（`shim/edm_lib.py`）亦属此纪律。
_Avoid_: 双写、多处维护

### 额度与计价

**credit（点）**:
额度计价单位。1 credit = $1 官方原价消耗；消耗速率 = 官方原价 × 渠道倍率（倍率为管理员配置项，无默认值）。
_Avoid_: 额度单位、积分

**档（Tier）**:
挂在项目上的额度 Profile（体验档 100 点 / 标准档 500 点 / 高档 2000 点，每自然月；issue #84 起）。员工 Key 的额度 = 其项目对应的档。

### 基础设施

**稳定版钉住（Stable-only Pinning）**:
axonhub 升级只考虑稳定版（GA 及以上），beta 一律不追（ADR-0005，2026-08-24 拍板；现网钉住 v1.0.0-beta6）。重评审触发条件：稳定版发布 / #88 三项上游缺陷或上游 #2281 修复 / 影响 beta6 的安全补丁。beta6→beta7 评审存档 `docs/research/2026-08-24-axonhub-beta7-review.md`。
_Avoid_: 追 latest/unstable

### 身份

**JIT 用户（JIT User）**:
飞书 SSO 首登时 axonhub 自动创建的账号，email 形如 `ou_<open_id>@casdoor.oidc`；open_id 是其与飞书通讯录的唯一关联键。

**员工能力档（Employee Posture）**:
issue #68（2026-08-20）定稿：JIT 系统档为空，能力一律走项目级 `user_projects.scopes`，员工仅持 `read_requests, write_requests`（观测 + playground/请求写入 + 自己的 settings）。员工**不自助建/管 Key**——上游项目级 `read_api_keys`/`write_api_keys` 无属主过滤（明文横读、自助提额同源同闸），Key 由管理员签发、提额走飞书审批。issue #70（2026-08-20）：项目级种子角色 Developer/Viewer 删除（含 read_users/read_api_keys/write_api_keys，授予即绕开 #68；实证重启不复活）；playground 渠道/模型选择器保持为空——项目级 `read_channels` 上游 resolver 不认（仍 403），系统级放开面超员工最小集。
_Avoid_: 员工基础档（旧口径，已废止）
