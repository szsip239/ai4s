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

### 统一配置

**统一配置中心（Unified Config Center）**:
DLP 全部配置面（词表/识别器/格式规则/EDM 语料/开关阈值）的单一维护入口：shim admin API `/dlp-admin/*` 为权威写入口，web「脱敏规则」页是其前端。
_Avoid_: 配置文件直改、env 调参

**admin 平面（Admin Plane）**:
shim 内与检测路径完全隔离的 `/dlp-admin/*` 管理面：Bearer token 经 axonhub me 内省鉴权（读 read_channels / 写 write_channels scope），fail-closed（内省不可达 503），不适用检测链 fail-open 分级。
_Avoid_: 管理接口、后台 API

**单一源（Single Source of Truth）**:
每类配置只有一个权威存储（词表/规则/settings JSON、EDM 指纹库），渲染产物（agentgateway config.yaml 标记区块）由它派生；EDM 入库与检测同一算法（`shim/edm_lib.py`）亦属此纪律。
_Avoid_: 双写、多处维护

### 额度与计价

**credit（点）**:
额度计价单位。1 credit = $1 官方原价消耗；消耗速率 = 官方原价 × 渠道倍率（倍率为管理员配置项，无默认值）。
_Avoid_: 额度单位、积分

**档（Tier）**:
挂在项目上的额度 Profile（体验档 3 点 / 标准档 20 点 / 高档 80 点，每自然月）。员工 Key 的额度 = 其项目对应的档。

### 身份

**JIT 用户（JIT User）**:
飞书 SSO 首登时 axonhub 自动创建的账号，email 形如 `ou_<open_id>@casdoor.oidc`；open_id 是其与飞书通讯录的唯一关联键。

**员工能力档（Employee Posture）**:
issue #68（2026-08-20）定稿：JIT 系统档为空，能力一律走项目级 `user_projects.scopes`，员工仅持 `read_requests, write_requests`（观测 + playground/请求写入 + 自己的 settings）。员工**不自助建/管 Key**——上游项目级 `read_api_keys`/`write_api_keys` 无属主过滤（明文横读、自助提额同源同闸），Key 由管理员签发、提额走飞书审批。issue #70（2026-08-20）：项目级种子角色 Developer/Viewer 删除（含 read_users/read_api_keys/write_api_keys，授予即绕开 #68；实证重启不复活）；playground 渠道/模型选择器保持为空——项目级 `read_channels` 上游 resolver 不认（仍 403），系统级放开面超员工最小集。
_Avoid_: 员工基础档（旧口径，已废止）
