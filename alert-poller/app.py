#!/usr/bin/env python3
"""ai4s 告警巡检（issue #17）+ 提额审批同步（issue #19）：axonhub 无事件源的事务靠主动轮询补齐。

巡检项（状态翻转才发飞书，恢复也通知，状态存 /state 防抖）：
  1. DLP fail-open 探活：shim /healthz、presidio /health 任一不可达 → 告警
     （agentgateway failureMode=failOpen，组件挂掉流量静默直传，必须有人喊）
  2. 上游渠道额度：queryChannels → providerQuotaStatus ∈ {warning, exhausted} → 告警
  3. 员工 API key 额度：apiKeys → apiKeyQuotaUsages，usage 达到 quota → 告警；≥80% → 预警（issue #18）

提额审批同步（issue #19）：
  轮询飞书审批实例（APPROVAL_QUOTA_CODE 定义），APPROVED 且未处理过的：
  申请人 open_id → axonhub 用户（email = ou_*@casdoor.oidc）→ 其 enabled Key
  → 按表单"目标档位"换挂对应 Profile 模板 → 群里回执。拒绝/撤回只标记已处理。

发送：飞书群机器人签名校验（与 shim /feishu-alert 同算法）；axonhub 实时事件
（channel.auto_disabled）走 shim 适配器，本服务不重复。
依赖仅标准库，镜像 python:3.12-alpine。secret 不进日志。
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request

AXONHUB_BASE = os.environ.get("AXONHUB_BASE", "http://axonhub:8090")
ADMIN_EMAIL = os.environ.get("AXONHUB_ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("AXONHUB_ADMIN_PASSWORD", "")
FEISHU_WEBHOOK = os.environ.get("FEISHU_ALERT_WEBHOOK", "")
FEISHU_SECRET = os.environ.get("FEISHU_ALERT_SECRET", "")
SHIM_URL = os.environ.get("SHIM_URL", "http://shim:8080")
PRESIDIO_URL = os.environ.get("PRESIDIO_URL", "http://presidio:3000")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
STATE_PATH = os.environ.get("STATE_PATH", "/state/alert-state.json")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
APPROVAL_QUOTA_CODE = os.environ.get("APPROVAL_QUOTA_CODE", "")


def feishu_sign(ts: str, secret: str) -> str:
    return base64.b64encode(hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()).decode()


def send_feishu(text: str) -> bool:
    if not FEISHU_WEBHOOK:
        print("[alert-poller] FEISHU_ALERT_WEBHOOK 未配置，丢弃消息", flush=True)
        return False
    for attempt in range(3):
        try:
            body = {"msg_type": "text", "content": {"text": text}}
            if FEISHU_SECRET:
                ts = str(int(time.time()))
                body["timestamp"] = ts
                body["sign"] = feishu_sign(ts, FEISHU_SECRET)
            req = urllib.request.Request(
                FEISHU_WEBHOOK,
                data=json.dumps(body, ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                resp = json.load(r)
            if resp.get("code") == 0 or resp.get("StatusCode") == 0:
                return True
            print(f"[alert-poller] 飞书返回非零: code={resp.get('code')}", flush=True)
        except Exception as e:
            print(f"[alert-poller] 飞书发送失败(第{attempt + 1}次): {type(e).__name__}", flush=True)
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
    users = ax.gql("query { users(first: 200) { edges { node { id email status } } } }")["users"]["edges"]
    user = next((e["node"] for e in users if e["node"]["email"] == email), None)
    if not user:
        return f"axonhub 中无 {email} 用户（未完成过 SSO 首登？）"
    keys = ax.gql("query { apiKeys(first: 100, where: {statusIn: [enabled]}) { edges { node { id name userID } } } }")["apiKeys"]["edges"]
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


def approval_sync(ax: Axonhub, state: dict):
    """轮询提额审批单，处理 APPROVED 实例（issue #19）。拒绝/撤回标记跳过，异常不标记下轮重试。"""
    if not (APPROVAL_QUOTA_CODE and FEISHU_APP_ID and FEISHU_APP_SECRET):
        return
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 7 * 24 * 3600 * 1000
    try:
        data = feishu_get(
            f"/approval/v4/instances?approval_code={APPROVAL_QUOTA_CODE}&start_time={start_ms}&end_time={now_ms}"
        )
    except Exception as e:
        print(f"[alert-poller] 审批实例列表失败: {type(e).__name__}: {e}", flush=True)
        return
    ids = data.get("instance_code_list") or data.get("instances") or []
    done = state.setdefault("approval_done", [])
    for ic in ids:
        if ic in done:
            continue
        try:
            inst = feishu_get(f"/approval/v4/instances/{ic}")
        except Exception as e:
            print(f"[alert-poller] 审批实例 {ic} 详情失败: {type(e).__name__}", flush=True)
            continue
        status = inst.get("status")
        if status == "PENDING":
            continue
        if status == "APPROVED":
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
                    print(f"[alert-poller] 提额执行失败 {ic}: {type(e).__name__}: {e}", flush=True)
                    continue  # 不标记，下轮重试
            if send_feishu(f"[ai4s 提额] 审批通过\n申请人: {open_id}\n目标档: {tier_text}\n结果: {result}\n实例: {ic}"):
                print(f"[alert-poller] 提额已处理: {ic} -> {tier_text}", flush=True)
        elif status == "REJECTED":
            open_id = inst.get("open_id") or inst.get("user_id") or ""
            if send_feishu(f"[ai4s 提额] 审批未通过\n申请人: {open_id}\n额度维持现状，未做变更\n实例: {ic}"):
                print(f"[alert-poller] 提额拒绝回执: {ic}", flush=True)
        # CANCELED / DELETED：只标记
        done.append(ic)
    state["approval_done"] = done[-200:]


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


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
        print(f"[alert-poller] 渠道额度查询失败: {type(e).__name__}", flush=True)

    # 3) 员工 API key 额度
    try:
        data = ax.gql("query { apiKeys(first: 100, where: {statusIn: [enabled]}) { edges { node { id name } } } }")
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
                q, g = u.get("quota") or {}, u.get("usage") or {}
                # 各维度用量比率：requests / totalTokens / cost
                dims = []
                if q.get("requests"):
                    dims.append(("请求数", (g.get("requestCount") or 0) / q["requests"], f"{g.get('requestCount')}/{q['requests']}"))
                if q.get("totalTokens"):
                    dims.append(("token", (g.get("totalTokens") or 0) / q["totalTokens"], f"{g.get('totalTokens')}/{q['totalTokens']}"))
                if q.get("cost") is not None and float(q["cost"]):
                    dims.append(("credit", float(g.get("totalCost") or 0) / float(q["cost"]), f"{g.get('totalCost')}/{q['cost']}"))
                over = [d for d in dims if d[1] >= 1.0]
                near = [d for d in dims if 0.8 <= d[1] < 1.0]
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
        print(f"[alert-poller] API key 额度查询失败: {type(e).__name__}", flush=True)

    # 状态翻转才发送；发送失败不更新状态（下轮自然重试）
    for k, (bad, alert_text, recover_text) in findings.items():
        prev = state.get(k, False)
        if bad and not prev:
            if send_feishu(alert_text):
                state[k] = True
                print(f"[alert-poller] 已告警: {k}", flush=True)
        elif not bad and prev:
            if send_feishu(recover_text):
                state[k] = False
                print(f"[alert-poller] 已恢复: {k}", flush=True)
    return state


def main():
    print(f"[alert-poller] 启动，间隔 {POLL_INTERVAL}s", flush=True)
    ax = Axonhub()
    state = load_state()
    while True:
        try:
            state = check_cycle(ax, state)
            approval_sync(ax, state)
            save_state(state)
        except Exception as e:
            print(f"[alert-poller] 本轮异常: {type(e).__name__}: {e}", flush=True)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
