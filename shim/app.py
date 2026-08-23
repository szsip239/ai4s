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
本模块（检测路径）依赖仅标准库；镜像 python:3.12-slim（issue #48 起含 doc_extract 文档解析依赖，
检测路径不 import 第三方库，纪律不变）。
"""
import json
import os
import re
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import admin_api  # DLP 统一配置 admin 平面（issue #31）：/dlp-admin/*，与检测路径隔离
import self_api   # 员工自助平面（issue #74）：/self/*（本人 key 列表，无明文）
import edm_lib    # EDM 指纹算法共享库（issue #34）：入库/检测同法（契约铁律）
import feishu_lib  # 飞书签名共享实现（issue #70）：alert_poller 同一份
import alert_poller  # 告警巡检+提额审批（issue #56 并入）：import 不起线程，仅 __main__ start_daemon
import shadow_log  # shadow 判定观测闭环（issue #92）：stdlib-only；judge/PG 判定持久化供巡检/查询

PRESIDIO_URL = os.environ.get("PRESIDIO_URL", "http://presidio:3000")
WORDLIST_PATH = os.environ.get("WORDLIST_PATH", "/dlp/confidential-terms.json")
PII_RECOGNIZERS_PATH = os.environ.get("PII_RECOGNIZERS_PATH", "/recognizers/pii-zh.json")
SETTINGS_PATH = os.environ.get("SETTINGS_PATH", "/dlp/settings.json")
MAX_BODY = 256 * 1024  # 契约：请求体超限截断送检


# ---- 统一配置（issue #35）：settings.json > env > 内置默认 ----
# JUDGE/EDM/PG 开关阈值收敛到 settings.json（每请求重读，热生效，admin 平面 PUT /dlp-admin/settings
# 维护）；env 层保留作覆盖/回退。judge prompt 单一源=settings.json（review #2：无 env/代码默认，
# 缺失即 judge 降级纯词表）。凭据（JUDGE_API_KEY/FEISHU_*）永远只走 env。
def load_settings() -> dict:
    """每请求重读 settings.json（热更新）。缺失 → {}（可选覆盖层，合法回退态，不 warn）；
    损坏/不可读/非对象 → warn + {}（回退 env/内置默认，fail-open 语义）。"""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"[settings] 读取失败回退 env/默认: {SETTINGS_PATH}: {type(e).__name__}", flush=True)
        return {}
    if not isinstance(data, dict):
        print(f"[settings] 读取失败回退 env/默认: {SETTINGS_PATH}: 顶层非对象", flush=True)
        return {}
    return data


# 逐键类型护栏（review #1）：手工改坏 JSON 单个值类型时，坏键回退 env/默认 + warn，好键仍生效——
# 否则检测路径在响应已发后抛 TypeError（如 score >= "0.7"）。与 admin 写侧 _validate_settings 同款映射。
_SETTINGS_SCHEMA = {
    "judge": {"enabled": "bool", "model": "str", "base_url": "str", "timeout": "number",
              "prompt_system": "str", "prompt_fewshot": "str"},
    "edm": {"enabled": "bool", "min_hits": "int"},
    "pg": {"enabled": "bool", "threshold": "number", "normalize": "bool"},
    # 分层总开关（issue #40）：默认 True 保现网行为；关掉即整层跳过（l1=格式规则全族，
    # l2=词表/Presidio PII，response=响应侧整分支）
    "l1": {"enabled": "bool"},
    "l2": {"enabled": "bool"},
    "response": {"enabled": "bool"},
}


def _json_type_ok(v, kind: str) -> bool:
    """settings JSON 值类型检查（bool 先于数值排除——Python bool 是 int 子类）。"""
    if kind == "bool":
        return isinstance(v, bool)
    if kind == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if kind == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if kind == "str":
        return isinstance(v, str)
    return True


def setting_value(settings: dict, section: str, key: str, env_name, default):
    """三级取值（issue #35）：settings.json[section][key] > env（非空） > 内置默认。
    JSON 值先过 _SETTINGS_SCHEMA 类型护栏：不符 → 该键回退 env/默认 + warn（不含值本身）。
    env 按 default 类型转换：bool 对齐 "1" 语义；int/float/str 转换失败回退默认
    （比旧模块级 int()/float() 非法 env 启动即崩更宽容）。"""
    sec = settings.get(section)
    if isinstance(sec, dict) and key in sec:
        v = sec[key]
        kind = _SETTINGS_SCHEMA.get(section, {}).get(key)
        if kind is None or _json_type_ok(v, kind):
            return v
        print(f"[settings] {section}.{key} 类型不符（期望 {kind}），回退 env/默认", flush=True)
    if env_name:
        v = os.environ.get(env_name, "")
        if v != "":
            if isinstance(default, bool):
                return v == "1"
            try:
                return type(default)(v)
            except (TypeError, ValueError):
                return default
    return default


# 分层总开关可观测（issue #40 review）：l1/l2/response 开关状态变化时打一行 [settings] warn——
# 模块级记忆上次状态，不每请求刷日志；启动后首次观测到关闭也打（启动即关不留盲区）。
_LAYER_SWITCH_STATE = {}
_LAYER_SWITCH_TEXT = {
    "l1": ("格式规则层已撤防（密钥拦截敞口）", "格式规则层恢复生效"),
    "l2": ("词表/PII 检测层已跳过", "词表/PII 检测层恢复生效"),
    "response": ("响应侧输出检查已关闭", "响应侧输出检查恢复生效"),
}


def _layer_switch_observe(settings: dict) -> None:
    """每请求调用（基于已加载的 settings，成本可忽略）；并发下重复打一行可接受，不加锁（KISS）。"""
    for section, env_name in (("l1", "L1_ENABLED"), ("l2", "L2_ENABLED"), ("response", "RESPONSE_ENABLED")):
        cur = setting_value(settings, section, "enabled", env_name, True)
        prev = _LAYER_SWITCH_STATE.get(section)
        if prev == cur:
            continue
        _LAYER_SWITCH_STATE[section] = cur
        off_text, on_text = _LAYER_SWITCH_TEXT[section]
        if not cur:
            print(f"[settings] {section}.enabled=false，{off_text}", flush=True)
        elif prev is not None:  # 首次观测到开启是正常启动态，不打
            print(f"[settings] {section}.enabled=true，{on_text}", flush=True)

# 飞书告警适配（issue #17）：axonhub webhook → 群机器人（签名校验 + 有限重试，补 axonhub fire-and-forget 无重试）
FEISHU_WEBHOOK = os.environ.get("FEISHU_ALERT_WEBHOOK", "")
FEISHU_SECRET = os.environ.get("FEISHU_ALERT_SECRET", "")
ALERT_RETRIES = 2

# 商密语义层 shadow（issue #21）：LLM judge 只记录不阻断。
# 测试期 judge 经 axonhub:8090 直连（绕过 agentgateway DLP 防递归）调 deepseek-flash，样本全为合成占位词；
# 真实流量启用前必须换内网模型（Ollama/vLLM）——judge 会扩大暴露面，生产不可外发。
# 开关/模型/超时/prompt 走统一 settings（issue #35）；凭据永远只走 env。
JUDGE_API_KEY = os.environ.get("JUDGE_API_KEY", "")

# EDM 文档指纹（issue #29，L3 层 PoC）：归一化 shingle + SHA-256，命中≥阈值即 451。
# 语料/指纹库 gitignored；指纹是单向哈希，不存原文。
# 算法收编 shim/edm_lib.py（issue #34）：归一化/窗口/行级阈值与入库侧同法。
# 开关/命中阈值走统一 settings（issue #35）；指纹库路径仍走 env。
EDM_FP_PATH = os.environ.get("EDM_FP_PATH", "/edm/fingerprints.json")


def load_edm_fps():
    """指纹库（每请求重读，热更新）。读不到 → 两个空集（fail-open 语义）。
    返回 (shingle哈希集, 行级哈希集)。"""
    shingles_set, lines_set = set(), set()
    try:
        d = json.load(open(EDM_FP_PATH, encoding="utf-8"))
        for v in (d.get("docs") or {}).values():
            if isinstance(v, list):
                shingles_set.update(v)
            else:
                shingles_set.update(v.get("shingles") or [])
                lines_set.update(v.get("lines") or [])
    except Exception:
        pass
    return shingles_set, lines_set

def edm_hit_count(text: str, fps) -> int:
    """双通道命中数（issue #29）：char-shingle（整段/连续片段）+ 行级（抗乱序）。任一通道达阈即命中。
    算法走 edm_lib（issue #34）：归一化/哈希与入库侧同法。"""
    shingle_fps, line_fps = fps
    if not text:
        return 0
    hits = 0
    if shingle_fps:
        sh = {edm_lib.fp_of(s) for s in edm_lib.shingles(text)}
        hits = max(hits, len(sh & shingle_fps))
    if line_fps:
        hits = max(hits, len(edm_lib.line_hashes(text) & line_fps))
    return hits

# judge prompt 单一源 = settings.json（issue #35 review #2，AC：代码内不留默认 prompt）——
# JSON 缺失或 prompt 键缺失/类型不符时 judge 不可用，judge_text 返回 None 降级纯词表（既有 fail-open）。
# 现网生效 prompt 见 deploy/dlp/settings.json；{{ }}/{terms} 为 .format 转义/占位，改 prompt 时须保留。


def judge_text(text: str):
    """语义涉密判定。返回 {"confidential": bool, "entities": [...], "confidence": float}；异常/未启用返回 None（fail-open）。
    开关/模型/地址/超时三级取值（issue #35）：settings.json judge.* > env > 内置默认；
    prompt 只认 settings.json（无 env/代码默认）；凭据 JUDGE_API_KEY 永远只走 env。"""
    if not text:
        return None
    s = load_settings()
    enabled = setting_value(s, "judge", "enabled", "JUDGE_ENABLED", False)
    if not (enabled and JUDGE_API_KEY):
        return None
    model = setting_value(s, "judge", "model", "JUDGE_MODEL", "deepseek-v4-flash")
    base_url = setting_value(s, "judge", "base_url", "JUDGE_BASE_URL", "http://axonhub:8090/v1")
    timeout = setting_value(s, "judge", "timeout", "JUDGE_TIMEOUT", 8)
    prompt_system = setting_value(s, "judge", "prompt_system", None, None)
    prompt_fewshot = setting_value(s, "judge", "prompt_fewshot", None, None)
    if not prompt_system or not prompt_fewshot:
        # prompt 无源（settings.json 缺失/键缺失/类型不符）→ judge 不可用，降级纯词表（fail-open 语义沿用契约分级）
        print("[settings] judge prompt 缺失（settings.json judge.prompt_system/prompt_fewshot），judge 降级纯词表", flush=True)
        return None
    terms = "、".join(t["value"] for t in load_terms())
    try:
        system_content = prompt_system.format(terms=terms)
    except Exception:
        return None  # settings 里 prompt 占位损坏 → fail-open（与 judge 其余异常同语义）
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt_fewshot},
            {"role": "user", "content": text[:4000]},
        ],
        "max_tokens": 1500,  # issue #61：deepseek-v4-flash 是推理模型，300 会被 reasoning 烧尽（finish_reason=length、content 空 → ERR）；1500 实测够用
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        base_url + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {JUDGE_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        content = (d["choices"][0]["message"].get("content") or "").strip()
        content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        v = json.loads(content)
        return {"confidential": bool(v.get("confidential")),
                "entities": [str(e) for e in v.get("entities") or []],
                "confidence": float(v.get("confidence") or 0)}
    except Exception:
        return None


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


# PromptGuard 2 注入检测 shadow（issue #30）：检出记日志不阻断，fail-open。
# 开关/阈值走统一 settings（issue #35）；issue #67：promptguard 服务并入——
# HTTP POST $PG_URL 改进程内 pg_engine.score（PG_URL env 退役）。


def pg_guard(text: str):
    """PromptGuard 2 MALICIOUS 概率；异常/未启用返回 None（fail-open）。
    pg.normalize（issue #44）：true 时打分前置归一化（base64 内联解码/零宽清除/
    全角转半角，pg_engine.normalize_for_scoring 进程内单点）——只改打分输入，
    转发原文不动；默认 false 保持现网行为。
    issue #67 进程内化：import pg_engine 在 enabled 判定之后（issue #49 纪律——
    pg.enabled=false 时 import 都不发生，检测路径零 PG 开销）。推理同步阻塞
    （原 HTTP 同为同步；实测 p50≈50ms/p95≈136ms，4000 字符截断 + 512 token 截断封顶，
    无超时概念）；异常=放行+记日志（与原 HTTP 错误同语义，只记异常类型不含文本）。"""
    if not text:
        return None
    settings = load_settings()
    if not setting_value(settings, "pg", "enabled", "PG_ENABLED", False):
        return None
    normalize = setting_value(settings, "pg", "normalize", "PG_NORMALIZE", False)
    try:
        import pg_engine

        t = text[:4000]
        if normalize is True:
            t = pg_engine.normalize_for_scoring(t)
        return pg_engine.score(t)
    except Exception as e:
        print(f"[injection.shadow] fail-open: {type(e).__name__}", flush=True)
        return None


# ---- 归一化前置（issue #22）----
# 只用于检测：全角→半角、词表字符繁简映射、空白/横线/下划线分隔容忍；
# mask 经 index map 映射回原文位置，原文结构不丢。纯 stdlib，无新故障面。
_FULLWIDTH = {i: chr(i - 0xFEE0) for i in range(0xFF01, 0xFF5F)}
_FULLWIDTH[0x3000] = " "
# 繁简映射：覆盖当前词表用字（词表扩词时按需补字，宁缺勿滥——错映射会误伤）
_TRAD2SIMP = dict(zip("鳳計劃鯨藍號話統雲網內級鳳", "凤计划鲸蓝号话统云网内级凤"))
_SEP = set(" \t\r\n-_")

# L1/L1.5 格式规则统一源（issue #33）：每请求重读（与 load_terms 同纪律，热更新免重启）；
# fail-open：文件缺失/损坏 → 空规则（格式检测层失效但不 500，同契约 fail-open 分级）。
# shim_patterns 为归一化检测变体（分隔符已剔除，Python regex 支持 lookaround）；
# 无 shim_patterns 的规则不参与归一化检测（review #5：不静默回退 gateway_patterns）。
FORMAT_RULES_PATH = os.environ.get("FORMAT_RULES_PATH", "/dlp/format-rules.json")


def load_format_rules() -> list:
    try:
        with open(FORMAT_RULES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        # fail-open 必须留痕（契约硬性配套）：只记路径与错误类型，不含敏感值
        print(f"[format-rules] fail-open: {FORMAT_RULES_PATH}: {type(e).__name__}", flush=True)
        return []
    rules = data.get("rules", []) if isinstance(data, dict) else []
    return rules if isinstance(rules, list) else []


def _norm_compiled(rule: dict) -> list:
    """编译单条规则的 shim 侧归一化 pattern（shim_patterns）；坏 pattern 跳过。
    无 shim_patterns 的规则不参与归一化检测（review #5：删 gateway_patterns 静默回退——
    两栈 pattern 语义不同（归一化文本 vs 原文），静默回退是漏检面，显式不写才可见）。"""
    out = []
    for p in rule.get("shim_patterns") or []:
        try:
            out.append(re.compile(p))
        except re.error:
            continue
    return out


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


def norm_secret_hits(norm: str, rules: list = None) -> list:
    """归一化文本上的 L1 reject 命中（issue #33 起读 format-rules.json）。
    逐 pattern 命中逐条 append（同规则多 pattern 全中会出现多次，下游 set 化，行为同原硬编码表）。
    rules 缺省内部加载；调用方在循环中使用时传入一次加载的结果（review #4）。"""
    if rules is None:
        rules = load_format_rules()
    hits = []
    for rule in rules:
        if not rule.get("enabled") or rule.get("action") != "reject" or not rule.get("code"):
            continue
        for rgx in _norm_compiled(rule):
            if rgx.search(norm):
                hits.append(rule["code"])
    return hits


def norm_term_hits(norm_low: str, terms: list) -> list:
    hits = []
    for t in terms:
        nv, _ = normalize_hard(t["value"])
        if nv and nv.lower() in norm_low:
            hits.append(t)
    return hits


def norm_pii_mask_in_text(s: str, rules: list = None):
    """对单段原文做归一化 PII 检测并回映 mask；返回 (新文本, 命中实体列表)。
    issue #33 起规则读 format-rules.json（action=mask 项；entity/replacement 取自规则）。"""
    norm, idx = normalize_hard(s)
    if not norm:
        return s, []
    if rules is None:
        rules = load_format_rules()
    spans = []  # (orig_start, orig_end, replacement, entity)
    for rule in rules:
        if not rule.get("enabled") or rule.get("action") != "mask":
            continue
        entity = rule.get("entity") or rule.get("code") or "PII"
        repl = rule.get("replacement") or f"【PII:{entity}】"
        for rgx in _norm_compiled(rule):
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


def norm_mask_messages(messages, rules: list = None):
    """逐消息归一化 PII mask（issue #22）；返回 (new_messages, any_masked, entities)。
    规则一次加载传入各消息（issue #33：避免逐消息重读文件）；
    rules 缺省内部加载，调用方在循环中使用时传入一次加载的结果（review #4）。"""
    if rules is None:
        rules = load_format_rules()
    out, any_masked, all_entities = [], False, set()
    for m in messages or []:
        m2 = dict(m)
        c = m.get("content")
        if isinstance(c, str):
            new_c, ents = norm_pii_mask_in_text(c, rules)
            if ents:
                m2["content"] = new_c
                any_masked = True
                all_entities |= set(ents)
        elif isinstance(c, list):
            parts = []
            for p in c:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    new_t, ents = norm_pii_mask_in_text(p["text"], rules)
                    if ents:
                        p = {**p, "text": new_t}
                        any_masked = True
                        all_entities |= set(ents)
                parts.append(p)
            m2["content"] = parts
        out.append(m2)
    return out, any_masked, sorted(all_entities)


def mask_response_body(body, l1_enabled: bool = True, l2_enabled: bool = True):
    """响应侧 mask（issue #23）：对 completion JSON 的 choices[].message.content/reasoning_content
    做归一化 secrets/词表/PII 检测，命中字段整体替换为掩码。返回 (新body, 命中实体/规则列表)。
    响应侧不 reject（员工看到莫名错误比截断更糟）；命中只记规则，不落原文。
    分层总开关（issue #40）：模块关=处处关——l1_enabled=False 跳过 secrets/格式 PII 检测，
    l2_enabled=False 跳过词表检测（缺省 True 保旧调用方行为）。"""
    if not isinstance(body, dict):
        return body, []
    choices = body.get("choices")
    if not isinstance(choices, list):
        return body, []
    terms = load_terms()
    rules = load_format_rules()  # 响应级一次加载（review #4）：scan 按字段调用，避免逐字段重读+编译
    hits = set()

    def scan(text):
        found = []
        if not isinstance(text, str) or not text:
            return found
        norm, _ = normalize_hard(text)
        if l1_enabled:
            found += norm_secret_hits(norm, rules)
            _, _, ents = norm_mask_messages([{"role": "assistant", "content": text}], rules)
            found += ents
        if l2_enabled:
            found += [t["rule_id"] for t in norm_term_hits(norm.lower(), terms)]
        return found

    out = json.loads(json.dumps(body))  # 深拷贝
    for ch in out.get("choices", []):
        # 非流式 message.*；流式分片 delta.*（issue #23 实测：流式 chunk 走 delta）
        for container in (ch.get("message"), ch.get("delta")):
            if not isinstance(container, dict):
                continue
            for field in ("content", "reasoning_content"):
                v = container.get(field)
                if isinstance(v, str) and v:
                    f = scan(v)
                    if f:
                        hits.update(f)
                        container[field] = f"【ai4s DLP：应答含敏感内容已屏蔽（{', '.join(sorted(set(f)))}）】"
    return out, sorted(hits)


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
        if admin_api.handle(self, "GET"):  # admin 平面（issue #31）：/dlp-admin/* 优先分流
            return
        if self_api.handle(self, "GET"):  # 员工自助平面（issue #74）：/self/*
            return
        self._json(200 if self.path == "/healthz" else 404, {"ok": self.path == "/healthz"})

    def do_POST(self):
        if admin_api.handle(self, "POST"):  # admin 平面（issue #31）：/dlp-admin/* 优先分流
            return
        if self_api.handle(self, "POST"):  # 员工自助平面（issue #74 评审 P2）：已鉴权 POST → 显式 404
            return
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
        if self.path == "/response":
            # 响应侧 DLP（issue #23）：模型应答 secrets/词表/PII 命中 → 451 拒绝。
            # 语义说明：流式（SSE）下 agentgateway 会缓冲整流后评估，mask 改写对流式无效（实测），
            # 故响应侧命中统一拒绝——应答含敏感内容本身就是异常信号，阻断比部分遮盖更正确。
            # 总开关（issue #40）：response.enabled=false → 整个分支直接放行不检测；
            # l1/l2 总开关在响应侧同样生效（模块关=处处关）。
            try:
                length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                payload = json.loads(self.rfile.read(length) or b"{}")
                resp_body = payload.get("body")
                settings = load_settings()
                _layer_switch_observe(settings)  # 开关状态变化 warn（issue #40 review）
                if not setting_value(settings, "response", "enabled", "RESPONSE_ENABLED", True):
                    self._json(200, {"action": {"reason": "pass"}})
                    return
                _, entities = mask_response_body(
                    resp_body,
                    setting_value(settings, "l1", "enabled", "L1_ENABLED", True),
                    setting_value(settings, "l2", "enabled", "L2_ENABLED", True),
                )
                if entities:
                    err = json.dumps(
                        {"error": {"message": "Blocked by ai4s DLP: response contained sensitive content",
                                   "type": "content_policy_violation",
                                   "code": "response." + entities[0]}},
                        ensure_ascii=False,
                    )
                    self._json(200, {"action": {"body": err, "status_code": 451,
                                                "reason": f"response sensitive hit: {', '.join(entities)} (values withheld)"}})
                else:
                    self._json(200, {"action": {"reason": "pass"}})
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
            # 统一配置（issue #35）：settings.json > env > 内置默认，每请求重读热生效；本段一次读多键用
            settings = load_settings()
            _layer_switch_observe(settings)  # 开关状态变化 warn（issue #40 review）
            # 分层总开关（issue #40，默认 True 保现网行为）：l1 关 → 格式规则全族（secrets reject +
            # 归一化 PII mask）整层跳过；l2 关 → 词表/Presidio PII 阶段整体跳过
            l1_on = setting_value(settings, "l1", "enabled", "L1_ENABLED", True)
            l2_on = setting_value(settings, "l2", "enabled", "L2_ENABLED", True)
            # 归一化预检（issue #22）：全角/繁简/分隔归一后查 secrets + 词表
            norm, _ = normalize_hard(text)
            pre_rules = []
            if text:
                if l1_on:
                    pre_rules += norm_secret_hits(norm)
                if l2_on:
                    pre_rules += [t["rule_id"] for t in norm_term_hits(norm.lower(), terms)]
            # EDM 文档指纹（issue #29，L3）：整段粘贴商密文档 → 命中阈值即拦
            edm_enabled = setting_value(settings, "edm", "enabled", "EDM_ENABLED", False)
            edm_min_hits = setting_value(settings, "edm", "min_hits", "EDM_MIN_HITS", 2)
            if edm_enabled and not pre_rules and edm_hit_count(text, load_edm_fps()) >= edm_min_hits:
                pre_rules = ["edm.doc_match"]
            hits = [] if (pre_rules or not l2_on) else (analyze(text, terms) if text else [])
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
        # issue #40：归一化 mask 属 l1（格式规则全族），Presidio PII 属 l2，各自随总开关跳过
        try:
            masked_msgs, any_masked, entities = messages, False, []
            if l1_on:
                masked_msgs, any_masked, entities = norm_mask_messages(messages)
            if not any_masked and l2_on:
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
        # issue #92：判定持久化 shadow_log（实体只存命中数，不存字符串）——观测闭环供巡检/统计消费；
        # enabled 但无 verdict（prompt 缺失/API 异常等）记 error 条，可用率巡检才有数据；
        # 空 text（纯图片/工具调用请求无可判输入）整体跳过不落条——否则误计「层不可用」污染异常率
        if text and setting_value(settings, "judge", "enabled", "JUDGE_ENABLED", False):
            _t0 = time.monotonic()
            v = judge_text(text)
            _ms = int((time.monotonic() - _t0) * 1000)
            if v is not None:
                shadow_log.record("judge", hit=v["confidential"], confidence=v["confidence"],
                                  latency_ms=_ms, entities=len(v["entities"]))
                print(f"[semantic.shadow] confidential={v['confidential']} entities={','.join(v['entities']) or '-'} confidence={v['confidence']:.2f}", flush=True)
            else:
                shadow_log.record("judge", error="unavailable", latency_ms=_ms)
        # 注入检测 shadow（issue #30）：PromptGuard 2 评分 ≥阈值记日志，不阻断
        # issue #92：同上持久化；调用点显式判 enabled——enabled 且 score 为 None 即本次判定不可用；
        # 空 text 跳过不落条（同 judge 侧，code-review 修复）
        pg_threshold = setting_value(settings, "pg", "threshold", "PG_THRESHOLD", 0.7)
        if text and setting_value(settings, "pg", "enabled", "PG_ENABLED", False):
            _t0 = time.monotonic()
            score = pg_guard(text)
            _ms = int((time.monotonic() - _t0) * 1000)
            if score is not None:
                shadow_log.record("pg", hit=score >= pg_threshold, score=score, latency_ms=_ms)
                if score >= pg_threshold:
                    print(f"[injection.shadow] malicious={score:.3f} >= {pg_threshold}", flush=True)
            else:
                shadow_log.record("pg", error="unavailable", latency_ms=_ms)

    def do_PUT(self):
        # 非 admin 路径回 404 而非 BaseHTTPRequestHandler 默认 501：有意语义——
        # 对外统一"未知写操作路径 404"，不暴露服务未实现 PUT 的内部细节。
        if admin_api.handle(self, "PUT"):  # admin 平面（issue #32）：/dlp-admin/* 写端点
            return
        if self_api.handle(self, "PUT"):  # 员工自助平面（issue #74 评审 P2）：已鉴权 PUT → 显式 404
            return
        self._json(404, {})

    def do_DELETE(self):
        # 同 do_PUT：非 admin 路径有意回 404（非 501）。
        if admin_api.handle(self, "DELETE"):  # admin 平面（issue #32）
            return
        if self_api.handle(self, "DELETE"):  # 员工自助平面（issue #74 评审 P2）：已鉴权 DELETE → 显式 404
            return
        self._json(404, {})


if __name__ == "__main__":
    # 启动即打一行生效来源（issue #35）：settings.json 可读 → 配置来自 JSON 覆盖层；否则全量 env/内置默认
    _s = load_settings()
    print(f"[settings] 配置来源: {'settings.json' if _s else 'env/内置默认（settings.json 缺失/损坏）'} path={SETTINGS_PATH}", flush=True)
    # 告警巡检 daemon 线程（issue #56：alert-poller 并入）：与检测路径隔离——
    # 循环体整体 try/except，单轮异常只记日志；daemon 线程随主进程退出。
    # 先实例化 server 再 start_daemon（issue #57 P2-1 启动竞态）：ThreadingHTTPServer 构造即完成
    # bind+listen，巡检线程首轮自探活 localhost:8080/healthz 不会抢在 bind 前误报 shim 不可达
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    alert_poller.start_daemon()
    server.serve_forever()
