#!/usr/bin/env python3
"""ai4s 告警巡检（issue #17）+ 审批同步（issue #19 提额 / issue #72 新建），issue #56 起并入 shim 后台线程。

axonhub 无事件源的事务靠主动轮询补齐。巡检项（状态翻转才发飞书，恢复也通知，状态存 /state 防抖）：
  1. DLP fail-open 探活：shim /healthz、presidio /health 任一不可达 → 告警
     （agentgateway failureMode=failOpen，组件挂掉流量静默直传，必须有人喊）；
     并入 shim 后 SHIM_URL 默认进程内自调 http://localhost:8080（issue #56）
  2. 上游渠道额度：queryChannels → providerQuotaStatus ∈ {warning, exhausted} → 告警
  3. 员工 API key 额度：apiKeys → apiKeyQuotaUsages，usage 达到 quota → 告警；≥80% → 预警（issue #18）

审批同步（issue #19 提额 + issue #72 新建，approval_sync 泛化为多定义并存）：
  轮询飞书审批实例（APPROVAL_QUOTA_CODE / APPROVAL_KEY_CODE 两个定义各自轮询）：
  - 提额：APPROVED → 申请人 open_id → axonhub 用户（email = ou_*@casdoor.oidc）→ 其 enabled Key
    → 按表单"目标档位"换挂对应 Profile 模板 → 群里回执。拒绝/撤回只回执/标记。
  - 新建：APPROVED → open_id → axonhub 用户（无用户=未首登，回执提示先登录再重新申请）
    → createAPIKey（体验档写死，命名 emp-<oid8>-<yyyymmdd>-<用途摘要>-<ic尾4>）
    → 归属申请人（createAPIKey 无 userID 入参，v1.0.0-beta6 实证；建后 SQL 直改 user_id 并
      bump updated_at，axonhub 30s 增量刷新自动跟进缓存；归申请人是提额流按 userID 找 key 的前提）
    → 挂体验档 profile → 机器人私信申请人交付明文（im:message，2026-08 实证可用）；
    私信失败兜底=群回执只发尾号 4 位 + 找管理员领取。群回执只发摘要，绝不含明文。
  FEISHU_APP_ID/FEISHU_APP_SECRET 缺失或两个 code 都空即整体跳过（与独立容器时代一致）。

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
ENABLED_API_KEYS_QUERY = (
    "query { apiKeys(first: 100, where: {statusIn: [enabled]}) { edges { node { id name userID } } } }"
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
    "profile { name quota { requests totalTokens cost period { type calendarDuration { unit } } } } } } } }"
)
UPDATE_PROFILES_MUTATION = (
    "mutation($id: ID!, $input: UpdateAPIKeyProfilesInput!) { updateAPIKeyProfiles(id: $id, input: $input) { id } }"
)


def find_user_by_email(ax: Axonhub, email: str):
    """SSO 用户精确查（issue #70 #6：where email 等值，不受用户总数影响）。无用户返回 None。"""
    users = ax.gql(USER_BY_EMAIL_QUERY, {"email": email})["users"]["edges"]
    return users[0]["node"] if users else None


def load_tier_profile(ax: Axonhub, tier_name: str):
    """Profile 模板 → updateAPIKeyProfiles 输入形状；模板不存在返回 None。"""
    tpls = ax.gql(PROFILE_TEMPLATES_QUERY)["apiKeyProfileTemplates"]["edges"]
    tpl = next((e["node"] for e in tpls if e["node"]["name"] == tier_name), None)
    if not tpl:
        return None
    quota = (tpl["profile"] or {}).get("quota") or {}
    period = quota.get("period") or {}
    return {"name": tier_name, "quota": {
        "requests": quota.get("requests"),
        "totalTokens": quota.get("totalTokens"),
        "cost": str(quota["cost"]) if quota.get("cost") is not None else None,
        "period": {"type": period.get("type", "calendar_duration"),
                   "calendarDuration": period.get("calendarDuration") or {"unit": "month"}},
    }}


def apply_tier(ax: Axonhub, open_id: str, tier_name: str):
    """申请人 open_id → axonhub 用户 → 其 enabled Key 全部换挂目标档。返回结果文本。"""
    prof = load_tier_profile(ax, tier_name)
    if not prof:
        return f"找不到 {tier_name} Profile 模板"
    email = f"{open_id}@casdoor.oidc"
    user = find_user_by_email(ax, email)
    if not user:
        return f"axonhub 中无 {email} 用户（未完成过 SSO 首登？）"
    keys = ax.gql(ENABLED_API_KEYS_QUERY)["apiKeys"]["edges"]
    own = [e["node"] for e in keys if e["node"]["userID"] == user["id"]]
    if not own:
        return f"{email} 名下无 enabled Key"
    for k in own:
        ax.gql(
            UPDATE_PROFILES_MUTATION,
            {"id": k["id"], "input": {"activeProfile": tier_name, "profiles": [prof]}},
        )
    return f"已将 {len(own)} 个 Key 换挂 {tier_name}（{', '.join(k['name'] for k in own)}）"


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


def key_by_name(ax: Axonhub, name: str):
    """按名+本项目查 key（幂等恢复用）；不存在返回 None。"""
    edges = ax.gql(KEY_BY_NAME_QUERY, {"name": name, "projectID": KEY_PROJECT_ID})["apiKeys"]["edges"]
    return edges[0]["node"] if edges else None


def create_emp_key(ax: Axonhub, open_id: str, purpose: str, day: str, ic: str) -> str:
    """新建审批 APPROVED 执行体：建 key → 归申请人 → 挂体验档 → 私信交付明文。
    返回群回执用的结果摘要（绝不含明文）；抛异常=本轮失败，approval_sync 不标记、下轮重试。
    幂等：上轮半途失败留下同名 key 时按名找回明文续走（不重复建 key）。"""
    email = f"{open_id}@casdoor.oidc"
    user = find_user_by_email(ax, email)
    if not user:
        # 未首登无用户：不建 key、不算失败，回执引导后标记已处理（重新申请会生成新实例）
        return f"axonhub 无用户 {email}（申请人未首登平台），未建 Key；请先登录平台一次再重新申请"
    name = make_key_name(open_id, purpose, day, ic)
    node = key_by_name(ax, name)
    if not node:
        node = ax.gql(CREATE_API_KEY_MUTATION,
                      {"input": {"name": name, "projectID": KEY_PROJECT_ID}})["createAPIKey"]
    plain = node["key"]
    owner_note = ""
    try:
        assign_key_owner(node["id"], user["id"])
    except Exception as e:
        owner_note = "；归属调整失败，请管理员人工核对"
        print(f"[alert] key 归属调整失败 {name}: {type(e).__name__}: {e}", flush=True)
    prof = load_tier_profile(ax, KEY_INIT_TIER)
    if not prof:
        raise RuntimeError(f"找不到 {KEY_INIT_TIER} Profile 模板")
    ax.gql(UPDATE_PROFILES_MUTATION,
           {"id": node["id"], "input": {"activeProfile": KEY_INIT_TIER, "profiles": [prof]}})
    dm_ok = feishu_dm(open_id, (
        f"[ai4s] 你的 API Key 已创建（审批 {ic}）\n"
        f"Key 名称: {name}\n档位: {KEY_INIT_TIER}（要更高档请另提「额度提升」审批）\n"
        f"明文（仅此一条消息，请立即复制保存）:\n{plain}\n"
        "保管提醒: 明文只出现这一次，勿转发勿提交到代码仓库；丢失不补办，重新提交「ai4s API Key 申请」审批即可。"
    ))
    if dm_ok:
        return f"已建 Key {name}（{KEY_INIT_TIER}）{owner_note}，明文已私信申请人"
    return f"已建 Key {name}（{KEY_INIT_TIER}）{owner_note}；私信未送达，Key 尾号 …{plain[-4:]}，请申请人联系管理员领取"


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
    try:
        data = ax.gql(ENABLED_API_KEYS_QUERY)
        for e in data["apiKeys"]["edges"]:
            key = e["node"]
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
                    f"[ai4s 告警] 员工 API Key 额度耗尽\nKey: {key['name']}（profile {u.get('profileName')}）\n用量: {'; '.join(hits)}\n时间: {now_str()}",
                    f"[ai4s 恢复] API Key 额度已重置: {key['name']}（profile {u.get('profileName')}）",
                )
                # 80% 预警（issue #18）：赶在 403 之前提醒走提额审批
                near_txt = "; ".join(f"{name} {txt}（{r:.0%}）" for name, r, txt in near)
                findings[f"quota80:apikey:{key['id']}:{u.get('profileName')}"] = (
                    bool(near) and not over,
                    f"[ai4s 预警] 员工 API Key 额度将尽（≥80%）\nKey: {key['name']}（profile {u.get('profileName')}）\n用量: {near_txt}\n请在飞书提交提额审批，避免被 403 拒载\n时间: {now_str()}",
                    f"[ai4s 恢复] API Key 额度预警解除（新周期/提额生效）: {key['name']}（profile {u.get('profileName')}）",
                )
    except Exception as e:
        print(f"[alert] API key 额度查询失败: {type(e).__name__}", flush=True)

    # 状态翻转才发送；发送失败不更新状态（下轮自然重试）
    for k, kind, text in flip_actions(findings, state):
        if send_feishu(text):
            state[k] = kind == "alert"
            print(f"[alert] 已{'告警' if kind == 'alert' else '恢复'}: {k}", flush=True)
    return state


def _poll_loop():
    print(f"[alert] 巡检启动，间隔 {POLL_INTERVAL}s", flush=True)
    ax = Axonhub()
    state = load_state()
    while True:
        try:
            state = check_cycle(ax, state)
            approval_sync(ax, state)
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
