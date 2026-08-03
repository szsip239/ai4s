#!/usr/bin/env python3
"""ai4s 告警巡检（issue #17）：axonhub 无事件源的告警靠主动轮询补齐。

巡检项（状态翻转才发飞书，恢复也通知，状态存 /state 防抖）：
  1. DLP fail-open 探活：shim /healthz、presidio /health 任一不可达 → 告警
     （agentgateway failureMode=failOpen，组件挂掉流量静默直传，必须有人喊）
  2. 上游渠道额度：queryChannels → providerQuotaStatus ∈ {warning, exhausted} → 告警
  3. 员工 API key 额度：apiKeys → apiKeyQuotaUsages，usage 达到 quota → 告警

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
                hits = []
                if q.get("requests") and (g.get("requestCount") or 0) >= q["requests"]:
                    hits.append(f"请求数 {g.get('requestCount')}/{q['requests']}")
                if q.get("totalTokens") and (g.get("totalTokens") or 0) >= q["totalTokens"]:
                    hits.append(f"token {g.get('totalTokens')}/{q['totalTokens']}")
                if q.get("cost") is not None and float(g.get("totalCost") or 0) >= float(q["cost"]):
                    hits.append(f"费用 {g.get('totalCost')}/{q['cost']}")
                findings[f"quota:apikey:{key['id']}:{u.get('profileName')}"] = (
                    bool(hits),
                    f"[ai4s 告警] 员工 API Key 额度耗尽\nKey: {key['name']}（profile {u.get('profileName')}）\n用量: {'; '.join(hits)}\n时间: {now_str()}",
                    f"[ai4s 恢复] API Key 额度已重置: {key['name']}（profile {u.get('profileName')}）",
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
            save_state(state)
        except Exception as e:
            print(f"[alert-poller] 本轮异常: {type(e).__name__}: {e}", flush=True)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
