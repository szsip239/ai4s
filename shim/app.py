#!/usr/bin/env python3
"""ai4s DLP shim：agentgateway promptGuard webhook ↔ Presidio。

契约：docs/contracts/dlp-webhook-shim.md（L2 语义/词表层，fail-open 分级）。
线路格式对齐 agentgateway v1.4.1 webhook.rs：
  POST /request  {"body": {"messages": [...]}}
  放行 ← {"reason": "..."}（PassAction）
  阻断 ← {"body": "<err json>", "status_code": 451, "reason": "..."}（RejectAction）

检测职责划分：
  - 非 CJK 词：Presidio /analyze（ad-hoc deny-list recognizer 随请求注入，词表热更新）
  - CJK 词：shim 直配兜底（Presidio deny-list 依赖 NLP 分词，中文不可靠）
依赖仅标准库，镜像 python:3.12-alpine。
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PRESIDIO_URL = os.environ.get("PRESIDIO_URL", "http://presidio:3000")
WORDLIST_PATH = os.environ.get("WORDLIST_PATH", "/dlp/confidential-terms.json")
PII_RECOGNIZERS_PATH = os.environ.get("PII_RECOGNIZERS_PATH", "/recognizers/pii-zh.json")
MAX_BODY = 256 * 1024  # 契约：请求体超限截断送检

# 飞书告警适配（issue #17）：axonhub webhook → 群机器人（签名校验 + 有限重试，补 axonhub fire-and-forget 无重试）
FEISHU_WEBHOOK = os.environ.get("FEISHU_ALERT_WEBHOOK", "")
FEISHU_SECRET = os.environ.get("FEISHU_ALERT_SECRET", "")
ALERT_RETRIES = 2


def feishu_sign(ts: str, secret: str) -> str:
    digest = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def send_feishu_text(text: str) -> bool:
    """签名发送飞书群机器人文本；成功返回 True。secret/URL 不进日志。"""
    if not FEISHU_WEBHOOK:
        return False
    for attempt in range(ALERT_RETRIES + 1):
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
        except Exception:
            pass
        if attempt < ALERT_RETRIES:
            time.sleep(1)
    return False


def format_axonhub_alert(p: dict) -> str:
    """axonhub webhook payload（channel.auto_disabled）→ 飞书文本。"""
    ch = p.get("channel") or {}
    tr = p.get("trigger") or {}
    return (
        f"[ai4s 告警] 渠道被自动禁用\n"
        f"事件: {p.get('event', '-')}\n"
        f"渠道: {ch.get('name', '-')}（provider={ch.get('provider', '-')}）\n"
        f"触发: 状态码 {tr.get('status_code', '-')} 连续 {tr.get('actual_count', '-')}/{tr.get('threshold', '-')} 次\n"
        f"原因: {tr.get('reason', '-')}\n"
        f"时间: {p.get('occurred_at', '-')}"
    )


def load_pii_recognizers() -> list:
    """PII recognizer 定义（issue #15，recognizers/ 首件）——每请求重读，热更新。"""
    try:
        with open(PII_RECOGNIZERS_PATH, encoding="utf-8") as f:
            return json.load(f).get("recognizers", [])
    except Exception:
        return []


def load_terms() -> list:
    try:
        with open(WORDLIST_PATH, encoding="utf-8") as f:
            return json.load(f).get("terms", [])
    except Exception:
        return []  # 词表读不到 → 零命中（fail-open 语义）


def extract_text(messages) -> str:
    parts = []
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    parts.append(p["text"])
    return "\n".join(parts)


def presidio_hits(text: str, latin_terms: list) -> list:
    body = {
        "text": text,
        "language": "en",
        "ad_hoc_recognizers": [
            {
                "name": "ai4s_confidential",
                "supported_entity": "AI4S_CONFIDENTIAL",
                "supported_language": "en",
                "deny_list": [t["value"] for t in latin_terms],
            }
        ],
    }
    req = urllib.request.Request(
        PRESIDIO_URL + "/analyze",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=3) as r:
        results = json.load(r)
    hits = []
    for res in results:
        # 只认我们注入的 ad-hoc 实体的命中；Presidio 内置实体（US_BANK_NUMBER 等低分噪音）忽略
        if res.get("entity_type") != "AI4S_CONFIDENTIAL":
            continue
        snippet = text[res.get("start", 0) : res.get("end", 0)]
        term = next(
            (t for t in latin_terms if t["value"].lower() == snippet.lower()), None
        )
        hits.append({"rule_id": (term or latin_terms[0])["rule_id"], "term": snippet})
    return hits


def is_simple_token(s: str) -> bool:
    """纯字母数字 token（无 CJK、无标点）——可托付 Presidio deny-list。"""
    return s.isascii() and s.replace("-", "").replace("_", "").isalnum()


def analyze(text: str, terms: list) -> list:
    # Presidio deny-list 对含标点/中文的词会过度或不足匹配（实测）：
    # 仅简单 token 走 Presidio；其余一律 shim 子串直配（词表语义本来就是确定性子串）。
    via_presidio = [t for t in terms if is_simple_token(t["value"])]
    via_substring = [t for t in terms if not is_simple_token(t["value"])]
    hits = presidio_hits(text, via_presidio) if via_presidio else []
    low = text.lower()
    for t in via_substring:
        if t["value"].lower() in low:
            hits.append({"rule_id": t["rule_id"], "term": t["value"]})
    return hits


def pii_analyze_and_mask(text: str, recs: list) -> tuple:
    """PII 识别 + 脱敏（issue #15）：Presidio ad-hoc pattern recognizer，命中区间替换为 replacement。
    返回 (masked_text, hit_entities)。recs 为空时原样返回。"""
    if not recs or not text:
        return text, []
    adhoc = [
        {
            "name": r["name"],
            "supported_entity": r["entity"],
            "supported_language": "en",
            "patterns": r["patterns"],
            "context": r.get("context", []),
        }
        for r in recs
    ]
    body = {"text": text, "language": "en", "ad_hoc_recognizers": adhoc}
    req = urllib.request.Request(
        PRESIDIO_URL + "/analyze",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=3) as resp:
        results = json.load(resp)
    # 只认我们注入的 PII 实体的命中（内置实体噪音忽略）
    own = {r["entity"] for r in recs}
    results = [h for h in results if h.get("entity_type") in own]
    if not results:
        return text, []
    repl = {r["entity"]: r.get("replacement", f"【PII:{r['entity']}】") for r in recs}
    masked = text
    for hit in sorted(results, key=lambda h: h.get("start", 0), reverse=True):
        ent = hit.get("entity_type", "")
        masked = masked[: hit["start"]] + repl.get(ent, "【PII】") + masked[hit["end"] :]
    entities = sorted({h.get("entity_type", "") for h in results})
    return masked, entities


def mask_message_contents(messages, recs):
    """逐消息脱敏；返回 (new_messages, any_masked, entities)。"""
    if not recs:
        return messages, False, []
    all_entities = set()
    any_masked = False
    out = []
    for m in messages or []:
        m2 = dict(m)
        c = m.get("content")
        if isinstance(c, str):
            masked, ents = pii_analyze_and_mask(c, recs)
            if ents:
                m2["content"] = masked
                any_masked = True
                all_entities |= set(ents)
        elif isinstance(c, list):
            parts = []
            for p in c:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    masked, ents = pii_analyze_and_mask(p["text"], recs)
                    if ents:
                        p = {**p, "text": masked}
                        any_masked = True
                        all_entities |= set(ents)
                parts.append(p)
            m2["content"] = parts
        out.append(m2)
    return out, any_masked, sorted(all_entities)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默（命中敏感值不进 shim 日志，契约）
        pass

    def _json(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._json(200 if self.path == "/healthz" else 404, {"ok": self.path == "/healthz"})

    def do_POST(self):
        if self.path == "/feishu-alert":
            # axonhub webhook → 飞书适配（issue #17）
            try:
                length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                payload = json.loads(self.rfile.read(length) or b"{}")
                ok = send_feishu_text(format_axonhub_alert(payload))
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})
                return
            self._json(200 if ok else 502, {"ok": ok})
            return
        if self.path != "/request":
            self._json(404, {})
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
            payload = json.loads(self.rfile.read(length) or b"{}")
            messages = (payload.get("body") or {}).get("messages") or []
            text = extract_text(messages)
            hits = analyze(text, load_terms()) if text else []
        except Exception as e:
            # shim 自身异常 → 500，由 agentgateway failureMode=failOpen 放行（契约分级）
            self._json(500, {"error": str(e)[:200]})
            return
        if hits:
            rule_ids = sorted({h["rule_id"] for h in hits})
            body = json.dumps(
                {
                    "error": {
                        "message": f"Blocked by ai4s DLP: confidential term detected ({', '.join(rule_ids)})",
                        "type": "content_policy_violation",
                        "code": rule_ids[0],
                    }
                },
                ensure_ascii=False,
            )
            self._json(
                200,
                {
                    "action": {
                        "body": body,
                        "status_code": 451,
                        "reason": f"confidential term hit: {', '.join(rule_ids)} (values withheld)",
                    }
                },
            )
            return
        # PII 脱敏（issue #15）：命中不阻断，返回改写后的消息体（MaskAction）
        try:
            recs = load_pii_recognizers()
            masked_msgs, any_masked, entities = mask_message_contents(messages, recs)
        except Exception as e:
            self._json(500, {"error": str(e)[:200]})
            return
        if any_masked:
            self._json(
                200,
                {
                    "action": {
                        "body": {"messages": masked_msgs},
                        "reason": f"PII masked: {', '.join(entities)}",
                    }
                },
            )
        else:
            self._json(200, {"action": {"reason": "pass"}})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
