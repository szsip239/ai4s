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
import re
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

# 商密语义层 shadow（issue #21）：LLM judge 只记录不阻断。
# 测试期 judge 经 axonhub:8090 直连（绕过 agentgateway DLP 防递归）调 deepseek-flash，样本全为合成占位词；
# 真实流量启用前必须换内网模型（Ollama/vLLM）——judge 会扩大暴露面，生产不可外发。
JUDGE_ENABLED = os.environ.get("JUDGE_ENABLED", "") == "1"
JUDGE_BASE_URL = os.environ.get("JUDGE_BASE_URL", "http://axonhub:8090/v1")
JUDGE_API_KEY = os.environ.get("JUDGE_API_KEY", "")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "deepseek-v4-flash")
JUDGE_TIMEOUT = int(os.environ.get("JUDGE_TIMEOUT", "8"))

JUDGE_SYSTEM = """你是企业数据防泄漏（DLP）语义判定器。判定用户文本是否在语义上涉及公司商密：内部项目代号、未发布产品名、内部系统/域名。注意识别谐音、拼音、拆字、繁体、暗示性描述等变形指代。普通业务话术、与词表无关的同名事物（如旅游景点）不算涉密。
已知商密词表（精确词由规则层拦截，你负责变形与语义指代）：{terms}
只输出 JSON：{{"confidential": true|false, "entities": ["类型"], "confidence": 0.0~1.0}}"""


def judge_text(text: str):
    """语义涉密判定。返回 {"confidential": bool, "entities": [...], "confidence": float}；异常/未启用返回 None（fail-open）。"""
    if not (JUDGE_ENABLED and JUDGE_API_KEY) or not text:
        return None
    terms = "、".join(t["value"] for t in load_terms())
    body = json.dumps({
        "model": JUDGE_MODEL,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM.format(terms=terms)},
            {"role": "user", "content": text[:4000]},
        ],
        "max_tokens": 300,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        JUDGE_BASE_URL + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {JUDGE_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=JUDGE_TIMEOUT) as r:
            d = json.load(r)
        content = (d["choices"][0]["message"].get("content") or "").strip()
        content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        v = json.loads(content)
        return {"confidential": bool(v.get("confidential")),
                "entities": [str(e) for e in v.get("entities") or []],
                "confidence": float(v.get("confidence") or 0)}
    except Exception:
        return None


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


# ---- 归一化前置（issue #22）----
# 只用于检测：全角→半角、词表字符繁简映射、空白/横线/下划线分隔容忍；
# mask 经 index map 映射回原文位置，原文结构不丢。纯 stdlib，无新故障面。
_FULLWIDTH = {i: chr(i - 0xFEE0) for i in range(0xFF01, 0xFF5F)}
_FULLWIDTH[0x3000] = " "
# 繁简映射：覆盖当前词表用字（词表扩词时按需补字，宁缺勿滥——错映射会误伤）
_TRAD2SIMP = dict(zip("鳳計劃鯨藍號話統雲網內級鳳", "凤计划鲸蓝号话统云网内级凤"))
_SEP = set(" \t\r\n-_")

# 归一化后文本上匹配的 secrets 规则（分隔符已剔除，pattern 不含分隔符；顺序同 L1：具体在前）
_NORM_SECRET_RULES = [
    ("secrets.anthropic_sk", r"skant[A-Za-z0-9]{20,}"),
    ("secrets.openai_sk", r"sk(?:proj)?[A-Za-z0-9]{20,}"),
    ("secrets.github_token", r"gh[pousr][A-Za-z0-9]{20,}"),
    ("secrets.github_token", r"githubpat[A-Za-z0-9]{20,}"),
    ("secrets.aws_key", r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    ("secrets.aliyun_ak", r"LTAI[A-Za-z0-9]{12,}"),
    ("secrets.private_key", r"BEGIN[A-Z0-9]*PRIVATEKEY"),
]
_NORM_SECRET_RES = [(rid, re.compile(p)) for rid, p in _NORM_SECRET_RULES]

_NORM_PII_RES = [  # 与 recognizers/pii-zh.json 同族（归一化后无需 \b，用 (?<!\d) 防长数字串误切）
    ("ZH_PHONE", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "【PII:手机号】"),
    ("ZH_ID_CARD", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "【PII:身份证号】"),
    ("ZH_BANK_CARD", re.compile(r"(?<!\d)(?:62|4|5)\d{14,17}(?!\d)"), "【PII:银行卡号】"),
]


def normalize_hard(s: str):
    """返回 (归一化文本, idx_map)：idx_map[归一化下标] = 原文下标。全角→半角、繁→简、剔除分隔符。"""
    out, idx = [], []
    for i, ch in enumerate(s):
        c = _FULLWIDTH.get(ord(ch), ch)
        c = _TRAD2SIMP.get(c, c)
        if c in _SEP:
            continue
        out.append(c)
        idx.append(i)
    return "".join(out), idx


def norm_secret_hits(norm: str) -> list:
    return [rid for rid, rgx in _NORM_SECRET_RES if rgx.search(norm)]


def norm_term_hits(norm_low: str, terms: list) -> list:
    hits = []
    for t in terms:
        nv, _ = normalize_hard(t["value"])
        if nv and nv.lower() in norm_low:
            hits.append(t)
    return hits


def norm_pii_mask_in_text(s: str):
    """对单段原文做归一化 PII 检测并回映 mask；返回 (新文本, 命中实体列表)。"""
    norm, idx = normalize_hard(s)
    if not norm:
        return s, []
    spans = []  # (orig_start, orig_end, replacement, entity)
    for entity, rgx, repl in _NORM_PII_RES:
        for m in rgx.finditer(norm):
            spans.append((idx[m.start()], idx[m.end() - 1] + 1, repl, entity))
    if not spans:
        return s, []
    spans.sort(key=lambda x: x[0], reverse=True)
    out, entities = s, []
    last_start = len(s) + 1
    for st, en, repl, entity in spans:
        if en > last_start:  # 重叠跨度跳过（先长后短原则已由排序保证大致正确）
            continue
        out = out[:st] + repl + out[en:]
        last_start = st
        entities.append(entity)
    return out, entities


def norm_mask_messages(messages):
    """逐消息归一化 PII mask（issue #22）；返回 (new_messages, any_masked, entities)。"""
    out, any_masked, all_entities = [], False, set()
    for m in messages or []:
        m2 = dict(m)
        c = m.get("content")
        if isinstance(c, str):
            new_c, ents = norm_pii_mask_in_text(c)
            if ents:
                m2["content"] = new_c
                any_masked = True
                all_entities |= set(ents)
        elif isinstance(c, list):
            parts = []
            for p in c:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    new_t, ents = norm_pii_mask_in_text(p["text"])
                    if ents:
                        p = {**p, "text": new_t}
                        any_masked = True
                        all_entities |= set(ents)
                parts.append(p)
            m2["content"] = parts
        out.append(m2)
    return out, any_masked, sorted(all_entities)


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
        if self.path == "/judge-test":
            # 语义层直测端点（issue #21）：不进请求链，供回归脚本测 judge 准确率/延迟
            try:
                length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                payload = json.loads(self.rfile.read(length) or b"{}")
                t0 = time.time()
                verdict = judge_text(payload.get("text", ""))
                self._json(200, {"verdict": verdict, "latency_ms": round((time.time() - t0) * 1000)})
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})
            return
        if self.path != "/request":
            self._json(404, {})
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
            payload = json.loads(self.rfile.read(length) or b"{}")
            messages = (payload.get("body") or {}).get("messages") or []
            text = extract_text(messages)
            terms = load_terms()
            # 归一化预检（issue #22）：全角/繁简/分隔归一后查 secrets + 词表
            norm, _ = normalize_hard(text)
            pre_rules = norm_secret_hits(norm) + [t["rule_id"] for t in norm_term_hits(norm.lower(), terms)] if text else []
            hits = [] if pre_rules else (analyze(text, terms) if text else [])
        except Exception as e:
            # shim 自身异常 → 500，由 agentgateway failureMode=failOpen 放行（契约分级）
            self._json(500, {"error": str(e)[:200]})
            return
        if pre_rules or hits:
            rule_ids = sorted(set(pre_rules) | {h["rule_id"] for h in hits})
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
        # issue #22：先走归一化 mask（分隔/全角变形）；无命中再走 Presidio context 流程
        try:
            masked_msgs, any_masked, entities = norm_mask_messages(messages)
            if not any_masked:
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
        # 语义层 shadow（issue #21）：响应已定后发 judge，只记录 verdict（不含原文），不影响本请求
        if JUDGE_ENABLED:
            v = judge_text(text)
            if v is not None:
                print(f"[semantic.shadow] confidential={v['confidential']} entities={','.join(v['entities']) or '-'} confidence={v['confidence']:.2f}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
