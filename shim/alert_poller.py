#!/usr/bin/env python3
"""ai4s 告警巡检（issue #17）+ 提额审批同步（issue #19），issue #56 起并入 shim 后台线程。

axonhub 无事件源的事务靠主动轮询补齐。巡检项（状态翻转才发飞书，恢复也通知，状态存 /state 防抖）：
  1. DLP fail-open 探活：shim /healthz、presidio /health 任一不可达 → 告警
     （agentgateway failureMode=failOpen，组件挂掉流量静默直传，必须有人喊）；
     并入 shim 后 SHIM_URL 默认进程内自调 http://localhost:8080（issue #56）
  2. 上游渠道额度：queryChannels → providerQuotaStatus ∈ {warning, exhausted} → 告警
  3. 员工 API key 额度：apiKeys → apiKeyQuotaUsages，usage 达到 quota → 告警；≥80% → 预警（issue #18）

提额审批同步（issue #19）：
  轮询飞书审批实例（APPROVAL_QUOTA_CODE 定义），APPROVED 且未处理过的：
  申请人 open_id → axonhub 用户（email = ou_*@casdoor.oidc）→ 其 enabled Key
  → 按表单"目标档位"换挂对应 Profile 模板 → 群里回执。拒绝/撤回只标记已处理。
  FEISHU_APP_ID/FEISHU_APP_SECRET/APPROVAL_QUOTA_CODE 任一缺失即整体跳过（与独立容器时代一致）。

发送：飞书群机器人签名校验（与 shim /feishu-alert 同算法）；axonhub 实时事件
（channel.auto_disabled）走 shim /feishu-alert 适配器，本模块不重复。
依赖仅标准库。secret 不进日志。

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


def apply_tier(ax: Axonhub, open_id: str, tier_name: str):
    """申请人 open_id → axonhub 用户 → 其 enabled Key 全部换挂目标档。返回结果文本。"""
    tpls = ax.gql(
        "query { apiKeyProfileTemplates(first: 50) { edges { node { id name "
        "profile { name quota { requests totalTokens cost period { type calendarDuration { unit } } } } } } } }"
    )["apiKeyProfileTemplates"]["edges"]
    tpl = next((e["node"] for e in tpls if e["node"]["name"] == tier_name), None)
    if not tpl:
        return f"找不到 {tier_name} Profile 模板"
    email = f"{open_id}@casdoor.oidc"
    # issue #70 #6：users(first:200) 硬上限（总数超 200 即漏人）改 where email 精确查
    # （上游 UserWhereInput 支持 email 等值，2026-08-20 实测），不再受用户总数影响
    users = ax.gql(
        "query($email: String!) { users(first: 1, where: {email: $email}) { edges { node { id email status } } } }",
        {"email": email},
    )["users"]["edges"]
    user = users[0]["node"] if users else None
    if not user:
        return f"axonhub 中无 {email} 用户（未完成过 SSO 首登？）"
    keys = ax.gql(ENABLED_API_KEYS_QUERY)["apiKeys"]["edges"]
    own = [e["node"] for e in keys if e["node"]["userID"] == user["id"]]
    if not own:
        return f"{email} 名下无 enabled Key"
    quota = (tpl["profile"] or {}).get("quota") or {}
    period = quota.get("period") or {}
    prof = {"name": tier_name, "quota": {
        "requests": quota.get("requests"),
        "totalTokens": quota.get("totalTokens"),
        "cost": str(quota["cost"]) if quota.get("cost") is not None else None,
        "period": {"type": period.get("type", "calendar_duration"),
                   "calendarDuration": period.get("calendarDuration") or {"unit": "month"}},
    }}
    for k in own:
        ax.gql(
            "mutation($id: ID!, $input: UpdateAPIKeyProfilesInput!) { updateAPIKeyProfiles(id: $id, input: $input) { id } }",
            {"id": k["id"], "input": {"activeProfile": tier_name, "profiles": [prof]}},
        )
    return f"已将 {len(own)} 个 Key 换挂 {tier_name}（{', '.join(k['name'] for k in own)}）"


def approval_action(status: str) -> str:
    """审批实例状态 → 处理分支（issue #56 抽纯函数，行为与独立容器时代一致）：
    process=APPROVED 执行提额并回执；receipt=REJECTED 只回执；skip=PENDING 下轮再看；
    mark=CANCELED/DELETED 及其余终态只标记已处理。"""
    if status == "PENDING":
        return "skip"
    if status == "APPROVED":
        return "process"
    if status == "REJECTED":
        return "receipt"
    return "mark"


def approval_sync(ax: Axonhub, state: dict):
    """轮询提额审批单，处理 APPROVED 实例（issue #19）。拒绝/撤回标记跳过，异常不标记下轮重试。
    凭据（APPROVAL_QUOTA_CODE/FEISHU_APP_ID/FEISHU_APP_SECRET）任一缺失整体跳过——只巡检不审批。"""
    if not (APPROVAL_QUOTA_CODE and FEISHU_APP_ID and FEISHU_APP_SECRET):
        return
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 7 * 24 * 3600 * 1000
    try:
        data = feishu_get(
            f"/approval/v4/instances?approval_code={APPROVAL_QUOTA_CODE}&start_time={start_ms}&end_time={now_ms}"
        )
    except Exception as e:
        print(f"[alert] 审批实例列表失败: {type(e).__name__}: {e}", flush=True)
        return
    ids = data.get("instance_code_list") or data.get("instances") or []
    done = state.setdefault("approval_done", [])
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
            try:
                form = json.loads(inst.get("form") or "[]")
            except Exception:
                form = []
            tier_text = next((w.get("value", "") for w in form if (w.get("custom_id") or w.get("id")) == "widget_tier"), "")
            open_id = inst.get("open_id") or inst.get("user_id") or ""
            tier_name = parse_tier(tier_text)
            if not tier_name:
                result = f"无法识别目标档位（{tier_text!r}），请填 标准档 或 高档"
            else:
                try:
                    result = apply_tier(ax, open_id, tier_name)
                except Exception as e:
                    print(f"[alert] 提额执行失败 {ic}: {type(e).__name__}: {e}", flush=True)
                    continue  # 不标记，下轮重试
            if send_feishu(f"[ai4s 提额] 审批通过\n申请人: {open_id}\n目标档: {tier_text}\n结果: {result}\n实例: {ic}"):
                print(f"[alert] 提额已处理: {ic} -> {tier_text}", flush=True)
        elif action == "receipt":
            open_id = inst.get("open_id") or inst.get("user_id") or ""
            if send_feishu(f"[ai4s 提额] 审批未通过\n申请人: {open_id}\n额度维持现状，未做变更\n实例: {ic}"):
                print(f"[alert] 提额拒绝回执: {ic}", flush=True)
        # mark（CANCELED / DELETED / 其余终态）：只标记
        done.append(ic)
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
