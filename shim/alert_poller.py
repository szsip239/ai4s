#!/usr/bin/env python3
"""ai4s 告警巡检（issue #17）+ 审批同步（issue #19 提额 / issue #72 新建），issue #56 起并入 shim 后台线程。

axonhub 无事件源的事务靠主动轮询补齐。巡检项（状态翻转才发飞书，恢复也通知，状态存 /state 防抖）：
  1. DLP fail-open 探活：shim /healthz、presidio /health 任一不可达 → 告警
     （agentgateway failureMode=failOpen，组件挂掉流量静默直传，必须有人喊）；
     并入 shim 后 SHIM_URL 默认进程内自调 http://localhost:8080（issue #56）
  2. 上游渠道额度：queryChannels → providerQuotaStatus ∈ {warning, exhausted} → 告警
  3. 员工 API key 额度：apiKeys → apiKeyQuotaUsages，usage 达到 quota → 告警；≥80% → 预警（issue #18）
     issue #91 P2-3：告警/预警/恢复文本带项目名（一轮一次 myProjects 建 gid→名映射，
     名单失败回退裸 gid 不阻塞告警）
  4. shadow 层可用率（issue #92）：judge/PG 判定持久化（shadow_log.stats）窗口内
     异常率 ≥ SHADOW_ERR_RATE 且样本 ≥ SHADOW_ALERT_MIN → 告警（样本不足/无流量不判坏）
  5. PG 阻断事件（issue #103）：tail 消费 shadow_log pg 层 blocked=True 条目，发现即发卡
     （脱敏：层名/score/阻断阈值/请求模型名/时间，绝无原文无 key）——事件型告警非状态翻转，
     游标（alert-state.json pg_block_cursor=已告警条 ts）防抖，发送成功才推进、失败下轮重试；
     单轮条数封顶 PG_BLOCK_ALERT_BATCH（防阻断风暴刷屏，剩余下轮续发）
  6. judge warn 事件（issue #101）：tail 消费 shadow_log judge 层 warned=True 条目，发现即发卡
     （脱敏：项目/confidence/实体命中数/请求模型名/时间，绝无原文无 key 无实体字符串）——
     同款事件型游标模型（judge_warn_cursor），发送成功才推进、失败下轮重试；
     单轮条数封顶 JUDGE_WARN_ALERT_BATCH。warn 只告警不拦截（契约「语义层永不阻断」）

审批同步（issue #19 提额 + issue #72 新建，approval_sync 泛化为多定义并存）：
  轮询飞书审批实例（APPROVAL_QUOTA_CODE / APPROVAL_KEY_CODE 两个定义各自轮询）：
  - 提额：APPROVED → 申请人 open_id → axonhub 用户（email = ou_*@casdoor.oidc）→ 其 enabled Key
    → 按表单"目标档位"换挂对应 Profile 模板 → 群里回执。拒绝/撤回只回执/标记。
    issue #85：档位秩次 TIER_RANK（体验档<标准档<高档）单一定义点在本模块；apply_tier_to_user
    带逐 key never-downgrade 守卫（目标秩>当前秩换挂 / ==同档重挂保留运维刷新路径 / <跳过并
    在结果文本列出，绝不降档），飞书审批定义与控制台（key_requests）两通道同享；
    per-key 当前档走 USER_ENABLED_KEYS_QUERY（按 userID 精确过滤），不复用巡检共用的
    ENABLED_API_KEYS_QUERY（加字段影响面大）。issue #86：apply_tier_to_user 加 key_ids
    子集参数（控制台按 Key 勾选提档；None=全部 enabled Key，飞书通道/存量申请原语义），
    子集内审批期间被归档/删除的 Key 跳过并在结果文本列明。
    issue #89 多项目隔离：apply_tier_to_user/query_user_enabled_keys/ensure_emp_key/key_by_name
    加 project_id 参数（默认 KEY_PROJECT_ID=Default——飞书通道与存量申请零变化，其 key 均落
    Default）；控制台申请/审批按申请单记录的项目执行，不再跨项目拉齐。
  - 新建：APPROVED → open_id → axonhub 用户（无用户=未首登，回执提示先登录再重新申请）
    → 成员校验（issue #91 P2-2：申请人须仍是 Default 项目成员，非成员不建、回执说明，
    正常终态不重试，与「无用户」先例同语义）
    → createAPIKey（体验档写死，命名 emp-<oid8>-<yyyymmdd>-<用途摘要>-<ic尾4>）
    → 归属申请人（createAPIKey 无 userID 入参，v1.0.0-beta6 实证；建后 SQL 直改 user_id 并
      bump updated_at，axonhub 30s 增量刷新自动跟进缓存；归申请人是提额流按 userID 找 key 的前提）
    → 挂体验档 profile → 机器人私信申请人交付明文（im:message，2026-08 实证可用）；
    私信失败兜底=申请人到控制台「我的 Key」页查看明文（issue #81 起 /self/keys 对本人下发明文）。
    群回执只发摘要，绝不含明文。
  FEISHU_APP_ID/FEISHU_APP_SECRET 缺失或两个 code 都空即整体跳过（与独立容器时代一致）。

新用户自动入 Default 项目（issue #73，移植 assign-default-project.sh 筛选+入项逻辑）：
  axonhub 无 JIT 默认项目钩子、无用户创建事件（上游实证），靠轮询补齐——users 扫描筛
  activated/非 owner/不在 Default 项目 → addUserToProject（scopes 与脚本 SCOPES 一致）；
  幂等天然成立（已成员被筛除），单用户失败记日志下轮重试，入项成功发群通知让管理员感知。
  issue #87：Default 解析 fail-closed——只按名命中 "Default"，找不到记日志本轮跳过，
  不回退到 myProjects 首项（改名/删除时会把新用户静默加进任意项目且通知谎报 Default）。
  issue #91 P2-4：改为按 gid（KEY_PROJECT_ID）匹配 myProjects——项目改名不再静默停摆，
  缺失仍记日志本轮跳过（fail-closed 不变）；通知文本按匹配到的项目实名单发出。

发送：飞书群机器人签名校验（与 shim /feishu-alert 同算法）；axonhub 实时事件
（channel.auto_disabled）走 shim /feishu-alert 适配器，本模块不重复。
除归属步骤的 psycopg（函数级懒加载）外依赖仅标准库。secret 不进日志。

隔离纪律（issue #56）：本模块只被 app.py __main__ 以 daemon 线程启动（import app 不起线程，
单测环境安全）；轮询循环体整体 try/except，单轮异常只记日志不杀线程、绝不影响检测路径。
"""
import json
import os
import threading
import time
import urllib.request

import admin_api  # 原子写复用（issue #57 P2-2）：唯一 tmp + .bak 滚动 + finally 清理
import feishu_lib  # 飞书签名共享实现（issue #70）：app.py 同一份
import shadow_log  # shadow 判定持久化（issue #92）：stdlib-only 无环；可用率巡检消费其 stats

AXONHUB_BASE = os.environ.get("AXONHUB_BASE", "http://axonhub:8090")
ADMIN_EMAIL = os.environ.get("AXONHUB_ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("AXONHUB_ADMIN_PASSWORD", "")
FEISHU_WEBHOOK = os.environ.get("FEISHU_ALERT_WEBHOOK", "")
FEISHU_SECRET = os.environ.get("FEISHU_ALERT_SECRET", "")
# issue #56：线程在 shim 进程内，探活默认自调本进程 HTTP 栈（真实验证服务活着，无新代码路径）
SHIM_URL = os.environ.get("SHIM_URL", "http://localhost:8080")
PRESIDIO_URL = os.environ.get("PRESIDIO_URL", "http://presidio:3000")


def _env_int(name: str, default: int) -> int:
    """env 整型宽容解析（app.py setting_value 同款纪律，issue #57 P1）：非法值落 default +
    warning——模块级 int() 非法 env 会在 import 期炸掉整个 shim（检测路径全灭），import 永不抛。"""
    v = os.environ.get(name, "")
    if v == "":
        return default
    try:
        return int(v)
    except ValueError:
        print(f"[alert] {name} 非法值（期望整数），回退默认 {default}", flush=True)
        return default


POLL_INTERVAL = _env_int("POLL_INTERVAL", 30)
STATE_PATH = os.environ.get("STATE_PATH", "/state/alert-state.json")


def _env_float(name: str, default: float) -> float:
    """_env_int 同款宽容解析的浮点版（issue #92）：非法值落 default + warning，import 永不抛。"""
    v = os.environ.get(name, "")
    if v == "":
        return default
    try:
        return float(v)
    except ValueError:
        print(f"[alert] {name} 非法值（期望数值），回退默认 {default}", flush=True)
        return default


# shadow 层可用率巡检（issue #92）：窗口内异常率超阈值才判坏，样本不足不判（防小样本抖动翻转）
SHADOW_ALERT_WINDOW = _env_int("SHADOW_ALERT_WINDOW", 20)
SHADOW_ALERT_MIN = _env_int("SHADOW_ALERT_MIN_SAMPLES", 4)
SHADOW_ERR_RATE = _env_float("SHADOW_ERR_RATE", 0.5)
SHADOW_LAYER_NAMES = {"judge": "语义 judge", "pg": "注入 PG"}
# PG 阻断事件巡检（issue #103）：单轮发卡封顶（防风暴）+ tail 窗口（须覆盖两轮间积压）
PG_BLOCK_ALERT_BATCH = _env_int("PG_BLOCK_ALERT_BATCH", 10)
PG_BLOCK_ALERT_TAIL = _env_int("PG_BLOCK_ALERT_TAIL", 100)
# judge warn 事件巡检（issue #101）：同款封顶/窗口（judge 同步在响应后判定，两轮间积压同量级）
JUDGE_WARN_ALERT_BATCH = _env_int("JUDGE_WARN_ALERT_BATCH", 10)
JUDGE_WARN_ALERT_TAIL = _env_int("JUDGE_WARN_ALERT_TAIL", 100)
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
APPROVAL_QUOTA_CODE = os.environ.get("APPROVAL_QUOTA_CODE", "")
# issue #72：新建 key 审批定义 code + 归属直改所需 DSN（createAPIKey 无 userID 入参，实证）
APPROVAL_KEY_CODE = os.environ.get("APPROVAL_KEY_CODE", "")
AXONHUB_DB_DSN = os.environ.get("AXONHUB_DB_DSN", "")
# 新建 key 落在的项目（axonhub 默认项目；GID 格式与 users 查询返回一致，2026-08-20 实证）
KEY_PROJECT_ID = os.environ.get("APPROVAL_KEY_PROJECT_ID", "gid://axonhub/Project/1")

# issue #70 #6：enabled Key 列表查询单份（原 apply_tier / check_cycle 各写一遍同样的查询）。
# 约束：first:100 硬上限——查询无 projectID 过滤，全局 enabled Key 超 100 即漏判；当前规模（十余个）远不及，
# 超规模时需改 after 分页（users 侧同类上限已改 where 精确查，见 apply_tier）。
# issue #91 P2-3：节点补 projectID（告警文本带项目名用；APIKey.projectID 2026-08-23 内省实证存在）。
ENABLED_API_KEYS_QUERY = (
    "query { apiKeys(first: 100, where: {statusIn: [enabled]}) { edges { node { id name userID projectID } } } }"
)


def send_feishu(text: str) -> bool:
    if not FEISHU_WEBHOOK:
        print("[alert] FEISHU_ALERT_WEBHOOK 未配置，丢弃消息", flush=True)
        return False
    for attempt in range(3):
        try:
            body = {"msg_type": "text", "content": {"text": text}}
            if FEISHU_SECRET:
                ts = str(int(time.time()))
                body["timestamp"] = ts
                body["sign"] = feishu_lib.feishu_sign(ts, FEISHU_SECRET)
            req = urllib.request.Request(
                FEISHU_WEBHOOK,
                data=json.dumps(body, ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                resp = json.load(r)
            if resp.get("code") == 0 or resp.get("StatusCode") == 0:
                return True
            print(f"[alert] 飞书返回非零: code={resp.get('code')}", flush=True)
        except Exception as e:
            print(f"[alert] 飞书发送失败(第{attempt + 1}次): {type(e).__name__}", flush=True)
        time.sleep(1)
    return False


def http_get(url: str, timeout=3) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


class Axonhub:
    def __init__(self):
        self.token = None

    def login(self):
        body = json.dumps({"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}).encode()
        req = urllib.request.Request(
            AXONHUB_BASE + "/admin/auth/signin", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            self.token = json.load(r)["token"]

    def gql(self, query: str, variables=None):
        for attempt in range(2):
            if not self.token:
                self.login()
            payload = json.dumps({"query": query, "variables": variables or {}}).encode()
            req = urllib.request.Request(
                AXONHUB_BASE + "/admin/graphql", data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = json.load(r)
                if data.get("errors"):
                    raise RuntimeError(str(data["errors"])[:200])
                return data["data"]
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    self.token = None  # 重新登录后重试一次
                    continue
                raise
        raise RuntimeError("gql unreachable")


# ---- 飞书 OpenAPI（提额审批同步，issue #19）----

_fs_token_cache = {"token": "", "exp": 0.0}


def feishu_tenant_token() -> str:
    if _fs_token_cache["token"] and time.time() < _fs_token_cache["exp"]:
        return _fs_token_cache["token"]
    body = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=body, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.load(r)
    _fs_token_cache["token"] = d["tenant_access_token"]
    _fs_token_cache["exp"] = time.time() + int(d.get("expire", 7200)) / 2
    return _fs_token_cache["token"]


def feishu_get(path: str):
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis" + path,
        headers={"Authorization": f"Bearer {feishu_tenant_token()}"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.load(r)
    if d.get("code") != 0:
        raise RuntimeError(f"feishu {path}: code={d.get('code')} {d.get('msg', '')[:120]}")
    return d.get("data") or {}


# 档位秩次（issue #85 单一定义点）：体验档 < 标准档 < 高档。key_requests（申请/审批侧守卫）
# 与前端 web/src/ai4s/pages/my-keys/tier-rank.ts 双向同源，改动需三侧同步。
TIER_RANK = {"体验档": 0, "标准档": 1, "高档": 2}


def parse_tier(text: str):
    """表单"目标档位"自由文本 → 档名（高档/标准档），识别不了返回 None。"""
    t = (text or "").lower()
    if "高档" in text or "premium" in t or "pro" in t:
        return "高档"
    if "标准" in text or "standard" in t or "std" in t:
        return "标准档"
    return None


# issue #72：apply_tier / create_emp_key 共用查询（原 apply_tier 内联字符串，抽常量复用）
USER_BY_EMAIL_QUERY = (
    "query($email: String!) { users(first: 1, where: {email: $email}) { edges { node { id email status } } } }"
)
PROFILE_TEMPLATES_QUERY = (
    "query { apiKeyProfileTemplates(first: 50) { edges { node { id name "
    "profile { name modelMappings { from to } "
    "channelIDs channelTags channelTagsMatchMode modelIDs loadBalanceStrategy "
    "quota { requests totalTokens cost period { type pastDuration { value unit } calendarDuration { unit } } } } } } } }"
)
UPDATE_PROFILES_MUTATION = (
    "mutation($id: ID!, $input: UpdateAPIKeyProfilesInput!) { updateAPIKeyProfiles(id: $id, input: $input) { id } }"
)


def find_user_by_email(ax: Axonhub, email: str):
    """SSO 用户精确查（issue #70 #6：where email 等值，不受用户总数影响）。无用户返回 None。"""
    users = ax.gql(USER_BY_EMAIL_QUERY, {"email": email})["users"]["edges"]
    return users[0]["node"] if users else None


def load_tier_profile(ax: Axonhub, tier_name: str):
    """Profile 模板 → updateAPIKeyProfiles 输入形状；模板不存在返回 None。
    issue #81：modelMappings 必须透传（模板空则 []）——此前丢弃落库 null，前端 zod
    必填数组解析崩（Key 管理「配置」对话框报错）。
    issue #83：全字段透传（channelIDs/channelTags/channelTagsMatchMode/modelIDs/
    loadBalanceStrategy 一个不落）——档位渠道允许列表经模板下发，丢字段=换档时静默丢
    限制（#81 同款教训）；channelTagsMatchMode 空落 'any'（对齐前端 zod 缺省语义）。"""
    tpls = ax.gql(PROFILE_TEMPLATES_QUERY)["apiKeyProfileTemplates"]["edges"]
    tpl = next((e["node"] for e in tpls if e["node"]["name"] == tier_name), None)
    if not tpl:
        return None
    prof = tpl["profile"] or {}
    quota = prof.get("quota") or {}
    period = quota.get("period") or {}
    period_type = period.get("type", "calendar_duration")
    # calendarDuration 缺省兜底只对 calendar 类模板生效；past_duration 模板不得臆造 calendar 值
    calendar = period.get("calendarDuration")
    if calendar is None and period_type == "calendar_duration":
        calendar = {"unit": "month"}
    return {"name": tier_name,
            "modelMappings": prof.get("modelMappings") or [],
            "channelIDs": prof.get("channelIDs"),
            "channelTags": prof.get("channelTags"),
            "channelTagsMatchMode": prof.get("channelTagsMatchMode") or "any",
            "modelIDs": prof.get("modelIDs"),
            "loadBalanceStrategy": prof.get("loadBalanceStrategy"),
            "quota": {
        "requests": quota.get("requests"),
        "totalTokens": quota.get("totalTokens"),
        "cost": str(quota["cost"]) if quota.get("cost") is not None else None,
        "period": {"type": period_type,
                   "pastDuration": period.get("pastDuration"),
                   "calendarDuration": calendar},
    }}


# issue #85：按 userID 精确查本人 enabled key 的当前挂档（profiles.activeProfile）——
# apply_tier_to_user 逐 key 方向守卫与 key_requests 申请侧当前档查询共用。
# issue #89：叠加 projectID 过滤（多项目隔离）。
# 与 self_api._SELF_KEYS_QUERY 同源形状（where userID 等值），叠加 statusIn enabled；
# 不复用 ENABLED_API_KEYS_QUERY——它是巡检/预警共用查询，加字段影响面大。
USER_ENABLED_KEYS_QUERY = (
    "query($uid: ID!, $projectID: ID!) { apiKeys(first: 100, where: {userID: $uid, projectID: $projectID, statusIn: [enabled]}) { edges { node { "
    "id name userID profiles { activeProfile } } } } }"
)


def query_user_enabled_keys(ax: Axonhub, uid: str, project_id: str = KEY_PROJECT_ID):
    """userID+projectID 精确查 enabled key（id/name/activeProfile）。first:100 上限与 ENABLED_API_KEYS_QUERY 同款。
    issue #89：projectID 入过滤（APIKeyWhereInput.projectID 2026-08-22 内省+实证支持）——
    默认 Default 常量，飞书通道/存量申请语义不变（其 key 均落 Default）。"""
    data = ax.gql(USER_ENABLED_KEYS_QUERY, {"uid": uid, "projectID": project_id})
    return [e["node"] for e in data["apiKeys"]["edges"]]


# issue #89：用户项目成员列表（成员校验 + 项目名快照）。无 user(id:) 单查（内省实证），
# 用 users(where:{id}) + projects 子查询；first:50 与 USERS_WITH_PROJECTS_QUERY 同款上限。
USER_PROJECTS_QUERY = (
    "query($uid: ID!) { users(first: 1, where: {id: $uid}) { edges { node { id "
    "projects(first: 50) { edges { node { id name } } } } } } }"
)


def query_user_projects(ax: Axonhub, uid: str):
    """用户所属项目列表（[{id, name}]）；用户不存在返回 []。"""
    edges = ax.gql(USER_PROJECTS_QUERY, {"uid": uid})["users"]["edges"]
    if not edges:
        return []
    return [e["node"] for e in ((edges[0]["node"].get("projects") or {}).get("edges") or [])]


def key_tier_name(key: dict):
    """key 当前档名（profiles.activeProfile）；未挂档返回 None。"""
    return (key.get("profiles") or {}).get("activeProfile") or None


def apply_tier_to_user(ax: Axonhub, user: dict, tier_name: str, key_ids=None, project_id: str = KEY_PROJECT_ID):
    """目标用户 enabled Key 换挂目标档 + 逐 key never-downgrade 守卫（issue #85，控制台/飞书
    两通道同享）：目标秩 > key 当前秩 → 换挂（升档）；== → 换挂（同档重挂，保留模板数值调整后
    刷新存量快照的运维路径）；< → 跳过并在结果文本如实列出（绝不降档）。未挂档 key 直挂。
    issue #86：key_ids=目标子集（控制台按 Key 勾选）；None=全部 enabled Key（飞书通道与
    #86 前存量申请的原语义）。子集内审批期间被归档/删除的 Key 跳过并在结果文本按 id 列明。
    issue #89：project_id 限定作用项目（默认 Default；多项目隔离——不再跨项目拉齐）。
    返回结果文本；模板缺失/无 enabled Key 也走文本（调用方回执/落结果），gql 异常上抛。"""
    prof = load_tier_profile(ax, tier_name)
    if not prof:
        return f"找不到 {tier_name} Profile 模板"
    own = query_user_enabled_keys(ax, user["id"], project_id)
    if not own:
        return f"{user.get('email')} 名下无 enabled Key"
    missing = []
    if key_ids is not None:
        want = set(key_ids)
        enabled_ids = {k["id"] for k in own}
        missing = [kid for kid in key_ids if kid not in enabled_ids]  # 审批期间被归档/删除
        own = [k for k in own if k["id"] in want]
    target_rank = TIER_RANK.get(tier_name)
    changed, skipped = [], []
    for k in own:
        cur = key_tier_name(k)
        cur_rank = TIER_RANK.get(cur) if cur else None
        if target_rank is not None and cur_rank is not None and target_rank < cur_rank:
            skipped.append(f"{k['name']}（当前{cur}）")
            continue
        ax.gql(
            UPDATE_PROFILES_MUTATION,
            {"id": k["id"], "input": {"activeProfile": tier_name, "profiles": [prof]}},
        )
        changed.append(k["name"])
    parts = []
    if changed:
        parts.append(f"已将 {len(changed)} 个 Key 换挂 {tier_name}（{', '.join(changed)}）")
    if skipped:
        parts.append(f"跳过 {len(skipped)} 个更高档 Key（绝不降档）：{', '.join(skipped)}")
    if missing:
        parts.append(f"所选 {len(missing)} 个 Key 已不可用（归档/删除，跳过）：{', '.join(missing)}")
    if not changed:
        return "未变更：" + "；".join(parts) if parts else "未变更（所选 Key 均不可用）"
    return "；".join(parts)


def apply_tier(ax: Axonhub, open_id: str, tier_name: str):
    """申请人 open_id → axonhub 用户 → 换挂目标档（飞书审批定义通道入口，入口形状不变；
    issue #85 起换挂带逐 key never-downgrade 守卫，见 apply_tier_to_user）。"""
    email = f"{open_id}@casdoor.oidc"
    user = find_user_by_email(ax, email)
    if not user:
        return f"axonhub 中无 {email} 用户（未完成过 SSO 首登？）"
    return apply_tier_to_user(ax, user, tier_name)


def approval_action(status: str) -> str:
    """审批实例状态 → 处理分支（issue #56 抽纯函数，行为与独立容器时代一致）：
    process=APPROVED 执行并回执；receipt=REJECTED 只回执；skip=PENDING 下轮再看；
    mark=CANCELED/DELETED 及其余终态只标记已处理。"""
    if status == "PENDING":
        return "skip"
    if status == "APPROVED":
        return "process"
    if status == "REJECTED":
        return "receipt"
    return "mark"


# ---- 新建 key 审批（issue #72）----

CREATE_API_KEY_MUTATION = (
    "mutation($input: CreateAPIKeyInput!) { createAPIKey(input: $input) { id name key } }"
)
# 重试幂等恢复用：按名+项目查回已建 key（apiKeys 查询 key 字段返回完整明文，2026-08-20 实证；
# projectID 过滤必须带——全局按名查跨项目同名会取回别人的 key 明文，评审 P2-1 安全洞）
KEY_BY_NAME_QUERY = (
    "query($name: String!, $projectID: ID!) { apiKeys(first: 1, where: {name: $name, projectID: $projectID})"
    " { edges { node { id name key } } } }"
)
# 新建审批初始档写死体验档（issue 拍板：最小权限，要更高档走既有提额审批）
KEY_INIT_TIER = "体验档"


def parse_purpose(form) -> str:
    """表单控件 widget_purpose 的用途说明（去空白）。无控件/空值返回 ""。"""
    if isinstance(form, str):
        try:
            form = json.loads(form or "[]")
        except Exception:
            form = []
    for w in form or []:
        if (w.get("custom_id") or w.get("id")) == "widget_purpose":
            return (w.get("value") or "").strip()
    return ""


def make_key_name(open_id: str, purpose: str, day: str, ic: str) -> str:
    """key 命名：emp-<open_id前8>-<yyyymmdd>-<用途摘要≤12>-<实例码尾4>。
    摘要去非文字字符（保留中英文数字）；实例码尾 4 位保证同日同人重复申请不重名
    （axonhub 项目级名称唯一是应用层约束，撞名 createAPIKey 直接报错）。"""
    oid8 = "".join(c for c in (open_id or "") if c.isalnum() or c == "_")[:8] or "unknown"
    slug = "".join(c for c in (purpose or "") if c.isalnum())[:12] or "key"
    tail = "".join(c for c in (ic or "") if c.isalnum())[-4:].lower() or "0000"
    return f"emp-{oid8}-{day}-{slug}-{tail}"


def assign_key_owner(key_gid: str, user_gid: str):
    """把 key 归属改为申请人（createAPIKey 无 userID 入参，v1.0.0-beta6 实证：CreateAPIKeyInput /
    UpdateAPIKeyInput / UpdateUserInput 均无，REST 亦无端点——SQL 直改是唯一路径）。
    bump updated_at 让 axonhub 30s 增量缓存刷新自动跟进；请求路径不认 user_id（仅归属/报表用），
    无认证语义变化。AXONHUB_DB_DSN 未配置直接抛，调用方降级为回执提示人工核对。"""
    if not AXONHUB_DB_DSN:
        raise RuntimeError("AXONHUB_DB_DSN 未配置")
    import psycopg  # 函数级懒加载（issue #49 纪律）：单测/未配置路径不 import
    key_id = int(key_gid.rsplit("/", 1)[1])
    user_id = int(user_gid.rsplit("/", 1)[1])
    with psycopg.connect(AXONHUB_DB_DSN, connect_timeout=5) as conn:
        cur = conn.execute(
            "UPDATE api_keys SET user_id=%s, updated_at=now() WHERE id=%s AND deleted_at=0",
            (user_id, key_id),
        )
        if cur.rowcount != 1:
            # 命中 0 行（key 不存在/已软删）当失败处理：抛错让上层重试，不静默成功
            conn.rollback()
            raise RuntimeError(f"api_keys id={key_id} UPDATE 命中 {cur.rowcount} 行")
        conn.commit()


def feishu_dm(open_id: str, text: str) -> bool:
    """机器人私信申请人（im:message 权限，2026-08-20 实证 code=0 送达）。异常/非零 code → False 走兜底。"""
    try:
        body = {"receive_id": open_id, "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False)}
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Authorization": f"Bearer {feishu_tenant_token()}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        if d.get("code") == 0:
            return True
        print(f"[alert] 私信发送非零: code={d.get('code')}", flush=True)
    except Exception as e:
        print(f"[alert] 私信发送失败: {type(e).__name__}", flush=True)
    return False


def key_by_name(ax: Axonhub, name: str, project_id: str = KEY_PROJECT_ID):
    """按名+指定项目查 key（幂等恢复用）；不存在返回 None。
    issue #89：project_id 参数化（默认 Default 常量，飞书通道/存量申请零变化）。"""
    edges = ax.gql(KEY_BY_NAME_QUERY, {"name": name, "projectID": project_id})["apiKeys"]["edges"]
    return edges[0]["node"] if edges else None


def ensure_emp_key(ax: Axonhub, user: dict, seed: str, purpose: str, day: str, tail: str,
                   tier: str = None, project_id: str = KEY_PROJECT_ID):
    """建 key 核心（issue #79 抽出，控制台/飞书审批两通道共用）：按名幂等找回或新建 →
    归 user → 挂档（默认体验档；issue #81 控制台审批可选档覆盖经 tier 传入，飞书通道不传=原样）。
    issue #89：project_id 参数化——控制台新建申请落在申请记录的项目（同名查找/创建同项目，
    跨项目同名不串）；默认 Default 常量，飞书通道与存量申请零变化。
    返回 (name, plain, owner_note)；模板缺失/gql 异常上抛（调用方下轮重试）。
    幂等：半途失败留下同名 key 时按名找回明文续走（不重复建 key）。"""
    tier_name = tier or KEY_INIT_TIER
    name = make_key_name(seed, purpose, day, tail)
    node = key_by_name(ax, name, project_id)
    if not node:
        node = ax.gql(CREATE_API_KEY_MUTATION,
                      {"input": {"name": name, "projectID": project_id}})["createAPIKey"]
    plain = node["key"]
    owner_note = ""
    try:
        assign_key_owner(node["id"], user["id"])
    except Exception as e:
        owner_note = "；归属调整失败，请管理员人工核对"
        print(f"[alert] key 归属调整失败 {name}: {type(e).__name__}: {e}", flush=True)
    prof = load_tier_profile(ax, tier_name)
    if not prof:
        raise RuntimeError(f"找不到 {tier_name} Profile 模板")
    ax.gql(UPDATE_PROFILES_MUTATION,
           {"id": node["id"], "input": {"activeProfile": tier_name, "profiles": [prof]}})
    return name, plain, owner_note


def create_emp_key(ax: Axonhub, open_id: str, purpose: str, day: str, ic: str) -> str:
    """新建审批 APPROVED 执行体（飞书审批定义通道）：找用户 → ensure_emp_key → 私信交付明文。
    返回群回执用的结果摘要（绝不含明文）；抛异常=本轮失败，approval_sync 不标记、下轮重试。
    issue #91 P2-2：建 Key 前校验申请人仍是 Default（KEY_PROJECT_ID）项目成员——与控制台
    通道（#89 fail-closed）同口径；非成员不建（否则建出「能用（上游 #88 U1）但员工在控制台
    看不见」的 Key），回执说明，与「无用户」先例同语义（正常终态、标记已处理，不重试）。"""
    email = f"{open_id}@casdoor.oidc"
    user = find_user_by_email(ax, email)
    if not user:
        # 未首登无用户：不建 key、不算失败，回执引导后标记已处理（重新申请会生成新实例）
        return f"axonhub 无用户 {email}（申请人未首登平台），未建 Key；请先登录平台一次再重新申请"
    if KEY_PROJECT_ID not in {p.get("id") for p in query_user_projects(ax, user["id"])}:
        return (f"用户 {email} 已不在 Default 项目（已被移出），未建 Key；"
                "请管理员重新入项后重新申请，或引导其在控制台目标项目下发起申请")
    name, plain, owner_note = ensure_emp_key(ax, user, open_id, purpose, day, ic)
    dm_ok = feishu_dm(open_id, (
        f"[ai4s] 你的 API Key 已创建（审批 {ic}）\n"
        f"Key 名称: {name}\n档位: {KEY_INIT_TIER}（要更高档请另提「ai4s 额度提额申请」审批）\n"
        f"明文（请立即复制保存）:\n{plain}\n"
        "保管提醒: 明文勿转发勿提交代码仓库；本消息之外也可在控制台「我的 Key」页查看复制（issue #81）。"
    ))
    if dm_ok:
        return f"已建 Key {name}（{KEY_INIT_TIER}）{owner_note}，明文已私信申请人"
    return f"已建 Key {name}（{KEY_INIT_TIER}）{owner_note}；私信未送达，申请人可在控制台「我的 Key」页查看明文"


def _approval_day(inst: dict) -> str:
    """实例发起时间 → yyyymmdd（命名用实例时间而非处理时间：跨天重试名字不变，幂等）。"""
    try:
        ms = int(inst.get("start_time") or 0)
        return time.strftime("%Y%m%d", time.gmtime(ms / 1000)) if ms else time.strftime("%Y%m%d", time.gmtime())
    except Exception:
        return time.strftime("%Y%m%d", time.gmtime())


def _process_approved(ax: Axonhub, kind: str, ic: str, inst: dict) -> bool:
    """APPROVED 实例按类型执行 + 群回执。True=处理完可标记（含回执失败，与原实现一致）；
    False=执行抛错不标记，下轮重试。"""
    try:
        form = json.loads(inst.get("form") or "[]")
    except Exception:
        form = []
    open_id = inst.get("open_id") or inst.get("user_id") or ""
    if kind == "key":
        purpose = parse_purpose(form)
        try:
            result = create_emp_key(ax, open_id, purpose, _approval_day(inst), ic)
        except Exception as e:
            print(f"[alert] 新建 key 执行失败 {ic}: {type(e).__name__}: {e}", flush=True)
            return False
        text = f"[ai4s 新建 Key] 审批通过\n申请人: {open_id}\n用途: {purpose}\n结果: {result}\n实例: {ic}"
    else:
        tier_text = next((w.get("value", "") for w in form if (w.get("custom_id") or w.get("id")) == "widget_tier"), "")
        tier_name = parse_tier(tier_text)
        if not tier_name:
            result = f"无法识别目标档位（{tier_text!r}），请填 标准档 或 高档"
        else:
            try:
                result = apply_tier(ax, open_id, tier_name)
            except Exception as e:
                print(f"[alert] 提额执行失败 {ic}: {type(e).__name__}: {e}", flush=True)
                return False
        text = f"[ai4s 提额] 审批通过\n申请人: {open_id}\n目标档: {tier_text}\n结果: {result}\n实例: {ic}"
    if send_feishu(text):
        print(f"[alert] {kind} 审批已处理: {ic}", flush=True)
    return True


def _sync_kind(ax: Axonhub, done: list, kind: str, code: str):
    """单审批定义一轮：拉实例列表 → 逐实例按状态分支处理 → 标记 done（就地追加）。"""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 7 * 24 * 3600 * 1000
    try:
        data = feishu_get(
            f"/approval/v4/instances?approval_code={code}&start_time={start_ms}&end_time={now_ms}"
        )
    except Exception as e:
        print(f"[alert] 审批实例列表失败({kind}): {type(e).__name__}: {e}", flush=True)
        return
    ids = data.get("instance_code_list") or data.get("instances") or []
    for ic in ids:
        if ic in done:
            continue
        try:
            inst = feishu_get(f"/approval/v4/instances/{ic}")
        except Exception as e:
            print(f"[alert] 审批实例 {ic} 详情失败: {type(e).__name__}", flush=True)
            continue
        action = approval_action(inst.get("status"))
        if action == "skip":
            continue
        if action == "process":
            if not _process_approved(ax, kind, ic, inst):
                continue  # 不标记，下轮重试
        elif action == "receipt":
            open_id = inst.get("open_id") or inst.get("user_id") or ""
            if kind == "key":
                text = f"[ai4s 新建 Key] 审批未通过\n申请人: {open_id}\n未创建任何 Key\n实例: {ic}"
            else:
                text = f"[ai4s 提额] 审批未通过\n申请人: {open_id}\n额度维持现状，未做变更\n实例: {ic}"
            if send_feishu(text):
                print(f"[alert] {kind} 拒绝回执: {ic}", flush=True)
        # mark（CANCELED / DELETED / 其余终态）：只标记
        done.append(ic)


def approval_sync(ax: Axonhub, state: dict):
    """轮询审批单（issue #19 提额 + issue #72 新建并存，共享 done 列表——实例 code 全局唯一）。
    拒绝/撤回回执后标记；执行异常不标记下轮重试。
    FEISHU_APP_ID/FEISHU_APP_SECRET 缺失或两个 code 都未配置即整体跳过——只巡检不审批。"""
    if not (FEISHU_APP_ID and FEISHU_APP_SECRET and (APPROVAL_QUOTA_CODE or APPROVAL_KEY_CODE)):
        return
    done = state.setdefault("approval_done", [])
    for kind, code in (("quota", APPROVAL_QUOTA_CODE), ("key", APPROVAL_KEY_CODE)):
        if not code:
            continue
        try:
            _sync_kind(ax, done, kind, code)
        except Exception as e:
            # 单类异常不影响另一类（隔离纪律与单轮异常同款）
            print(f"[alert] 审批同步异常({kind}): {type(e).__name__}: {e}", flush=True)
    state["approval_done"] = done[-200:]


# ---- 新用户自动入 Default 项目（issue #73）----

# users 全量扫描（axonhub 无"不在某项目"的服务端过滤，只能客户端筛）。
# 约束：first:200 硬上限（ENABLED_API_KEYS_QUERY 同款）——用户总数超 200 即漏扫，
# 超规模时需改 after 分页；projects(first:50) 同理（单用户项目数上限）。
USERS_WITH_PROJECTS_QUERY = (
    "query { users(first: 200) { edges { node { id email isOwner status "
    "projects(first: 50) { edges { node { id } } } } } } }"
)
MY_PROJECTS_QUERY = "query { myProjects { id name } }"
ADD_USER_TO_PROJECT_MUTATION = (
    "mutation($input: AddUserToProjectInput!) { addUserToProject(input: $input) { id userID projectID } }"
)
# 项目级能力（issue #68 定案，与 assign-default-project.sh SCOPES 一致）：
# read_api_keys/write_api_keys 刻意不发（项目级无属主过滤，下发即重开明文凭读与自助提额）
PROJECT_MEMBER_SCOPES = ["read_requests", "write_requests"]


def pending_default_project_users(users_edges: list, project_gid: str) -> list:
    """筛待入项用户（纯函数）：activated、非 owner、尚不在 Default 项目。返回 [node]。"""
    out = []
    for e in users_edges:
        n = e["node"]
        if n.get("isOwner") or n.get("status") != "activated":
            continue
        member = {p["node"]["id"] for p in ((n.get("projects") or {}).get("edges") or [])}
        if project_gid not in member:
            out.append(n)
    return out


def auto_assign_project(ax: Axonhub):
    """一轮自动入项（issue #73）：axonhub 无 JIT 默认项目机制/用户事件推送，轮询补齐。
    幂等天然成立（已成员被筛选跳过，无需状态位）；单用户失败记日志跳过、下轮重试；
    入项成功发群通知（管理员感知通道；best-effort——发送失败不重试，与审批回执同款纪律）。
    项目头实证：addUserToProject 不需要 X-Project-ID（项目由 input.projectId 决定，
    2026-08-20 与 assign-default-project.sh 不带头跑通一致），复用 Axonhub.gql 即可。"""
    projs = ax.gql(MY_PROJECTS_QUERY)["myProjects"] or []
    # issue #87：fail-closed——找不到目标项目记日志本轮跳过，不回退到任意项目（此前回退
    # projs[0]：Default 被改名/删除时会把新用户静默加进第一个项目，且通知仍谎报「Default」）。
    # issue #91 P2-4：按 gid（KEY_PROJECT_ID）匹配，不再按名 "Default"——项目改名不再
    # 静默停摆（按名匹配时改名后永远找不到，只能靠人工发现）；fail-closed 语义不变。
    proj = next((p for p in projs if p.get("id") == KEY_PROJECT_ID), None)
    if not proj:
        print(f"[alert] 自动入项：myProjects 中未找到 {KEY_PROJECT_ID}，本轮跳过", flush=True)
        return
    pid = proj["id"]
    pname = proj.get("name") or KEY_PROJECT_ID
    users = ax.gql(USERS_WITH_PROJECTS_QUERY)["users"]["edges"]
    for n in pending_default_project_users(users, pid):
        try:
            ax.gql(ADD_USER_TO_PROJECT_MUTATION, {"input": {
                "projectId": pid, "userId": n["id"], "isOwner": False,
                "scopes": PROJECT_MEMBER_SCOPES,
            }})
        except Exception as e:
            print(f"[alert] 自动入项失败 {n.get('email')}: {type(e).__name__}: {e}", flush=True)
            continue
        print(f"[alert] 新用户自动入项: {n.get('email')}", flush=True)
        send_feishu(
            f"[ai4s 通知] 新用户已自动加入 {pname} 项目\n"
            f"用户: {n.get('email')}\n项目级能力: {'/'.join(PROJECT_MEMBER_SCOPES)}"
        )


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict):
    # admin_api 原子写纪律（issue #57 P2-2）：唯一 tmp + .bak 滚动 + finally 清理，
    # 读者只见完整旧版或完整新版；makedirs 对空 dirname 容错（STATE_PATH 被 env 覆写为纯文件名时）
    d = os.path.dirname(STATE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    admin_api.write_json_atomic(STATE_PATH, state)


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


# ---- 巡检判定纯函数（issue #56：借迁移给核心分支补单测）----


def quota_dims(quota: dict, usage: dict) -> list:
    """API key 单 profile 各维度用量比率：[(维度名, ratio, "用量/配额" 文本)]。
    维度缺失或配额为 0（含 cost 为 None/"0"）不参与判定（与独立容器时代一致）。"""
    dims = []
    if quota.get("requests"):
        dims.append(("请求数", (usage.get("requestCount") or 0) / quota["requests"], f"{usage.get('requestCount')}/{quota['requests']}"))
    if quota.get("totalTokens"):
        dims.append(("token", (usage.get("totalTokens") or 0) / quota["totalTokens"], f"{usage.get('totalTokens')}/{quota['totalTokens']}"))
    if quota.get("cost") is not None and float(quota["cost"]):
        dims.append(("credit", float(usage.get("totalCost") or 0) / float(quota["cost"]), f"{usage.get('totalCost')}/{quota['cost']}"))
    return dims


def classify_quota(dims: list):
    """额度判定：over=任一维度 ratio ≥1.0（耗尽告警）；near=任一维度 ∈[0.8,1.0)（80% 预警）。
    返回 (over, near) 两个子列表；调用方按 bool(over) / bool(near) and not over 取 bad 位
    （耗尽优先——已耗尽只发耗尽告警，不同时发预警）。"""
    over = [d for d in dims if d[1] >= 1.0]
    near = [d for d in dims if 0.8 <= d[1] < 1.0]
    return over, near


def flip_actions(findings: dict, state: dict) -> list:
    """防抖翻转判定：findings key -> (bad, 告警文本, 恢复文本) 对比 state 中的 prev 位，
    返回 [(key, 'alert'|'recover', text)]——只列出发生翻转、需要发送的项；
    发送成功后才允许调用方更新 state（发送失败不更新，下轮自然重试）。"""
    actions = []
    for k, (bad, alert_text, recover_text) in findings.items():
        prev = state.get(k, False)
        if bad and not prev:
            actions.append((k, "alert", alert_text))
        elif not bad and prev:
            actions.append((k, "recover", recover_text))
    return actions


def shadow_avail_findings(layer_stats: dict) -> dict:
    """shadow 层可用率 findings（issue #92）：{layer: shadow_log.stats 形状} → flip_actions 形状。
    bad = 样本足够（total >= SHADOW_ALERT_MIN）且异常率 >= SHADOW_ERR_RATE——
    样本不足不判坏（防小样本抖动）；无流量即无记录，属正常（无流量无从判定，不误报）。"""
    findings = {}
    for layer, s in layer_stats.items():
        name = SHADOW_LAYER_NAMES.get(layer, layer)
        total, errors, rate = s.get("total", 0), s.get("errors", 0), s.get("error_rate", 0.0)
        bad = total >= SHADOW_ALERT_MIN and rate >= SHADOW_ERR_RATE
        findings[f"shadow:{layer}"] = (
            bad,
            f"[ai4s 告警] {name} 异常率过高\n近 {total} 次判定失败 {errors} 次（{rate:.0%}）\n"
            f"影响: 该层 shadow 判定停摆，观察期数据不可信（fail-open 不拦截，业务无感）\n时间: {now_str()}",
            f"[ai4s 恢复] {name} 已恢复（异常率回落）",
        )
    return findings


def pg_block_pending(recs: list, cursor: float) -> list:
    """PG 阻断事件待发卡列表（issue #103，纯函数）：pg 层 tail 记录（新到旧形状）+ 游标
    （上次已告警条 ts）→ [(ts, 告警文本)]（旧到新——先发生先告警）。
    只取 blocked=True 且 ts > cursor 的条目（== 不重发）；单轮封顶 PG_BLOCK_ALERT_BATCH
    （截最旧一批先发，剩余条 ts 仍在游标后，下轮续发）。
    卡片脱敏纪律：只带层名/score/阻断阈值/请求模型名/时间——记录本身不落原文（shadow_log
    #103 字段设计），此处亦不引入任何文本字段。"""
    out = []
    for r in sorted((r for r in recs if r.get("blocked") and (r.get("ts") or 0) > cursor),
                    key=lambda r: r["ts"]):
        score = r.get("score")
        score_txt = f"{score:.3f}" if isinstance(score, (int, float)) else "-"
        out.append((r["ts"], (
            f"[ai4s 告警] 提示词注入已阻断（451）\n"
            f"层: 注入 PG\n"
            f"score: {score_txt} ≥ 阻断阈值 {r.get('block_threshold')}\n"
            f"请求模型: {r.get('model') or '-'}\n"
            f"时间: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(r['ts']))}"
        )))
    return out[:PG_BLOCK_ALERT_BATCH]


def judge_warn_pending(recs: list, cursor: float) -> list:
    """judge warn 事件待发卡列表（issue #101，纯函数，严格对齐 pg_block_pending 模型）：
    judge 层 tail 记录（新到旧形状）+ 游标（上次已告警条 ts）→ [(ts, 告警文本)]（旧到新）。
    只取 warned=True 且 ts > cursor 的条目（== 不重发）；单轮封顶 JUDGE_WARN_ALERT_BATCH
    （截最旧一批先发，剩余条 ts 仍在游标后，下轮续发）。
    卡片脱敏纪律：只带项目/confidence/实体命中数/请求模型名/时间——记录本身不落原文
    不落 key 不落实体字符串（shadow_log #92/#101 字段设计），此处亦不引入任何文本字段。
    项目名口径（查实记录）：/request webhook 线路格式仅 body.messages/model
    （契约 docs/contracts/dlp-webhook-shim.md L21 + #103 阻断条同实证），无 gid/key
    项目标识——#91 gid→名映射无从映射，卡片项目字段标「未知（请求链路无项目标识）」；
    后续链路带上项目标识再补映射。"""
    out = []
    for r in sorted((r for r in recs if r.get("warned") and (r.get("ts") or 0) > cursor),
                    key=lambda r: r["ts"]):
        conf = r.get("confidence")
        conf_txt = f"{conf:.2f}" if isinstance(conf, (int, float)) else "-"
        entities = r.get("entities")
        out.append((r["ts"], (
            f"[ai4s 告警] 语义 judge 疑似涉密（warn 试点，告警未拦截）\n"
            f"项目: 未知（请求链路无项目标识）\n"
            f"confidence: {conf_txt}\n"
            f"实体命中数: {entities if entities is not None else '-'}\n"
            f"请求模型: {r.get('model') or '-'}\n"
            f"时间: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(r['ts']))}"
        )))
    return out[:JUDGE_WARN_ALERT_BATCH]


def check_cycle(ax: Axonhub, state: dict) -> dict:
    """一轮巡检；返回新状态。finding: key -> (bad: bool, 告警文本, 恢复文本)"""
    findings = {}

    # 1) DLP fail-open 探活
    findings["dlp:shim"] = (
        not http_get(SHIM_URL + "/healthz"),
        "[ai4s 告警] DLP shim 不可达\n影响: agentgateway failOpen 生效中，流量未经词表/PII 检测直传上游\n时间: " + now_str(),
        "[ai4s 恢复] DLP shim 已恢复",
    )
    findings["dlp:presidio"] = (
        not http_get(PRESIDIO_URL + "/health"),
        "[ai4s 告警] Presidio 不可达\n影响: shim 词表/PII 检测降级（fail-open 分级），流量可能未经检测直传\n时间: " + now_str(),
        "[ai4s 恢复] Presidio 已恢复",
    )

    # 2) 上游渠道额度
    try:
        data = ax.gql(
            "query { queryChannels(input: {first: 100, where: {statusIn: [enabled]}}) "
            "{ edges { node { name providerQuotaStatus { status ready } } } } }"
        )
        for e in data["queryChannels"]["edges"]:
            n = e["node"]
            qs = n.get("providerQuotaStatus")
            if not qs or not qs.get("ready"):
                continue
            st = qs.get("status")
            findings[f"quota:channel:{n['name']}"] = (
                st in ("warning", "exhausted"),
                f"[ai4s 告警] 上游渠道额度异常\n渠道: {n['name']}\n状态: {st}\n时间: {now_str()}",
                f"[ai4s 恢复] 上游渠道额度恢复: {n['name']}",
            )
    except Exception as e:
        print(f"[alert] 渠道额度查询失败: {type(e).__name__}", flush=True)

    # 3) 员工 API key 额度
    # issue #91 P2-3：告警/预警/恢复文本带项目名——一轮一次 myProjects 建 gid→名映射；
    # 名单查询失败回退裸 gid（不阻塞告警主流程）
    proj_names = {}
    try:
        proj_names = {p["id"]: p.get("name") for p in (ax.gql(MY_PROJECTS_QUERY)["myProjects"] or [])}
    except Exception as e:
        print(f"[alert] 项目名单查询失败（告警文本回退 gid）: {type(e).__name__}", flush=True)
    try:
        data = ax.gql(ENABLED_API_KEYS_QUERY)
        for e in data["apiKeys"]["edges"]:
            key = e["node"]
            proj_label = proj_names.get(key.get("projectID")) or key.get("projectID") or "未知项目"
            try:
                usages = ax.gql(
                    "query($id: ID!) { apiKeyQuotaUsages(apiKeyId: $id) "
                    "{ profileName quota { requests totalTokens cost } usage { requestCount totalTokens totalCost } } }",
                    {"id": key["id"]},
                )["apiKeyQuotaUsages"]
            except Exception:
                continue
            for u in usages:
                over, near = classify_quota(quota_dims(u.get("quota") or {}, u.get("usage") or {}))
                hits = [f"{name} {txt}" for name, _, txt in over]
                findings[f"quota:apikey:{key['id']}:{u.get('profileName')}"] = (
                    bool(over),
                    f"[ai4s 告警] 员工 API Key 额度耗尽\nKey: {key['name']}（profile {u.get('profileName')}）\n项目: {proj_label}\n用量: {'; '.join(hits)}\n时间: {now_str()}",
                    f"[ai4s 恢复] API Key 额度已重置: {key['name']}（profile {u.get('profileName')}，项目 {proj_label}）",
                )
                # 80% 预警（issue #18）：赶在 403 之前提醒走提额审批
                near_txt = "; ".join(f"{name} {txt}（{r:.0%}）" for name, r, txt in near)
                findings[f"quota80:apikey:{key['id']}:{u.get('profileName')}"] = (
                    bool(near) and not over,
                    f"[ai4s 预警] 员工 API Key 额度将尽（≥80%）\nKey: {key['name']}（profile {u.get('profileName')}）\n项目: {proj_label}\n用量: {near_txt}\n请在飞书提交提额审批，避免被 403 拒载\n时间: {now_str()}",
                    f"[ai4s 恢复] API Key 额度预警解除（新周期/提额生效）: {key['name']}（profile {u.get('profileName')}，项目 {proj_label}）",
                )
    except Exception as e:
        print(f"[alert] API key 额度查询失败: {type(e).__name__}", flush=True)

    # 4) shadow 层可用率（issue #92）：judge/PG 判定持久化（shadow_log）的异常率巡检——
    # 两侧判定原本只 print  stdout，异常无感知；统计失败只记日志不阻塞巡检主流程
    try:
        findings.update(shadow_avail_findings(
            {layer: shadow_log.stats(layer, window=SHADOW_ALERT_WINDOW) for layer in ("judge", "pg")}))
    except Exception as e:
        print(f"[alert] shadow 层统计失败: {type(e).__name__}", flush=True)

    # 状态翻转才发送；发送失败不更新状态（下轮自然重试）
    for k, kind, text in flip_actions(findings, state):
        if send_feishu(text):
            state[k] = kind == "alert"
            print(f"[alert] 已{'告警' if kind == 'alert' else '恢复'}: {k}", flush=True)

    # 5) PG 阻断事件（issue #103）：事件型告警（非 flip_actions 状态翻转模型——阻断无「恢复」
    # 语义），游标独立防抖：发送成功才把游标推进到该条 ts，失败即止下轮重试（与翻转项同款纪律）；
    # tail/发送异常只记日志，不阻塞巡检主流程
    try:
        pending = pg_block_pending(shadow_log.tail(PG_BLOCK_ALERT_TAIL, layer="pg"),
                                   state.get("pg_block_cursor") or 0.0)
        for ts, text in pending:
            if not send_feishu(text):
                break
            state["pg_block_cursor"] = ts
            print(f"[alert] 已告警: pg 阻断 ts={ts}", flush=True)
    except Exception as e:
        print(f"[alert] PG 阻断巡检失败: {type(e).__name__}: {e}", flush=True)

    # 6) judge warn 事件（issue #101）：严格对齐巡检项 5 模型——事件型告警（warn 无「恢复」语义），
    # 独立游标 judge_warn_cursor 防抖（发送成功才推进、失败即止下轮重试）；
    # tail/发送异常只记日志，不阻塞巡检主流程。warn 只告警不拦截（契约「语义层永不阻断」）
    try:
        pending = judge_warn_pending(shadow_log.tail(JUDGE_WARN_ALERT_TAIL, layer="judge"),
                                     state.get("judge_warn_cursor") or 0.0)
        for ts, text in pending:
            if not send_feishu(text):
                break
            state["judge_warn_cursor"] = ts
            print(f"[alert] 已告警: judge warn ts={ts}", flush=True)
    except Exception as e:
        print(f"[alert] judge warn 巡检失败: {type(e).__name__}: {e}", flush=True)
    return state


def _poll_loop():
    print(f"[alert] 巡检启动，间隔 {POLL_INTERVAL}s", flush=True)
    ax = Axonhub()
    state = load_state()
    while True:
        try:
            state = check_cycle(ax, state)
            approval_sync(ax, state)
            try:
                auto_assign_project(ax)
            except Exception as e:
                # 同款隔离（issue #73）：入项整体失败只记日志，不阻塞巡检/审批、不杀线程
                print(f"[alert] 自动入项本轮异常: {type(e).__name__}: {e}", flush=True)
            try:
                import key_requests  # 函数级懒加载（psycopg 同款纪律）：key_requests 顶层 import 本模块，顶层互导成环
                key_requests.sweep_expired()
            except Exception as e:
                # 同款隔离（issue #79）：超时清扫失败只记日志，不阻塞巡检/审批
                print(f"[alert] key 申请超时清扫异常: {type(e).__name__}: {e}", flush=True)
            save_state(state)
        except Exception as e:
            # 单轮异常只记日志：不杀线程、绝不影响 shim 检测路径（issue #56 隔离纪律）
            print(f"[alert] 本轮异常: {type(e).__name__}: {e}", flush=True)
        time.sleep(POLL_INTERVAL)


def start_daemon() -> threading.Thread:
    """以 daemon 线程启动巡检循环（issue #56 并入 shim）。只应由 app.py __main__ 调用——
    import 本模块不启动线程，本机单测环境 import app 安全。"""
    t = threading.Thread(target=_poll_loop, name="alert-poller", daemon=True)
    t.start()
    return t
