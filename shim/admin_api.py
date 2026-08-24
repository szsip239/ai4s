#!/usr/bin/env python3
"""DLP 统一配置 admin 平面：/dlp-admin/* 路由 + axonhub 内省鉴权 + 原子写工具。

issue #31 骨架：healthz/ping、内省鉴权（读 read_channels / 写 write_channels，isOwner 直通）、write_json_atomic（.bak 滚动）。
issue #32 配置面 CRUD：wordlist GET/PUT（整体替换 terms）、recognizers GET/POST/PUT/DELETE（regex 过 re.compile 校验）。
issue #48 EDM 文件直传：POST /dlp-admin/edm/corpus/upload?name=&filename=（raw bytes），
doc_extract 提取文本后与粘贴路径（POST /dlp-admin/edm/corpus）汇入同一 ingest。
issue #49：doc_extract 懒加载到 upload 调用点（解析库 import 失败不波及 /request /response 检测路径）；
提取文本 8M 字符上限（doc_extract.MAX_EXTRACTED_CHARS，防 zip/流扩张 OOM）。
issue #50：扫描 PDF/图片经 Tesseract OCR（shim 容器内置，本地识别不出域）。
issue #89：多项目隔离——read_project_header 统一解析/校验 X-Project-ID 头（gid 形，self
平面同用）；GET /dlp-admin/key-requests 按管理员当前项目过滤申请列表（存量无项目字段
申请视为 Default；approve/reject 不读项目头——执行按申请单记录的项目，与管理员当前
所在项目解耦，切错项目不批错单）。
issue #92：shadow 判定查询出口 GET /dlp-admin/shadow-verdicts（读级）——judge/PG shadow
判定持久化（shadow_log）的 stats + 近期记录（新到旧，不落原文），供误报观察期统计。
issue #94：judge 阈值与动作分级 schema——settings judge 段加 threshold（0~1 置信度门槛）
/action（off/shadow/warn/reject 四档）校验；消费执行在 #101 落地（/request 链路）。
issue #93：judge 采样/并发预算 schema——settings judge 段加 sample_rate（0~1 判定采样率）
/max_concurrency（≥1 judge HTTP 并发上限）校验；/request 链路消费（app.py），
采样/预算 skip 不落 shadow_log 条（非层异常，不污染 #92 error_rate）。
issue #103：PG 高分阻断 schema——settings pg 段加 block_enabled（布尔开关，默认关=纯 shadow
现状）/block_threshold（0~1 阻断阈值，默认 0.9）校验；/request 链路消费（app.py 应答前
同步判定 451），阻断事件落 shadow_log（blocked=True）由 alert_poller 巡检发飞书（脱敏）。
issue #104：注入规则层 schema——settings 新增 rules 段（必填，仅 enabled/block 两布尔键，
默认双关=先进场 shadow 观察再议阻断）；shadow-verdicts 出口 layer 过滤接受 rules、
stats 加 rules 层（判定条带 groups 命中模式组名脱敏字段）。
issue #101：judge action 消费落地（app.py /request 链路：off 不判定 / shadow 现状 /
warn 超阈值落 warned=True 条不拦截 / reject 契约不支持按 shadow）；shadow-verdicts
stats 聚合透出 warned 数（观察期误报对账口径），warn 事件由 alert_poller 巡检项 6 发飞书。

与检测路径（/request /response 调用链）完全隔离：admin 平面 fail-closed——
内省不可达回 503，不适用检测链的 fail-open 分级（契约 docs/contracts/dlp-webhook-shim.md）。
本模块自身仅标准库；doc_extract 的文档解析依赖第三方库（PyMuPDF/python-docx/openpyxl/
python-pptx/pytesseract/Pillow，全 manylinux wheel），仅 upload 端点函数级引用，
检测路径 app.py 不受影响（仍纯 stdlib）。
"""
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import edm_lib  # EDM 指纹算法共享库（issue #34）：入库/检测同法（契约铁律）
import shadow_log  # shadow 判定观测闭环（issue #92）：stdlib-only 无环；/dlp-admin/shadow-verdicts 消费

# axonhub 内省端点（容器内默认同栈服务名；测试经环境变量指向本地假服务）
AXONHUB_ADMIN_URL = os.environ.get("AXONHUB_ADMIN_URL", "http://axonhub:8090/admin/graphql")
INTROSPECT_TIMEOUT = 3  # 秒；超时按内省失败处理（fail-closed）
_ME_QUERY = "query Me { me { id email isOwner scopes } }"  # issue #79 加 email：控制台 Key 申请通道登记申请人身份用

# 配置文件路径（与 app.py 相同 env/默认值；测试用临时文件覆写模块属性）
WORDLIST_PATH = os.environ.get("WORDLIST_PATH", "/dlp/confidential-terms.json")
PII_RECOGNIZERS_PATH = os.environ.get("PII_RECOGNIZERS_PATH", "/recognizers/pii-zh.json")
FORMAT_RULES_PATH = os.environ.get("FORMAT_RULES_PATH", "/dlp/format-rules.json")
AGENTGW_CONFIG_PATH = os.environ.get("AGENTGW_CONFIG_PATH", "/agentgateway/config.yaml")
EDM_FP_PATH = os.environ.get("EDM_FP_PATH", "/edm/fingerprints.json")  # 与 app.py 同 env/默认
EDM_CORPUS_DIR = os.environ.get("EDM_CORPUS_DIR", "/edm/corpus")
SETTINGS_PATH = os.environ.get("SETTINGS_PATH", "/dlp/settings.json")  # 与 app.py 同 env/默认（issue #35）
_MAX_ADMIN_BODY = 1024 * 1024  # admin 请求体上限（配置文本足够）
_MAX_EDM_BODY = 16 * 1024 * 1024  # EDM corpus POST 放宽（review #5：真实商密文档规模可达数 MB）

# 端点级别 → 所需系统 scope（2026-08-06 定案：读 read_channels / 写 write_channels；isOwner 直通）。
_LEVEL_SCOPES = {"read": "read_channels", "write": "write_channels"}


def _respond(handler, code: int, obj) -> None:
    """admin 平面自用的 JSON 应答（不依赖 app.Handler._json，模块边界自包含）。"""
    body = json.dumps(obj, ensure_ascii=False).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _introspect(token: str):
    """caller Bearer token 透传 axonhub 内省（无缓存，每请求一次——admin 调用低频，KISS）。
    返回 (me, None) 或 (None, 错误码)：非 200 或 me 为空 → 401；
    网络错误/超时 → 503（admin 平面 fail-closed，不适用检测链 fail-open）。"""
    body = json.dumps({"query": _ME_QUERY}).encode()
    req = urllib.request.Request(
        AXONHUB_ADMIN_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=INTROSPECT_TIMEOUT) as r:
            payload = json.load(r)
    except urllib.error.HTTPError:
        return None, 401  # axonhub 拒绝（token 无效/过期）
    except Exception:
        return None, 503  # 不可达/超时/报文异常
    me = (payload.get("data") or {}).get("me")
    if not me:
        return None, 401
    return me, None


def _authorize(handler, level: str):
    """鉴权守卫：通过返回 me dict；失败已回错误响应并返回 None。"""
    auth = handler.headers.get("Authorization") or ""
    # RFC 6750：scheme 大小写不敏感（token 本身仍大小写敏感）
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        _respond(handler, 401, {"error": "missing bearer token"})
        return None
    me, err = _introspect(token)
    if err is not None:
        _respond(handler, err, {"error": "introspection unavailable" if err == 503 else "unauthorized"})
        return None
    if me.get("isOwner"):
        return me  # isOwner 直通（定案）
    need = _LEVEL_SCOPES[level]
    if need in set(me.get("scopes") or []):
        return me
    _respond(handler, 403, {"error": f"missing scope: {need}"})
    return None


def _healthz(handler, _me):
    _respond(handler, 200, {"status": "ok"})


def _load_json_file(path):
    """读配置 JSON 文件；读不到/解析失败返回 None（admin 平面 fail-closed，由调用方回 500）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _read_raw_body(handler, max_body):
    """读 admin 请求体原始字节；超限 → 已回 413 并返回 None。
    超限先分块 drain 再响应——否则客户端发送中途断连，看到 BrokenPipe 而非干净 413。"""
    raw_len = int(handler.headers.get("Content-Length") or 0)
    if raw_len > max_body:
        remaining = raw_len
        while remaining > 0:
            chunk = handler.rfile.read(min(remaining, 256 * 1024))
            if not chunk:
                break
            remaining -= len(chunk)
        _respond(handler, 413, {"error": f"body 超限: {raw_len} 字节 > 上限 {max_body}"})
        return None
    return handler.rfile.read(raw_len)


def _read_body(handler, max_body=_MAX_ADMIN_BODY):
    """读 admin 请求体 JSON；超限 → 已回 413，非法 → 已回 400，返回 None。
    路由级上限（review #5）：默认 _MAX_ADMIN_BODY（1MB），EDM corpus POST 放宽 _MAX_EDM_BODY。"""
    raw = _read_raw_body(handler, max_body)
    if raw is None:
        return None
    try:
        return json.loads(raw or b"{}")
    except Exception:
        _respond(handler, 400, {"error": "invalid JSON body"})
        return None


def _load_for_write(path: str, shell: dict, label: str):
    """写前加载（fail-closed，code-review 修复）：返回 (data, None) 或 (None, 错误信息)。
    文件不存在 → 从空壳新建（允许）；存在但读不到/损坏/非对象 → 拒绝写入，
    防止静默从空壳覆盖落盘、抹掉原 version/_comment/数据。"""
    if not os.path.exists(path):
        return shell, None
    data = _load_json_file(path)
    if not isinstance(data, dict):
        return None, f"{label} 读取失败，拒绝写入"
    return data, None


def _wordlist_get(handler, _me):
    """GET 词表全文（issue #32）。"""
    data = _load_json_file(WORDLIST_PATH)
    if data is None:
        _respond(handler, 500, {"error": "wordlist unreadable"})
        return
    _respond(handler, 200, data)


def _validate_terms(terms) -> str | None:
    """terms 校验（issue #32）：合法返回 None，非法返回具体原因。"""
    if not isinstance(terms, list):
        return "terms 必须是数组"
    seen = set()
    for i, t in enumerate(terms):
        if not isinstance(t, dict):
            return f"terms[{i}] 必须是对象"
        v, rid = t.get("value"), t.get("rule_id")
        if not isinstance(v, str) or not v:
            return f"terms[{i}].value 必须是非空字符串"
        if not isinstance(rid, str) or not rid:
            return f"terms[{i}].rule_id 必须是非空字符串"
        if v in seen:
            # 不回显词值本身（可能是敏感词）：只报下标与 rule_id
            return f"terms[{i}].value 重复 (rule_id={rid})"
        seen.add(v)
    return None


def _wordlist_put(handler, _me):
    """PUT 整体替换 terms（issue #32）：保留文件原 version/_comment；非法 400 带原因。"""
    payload = _read_body(handler)
    if payload is None:
        return
    err = _validate_terms(payload.get("terms") if isinstance(payload, dict) else None)
    if err:
        _respond(handler, 400, {"error": err})
        return
    data, err = _load_for_write(WORDLIST_PATH, {"version": 1}, "wordlist")
    if err:
        _respond(handler, 500, {"error": err})
        return
    data["terms"] = payload["terms"]
    write_json_atomic(WORDLIST_PATH, data)
    _respond(handler, 200, data)


def _recognizers_get(handler, _me):
    """GET recognizers 全文（issue #32）。"""
    data = _load_json_file(PII_RECOGNIZERS_PATH)
    if data is None:
        _respond(handler, 500, {"error": "recognizers unreadable"})
        return
    _respond(handler, 200, data)


def _format_rules_get(handler, _me):
    """GET format-rules 全文（issue #33）。"""
    data = _load_json_file(FORMAT_RULES_PATH)
    if data is None:
        _respond(handler, 500, {"error": "format-rules unreadable"})
        return
    _respond(handler, 200, data)


def _settings_get(handler, _me):
    """GET settings 全文（issue #35）。文件缺失 → 404（缺失是合法 env 兜底态，review #3，非故障）；
    存在但损坏 → 500（故障态）。"""
    if not os.path.exists(SETTINGS_PATH):
        _respond(handler, 404, {"error": "settings.json 不存在，当前为 env 兜底态"})
        return
    data = _load_json_file(SETTINGS_PATH)
    if data is None:
        _respond(handler, 500, {"error": "settings unreadable"})
        return
    _respond(handler, 200, data)


# settings schema（issue #35）：顶层/区段未知键一律拒绝（防 typo 静默漂移，同 format-rules 校验精神）
_SETTINGS_TOP_KEYS = {"version", "_comment", "judge", "edm", "pg", "rules", "l1", "l2", "response"}
_SETTINGS_JUDGE_KEYS = {"enabled", "model", "base_url", "timeout", "prompt_system", "prompt_fewshot",
                        "threshold", "action",  # threshold/action：issue #94 置信度门槛与动作分级
                        "sample_rate", "max_concurrency"}  # issue #93 判定采样率与并发预算
_SETTINGS_EDM_KEYS = {"enabled", "min_hits"}
_SETTINGS_PG_KEYS = {"enabled", "threshold", "normalize",  # normalize：issue #44 打分前置归一化开关
                     "block_enabled", "block_threshold"}  # 阻断试点（issue #103）：开关 + 阻断阈值
# 注入规则层（issue #104）：enabled=层开关（默认关，先进场 shadow 观察）/block=命中即 451
# （默认关；规则命中是布尔无分数，无阈值概念——与 pg 双键形态不同）
_SETTINGS_RULES_KEYS = {"enabled", "block"}
# 分层总开关（issue #40）：单键段
_SETTINGS_L1_KEYS = {"enabled"}
_SETTINGS_L2_KEYS = {"enabled"}
_SETTINGS_RESPONSE_KEYS = {"enabled"}


# judge 动作分级（issue #94）：off 关 / shadow 仅记录 / warn 告警 / reject 拦截；
# issue #101 消费落地：warn=告警不拦截，reject 契约「语义层永不阻断」不支持（按 shadow 处理）
_SETTINGS_JUDGE_ACTIONS = {"off", "shadow", "warn", "reject"}


def _is_number(v) -> bool:
    """JSON 数值（排除 bool——Python bool 是 int 子类）。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate_settings(data) -> str | None:
    """settings JSON 校验（issue #35）：合法返回 None，非法返回具体原因。
    整体替换语义：judge/edm/pg 三段必填且字段齐全（部分区段更新会静默丢键，不允许）。"""
    if not isinstance(data, dict):
        return "settings 必须是对象"
    for k in data:
        if k not in _SETTINGS_TOP_KEYS:
            return f"未知顶层键: {k}"
    if "version" in data and (not isinstance(data["version"], int) or isinstance(data["version"], bool)):
        return "version 必须是整数"
    if "_comment" in data and not isinstance(data["_comment"], str):
        return "_comment 必须是字符串"
    allowed = {"judge": _SETTINGS_JUDGE_KEYS, "edm": _SETTINGS_EDM_KEYS, "pg": _SETTINGS_PG_KEYS,
               "rules": _SETTINGS_RULES_KEYS,
               "l1": _SETTINGS_L1_KEYS, "l2": _SETTINGS_L2_KEYS, "response": _SETTINGS_RESPONSE_KEYS}
    for section, keys in allowed.items():
        sec = data.get(section)
        if not isinstance(sec, dict):
            return f"{section} 必须是对象"
        for k in sec:
            if k not in keys:
                return f"{section} 未知字段: {k}"
        missing = keys - set(sec)
        if missing:
            return f"{section} 缺字段: {', '.join(sorted(missing))}"
    judge, edm, pg = data["judge"], data["edm"], data["pg"]
    if not isinstance(judge["enabled"], bool):
        return "judge.enabled 必须是布尔值"
    for k in ("model", "base_url", "prompt_system", "prompt_fewshot"):
        if not isinstance(judge[k], str) or not judge[k]:
            return f"judge.{k} 必须是非空字符串"
    if not _is_number(judge["timeout"]) or judge["timeout"] <= 0:
        return "judge.timeout 必须是 >0 数值"
    # issue #94：threshold 0~1 置信度门槛 + action 四档（#101 起 /request 链路消费）
    if not _is_number(judge["threshold"]) or not 0 <= judge["threshold"] <= 1:
        return "judge.threshold 必须是 0~1 数值"
    # 先 str 再查档位集合：list/dict 等 unhashable 值直接 not in 会抛 TypeError 断连（#94 评审）
    if not isinstance(judge["action"], str) or judge["action"] not in _SETTINGS_JUDGE_ACTIONS:
        return "judge.action 必须是 off/shadow/warn/reject 之一"
    # issue #93：sample_rate 0~1 判定采样率 + max_concurrency ≥1 并发预算（/request 链路消费）
    if not _is_number(judge["sample_rate"]) or not 0 <= judge["sample_rate"] <= 1:
        return "judge.sample_rate 必须是 0~1 数值"
    if not isinstance(judge["max_concurrency"], int) or isinstance(judge["max_concurrency"], bool) or judge["max_concurrency"] < 1:
        return "judge.max_concurrency 必须是 ≥1 整数"
    if not isinstance(edm["enabled"], bool):
        return "edm.enabled 必须是布尔值"
    if not isinstance(edm["min_hits"], int) or isinstance(edm["min_hits"], bool) or edm["min_hits"] < 1:
        return "edm.min_hits 必须是 ≥1 整数"
    if not isinstance(pg["enabled"], bool):
        return "pg.enabled 必须是布尔值"
    if not _is_number(pg["threshold"]) or not 0 <= pg["threshold"] <= 1:
        return "pg.threshold 必须是 0~1 数值"
    if not isinstance(pg["normalize"], bool):
        return "pg.normalize 必须是布尔值"
    # issue #103：阻断开关必填布尔；阻断阈值必填 0~1 数值（消费在 app.py /request 应答前同步判定）
    if not isinstance(pg["block_enabled"], bool):
        return "pg.block_enabled 必须是布尔值"
    if not _is_number(pg["block_threshold"]) or not 0 <= pg["block_threshold"] <= 1:
        return "pg.block_threshold 必须是 0~1 数值"
    # issue #104：rules 段必填两布尔键（规则命中是布尔无分数，无阈值概念）
    for k in ("enabled", "block"):
        if not isinstance(data["rules"][k], bool):
            return f"rules.{k} 必须是布尔值"
    for section in ("l1", "l2", "response"):  # 分层总开关（issue #40）：仅 enabled 单键
        if not isinstance(data[section]["enabled"], bool):
            return f"{section}.enabled 必须是布尔值"
    return None


# 分层总开关 env 名（issue #40，与 app.py setting_value 调用点一致）
_LAYER_ENV = {"l1": "L1_ENABLED", "l2": "L2_ENABLED", "response": "RESPONSE_ENABLED"}


def _layer_enabled(section: str) -> bool:
    """分层总开关读取（issue #40）：settings.json[section].enabled > env > 默认 True。
    与 app.py setting_value 同三级语义（admin_api 自包含不 import app，按 bool 单键简化实现）；
    文件缺失/损坏/键类型不符 → env/默认 True（保现网行为方向，admin 平面读配置用）。"""
    data = _load_json_file(SETTINGS_PATH)
    if isinstance(data, dict):
        sec = data.get(section)
        if isinstance(sec, dict) and isinstance(sec.get("enabled"), bool):
            return sec["enabled"]
    v = os.environ.get(_LAYER_ENV[section], "")
    if v != "":
        return v == "1"
    return True


def _settings_put(handler, _me):
    """PUT 整体替换 settings（issue #35）：校验 → write_json_atomic。
    shim 检测路径每请求重读 settings.json，写入即热生效，无需重启。
    l1 总开关联动（issue #40）：l1.enabled 翻转时按新状态重渲染 config.yaml 标记区块
    （关=区块渲染为空，网关层同步撤掉格式规则）；渲染失败从 .bak 回滚 settings 并 500，
    两侧不留半更新（对称 format-rules PUT 纪律）。手改 settings.json 不触发联动，
    漂移时用 POST /dlp-admin/format-rules/render 兜底修复。"""
    payload = _read_body(handler)
    if payload is None:
        return
    err = _validate_settings(payload)
    if err:
        _respond(handler, 400, {"error": err})
        return
    old_l1 = _layer_enabled("l1")  # 写前读旧状态（文件缺失/env 兜底均按默认 True 语义）
    try:
        write_json_atomic(SETTINGS_PATH, payload)
    except OSError as e:
        _respond(handler, 500, {"error": f"settings 写入失败: {e}"})
        return
    new_l1 = _layer_enabled("l1")  # 写后读新状态：文件级优先于 env，PUT 整体替换后必读到 payload 新值
    if old_l1 != new_l1:
        data = _load_json_file(FORMAT_RULES_PATH)
        if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
            _rollback_settings_json()
            _respond(handler, 500, {"error": "format-rules unreadable，无法联动渲染（settings 已回滚）"})
            return
        rerr = _render_to_config(data["rules"], include_l1=new_l1)
        if rerr:
            _rollback_settings_json()
            _respond(handler, 500, {"error": f"{rerr}（settings 已回滚）"})
            return
    _respond(handler, 200, payload)


def _rollback_settings_json() -> None:
    """settings 已写但 l1 联动渲染失败时回滚：.bak 恢复（无 .bak 说明此前无文件，直接删除），两侧不留半更新。"""
    if os.path.exists(SETTINGS_PATH + ".bak"):
        shutil.copyfile(SETTINGS_PATH + ".bak", SETTINGS_PATH)
    elif os.path.exists(SETTINGS_PATH):
        os.unlink(SETTINGS_PATH)


# gateway_patterns 禁用的 Rust regex 不支持构造（lookaround；backreference 另行 \1~\9 扫描）
_RUST_UNSUPPORTED = ("(?=", "(?!", "(?<=", "(?<!")
_BACKREF_RE = re.compile(r"\\[1-9]")


def _check_pattern(p, label: str) -> str | None:
    """单条 pattern 校验：非空字符串 + 过 re.compile。"""
    if not isinstance(p, str) or not p:
        return f"{label} 必须是非空字符串"
    try:
        re.compile(p)
    except re.error as e:
        return f"{label} 非法: {e}"
    return None


def _validate_format_rules(data) -> str | None:
    """format-rules JSON 校验（issue #33）：合法返回 None，非法返回具体原因。
    schema：每条 code/layer/action/enabled 必填，action∈{reject,mask}，layer∈{L1,L1.5}；
    全部 patterns 过 re.compile；gateway_patterns 禁 Rust regex 不支持构造（lookaround/backreference）。"""
    if not isinstance(data, dict):
        return "format-rules 必须是对象"
    rules = data.get("rules")
    if not isinstance(rules, list):
        return "rules 必须是数组"
    seen = set()
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            return f"rules[{i}] 必须是对象"
        code = r.get("code")
        if not isinstance(code, str) or not code:
            return f"rules[{i}].code 必须是非空字符串"
        if code in seen:
            return f"rules[{i}].code 重复: {code}"
        seen.add(code)
        if r.get("layer") not in ("L1", "L1.5"):
            return f"rules[{i}].layer 必须是 L1/L1.5"
        action = r.get("action")
        if action not in ("reject", "mask"):
            return f"rules[{i}].action 必须是 reject/mask"
        if not isinstance(r.get("enabled"), bool):
            return f"rules[{i}].enabled 必须是布尔值"
        if action == "reject" and (not isinstance(r.get("message"), str) or not r["message"]):
            return f"rules[{i}].message 必须是非空字符串（reject 需要，用于 rejection body）"
        gp = r.get("gateway_patterns")
        if not isinstance(gp, list):
            return f"rules[{i}].gateway_patterns 必须是数组"
        for j, p in enumerate(gp):
            err = _check_pattern(p, f"rules[{i}].gateway_patterns[{j}]")
            if err:
                return err
            for bad in _RUST_UNSUPPORTED:
                if bad in p:
                    return f"rules[{i}].gateway_patterns[{j}] 含 Rust regex 不支持的构造: {bad}"
            if _BACKREF_RE.search(p):
                return f"rules[{i}].gateway_patterns[{j}] 含 Rust regex 不支持的 backreference"
        sp = r.get("shim_patterns", [])
        if not isinstance(sp, list):
            return f"rules[{i}].shim_patterns 必须是数组"
        for j, p in enumerate(sp):
            err = _check_pattern(p, f"rules[{i}].shim_patterns[{j}]")
            if err:
                return err
    return None


def _format_rules_put(handler, _me):
    """PUT 整体替换 format-rules（issue #33）：校验 → 写 JSON → 渲染 splice 进 config.yaml。
    config 渲染/写失败时 JSON 从 .bak 回滚，两侧不留半更新。"""
    payload = _read_body(handler)
    if payload is None:
        return
    err = _validate_format_rules(payload)
    if err:
        _respond(handler, 400, {"error": err})
        return
    try:
        write_json_atomic(FORMAT_RULES_PATH, payload)
    except OSError as e:
        # JSON 写失败（review #2）：与 YAML 写失败对称 500；此处 config.yaml 尚未触碰
        _respond(handler, 500, {"error": f"format-rules 写入失败: {e}"})
        return
    rerr = _render_to_config(payload["rules"], include_l1=_layer_enabled("l1"))
    if rerr:
        _rollback_format_rules_json()
        _respond(handler, 500, {"error": f"{rerr}（JSON 已回滚）"})
        return
    _respond(handler, 200, payload)


def _yaml_single_quote(s: str) -> str:
    """YAML 单引号标量（内嵌单引号翻倍）。"""
    return "'" + s.replace("'", "''") + "'"


def render_gateway_block(rules: list, include_l1: bool = True) -> str:
    """渲染 promptGuard request 段的 - regex 条目文本（issue #33）。
    缩进对齐现网（条目 12 空格级，模板化确定性构造兜底 stdlib 无 YAML 解析器）；
    enabled=false 或 gateway_patterns 为空（shim-only）的规则不渲染进网关。
    l1 总开关（issue #40）：include_l1=False 时格式规则全族不渲染（返回空串，标记区块渲染为空，
    网关层同步撤防——l1 管辖 format-rules.json 全族，含 L1 reject 与 L1.5 格式 mask）。"""
    lines = []
    for r in rules:
        patterns = r.get("gateway_patterns") or []
        if not include_l1 or not r.get("enabled") or not patterns:
            continue
        lines.append("            - regex:")
        lines.append(f"                action: {r['action']}")
        lines.append("                rules:")
        for p in patterns:
            lines.append(f"                  - pattern: {_yaml_single_quote(p)}")
        if r["action"] == "reject":
            body = json.dumps(
                {"error": {"message": f"Blocked by ai4s DLP: {r['message']}",
                           "type": "content_policy_violation", "code": r["code"]}},
                ensure_ascii=False, separators=(",", ":"))
            lines.append("              rejection:")
            lines.append("                status: 451")
            lines.append("                headers:")
            lines.append("                  set:")
            lines.append('                    content-type: "application/json"')
            lines.append("                body: |")
            lines.append(f"                  {body}")
    return ("\n".join(lines) + "\n") if lines else ""


# config.yaml 一次性标记区块（issue #33）：渲染内容替换 BEGIN/END 之间，区块外手改不动
_BEGIN_MARK = "# >>> DLP-FORMAT-RULES BEGIN"
_END_MARK = "# <<< DLP-FORMAT-RULES END"


def splice_rendered(config_text: str, block: str) -> str:
    """用渲染文本替换 BEGIN/END 标记行之间的内容（标记行保留）。标记缺失/顺序错 → ValueError。"""
    lines = config_text.splitlines(keepends=True)
    marks = [i for i, l in enumerate(lines)
             if l.strip().startswith(_BEGIN_MARK) or l.strip().startswith(_END_MARK)]
    if len(marks) != 2 or not lines[marks[0]].strip().startswith(_BEGIN_MARK):
        raise ValueError("config.yaml 缺少 DLP-FORMAT-RULES BEGIN/END 标记（或顺序错误）")
    b, e = marks
    return "".join(lines[:b + 1]) + block + "".join(lines[e:])


def _verify_spliced(text: str, rules: list, include_l1: bool = True) -> None:
    """渲染后校验（issue #33）：标记完整 + 每条启用规则的 gateway_patterns 与 reject code 均在文本中。
    l1 总开关（issue #40）：include_l1=False 时整族规则被排除在渲染外，逐规则校验同步跳过
    （否则恒 500）；标记完整性校验仍保留。"""
    if _BEGIN_MARK not in text or _END_MARK not in text:
        raise ValueError("渲染后校验失败: 标记缺失")
    if not include_l1:
        return
    for r in rules:
        if not r.get("enabled"):
            continue
        for p in r.get("gateway_patterns") or []:
            # pattern 原文含 ' 时渲染进 YAML 单引号标量会翻倍（review #3）：比对转义后形态，否则恒假误 500
            if p.replace("'", "''") not in text:
                raise ValueError(f"渲染后校验失败: pattern 未落文本 ({r.get('code')})")
        if r.get("action") == "reject" and f'"code":"{r["code"]}"' not in text:
            raise ValueError(f"渲染后校验失败: rejection code 未落文本 ({r.get('code')})")


def _render_to_config(rules, include_l1: bool = True) -> str | None:
    """读 config.yaml → 渲染 splice 标记区块 → 渲染后校验 → 原子写盘（PUT/render 共用，review #6）。
    成功返回 None；渲染/校验失败或写盘失败返回错误消息（渲染失败时未落盘）。
    include_l1 透传 l1 总开关（issue #40）：False 时标记区块渲染为空。"""
    try:
        with open(AGENTGW_CONFIG_PATH, encoding="utf-8") as f:
            config_text = f.read()
        new_config = splice_rendered(config_text, render_gateway_block(rules, include_l1))
        _verify_spliced(new_config, rules, include_l1)
    except (OSError, ValueError) as e:
        return f"渲染失败: {e}"
    try:
        write_text_atomic(AGENTGW_CONFIG_PATH, new_config)
    except OSError as e:
        return f"config.yaml 写入失败: {e}"
    return None


def _rollback_format_rules_json() -> None:
    """JSON 已写但 config 渲染/写盘失败时回滚：.bak 恢复（无 .bak 说明此前无文件，直接删除），两侧不留半更新。"""
    if os.path.exists(FORMAT_RULES_PATH + ".bak"):
        shutil.copyfile(FORMAT_RULES_PATH + ".bak", FORMAT_RULES_PATH)
    elif os.path.exists(FORMAT_RULES_PATH):
        os.unlink(FORMAT_RULES_PATH)


def _format_rules_render_post(handler, _me):
    """POST 幂等重渲染（issue #33）：按 JSON 当前内容重渲染 config.yaml 标记区块。
    用于区块被手改漂移后的修复；JSON 损坏/缺标记拒绝渲染，不落盘。
    l1 总开关（issue #40）：l1.enabled=false 时标记区块渲染为空（rendered=0），
    也是手改 settings.json 后同步网关层的兜底入口。"""
    data = _load_json_file(FORMAT_RULES_PATH)
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        _respond(handler, 500, {"error": "format-rules unreadable，拒绝渲染"})
        return
    rules = data["rules"]
    include_l1 = _layer_enabled("l1")
    rerr = _render_to_config(rules, include_l1=include_l1)
    if rerr:
        _respond(handler, 500, {"error": rerr})
        return
    rendered = sum(1 for r in rules if include_l1 and r.get("enabled") and r.get("gateway_patterns"))
    _respond(handler, 200, {"rendered": rendered})


def _edm_doc_summary(name: str, doc) -> dict:
    """单文档列表项（issue #34）：兼容旧格式纯 shingle 数组；旧文档无 added_at → None。"""
    if isinstance(doc, list):  # 旧格式（issue #29 初版）：纯 shingle 数组，无行级/时间
        return {"name": name, "shingle_count": len(doc), "line_count": 0, "added_at": None}
    return {"name": name,
            "shingle_count": len(doc.get("shingles") or []),
            "line_count": len(doc.get("lines") or []),
            "added_at": doc.get("added_at")}


def _edm_corpus_get(handler, _me):
    """GET EDM 语料文档列表（issue #34）：名称/shingle 数/行级数/入库时间。"""
    data = _load_json_file(EDM_FP_PATH)
    if not isinstance(data, dict) or not isinstance(data.get("docs"), dict):
        _respond(handler, 500, {"error": "edm fingerprints unreadable"})
        return
    out = [_edm_doc_summary(name, doc) for name, doc in sorted(data["docs"].items())]
    _respond(handler, 200, out)


_EDM_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")  # 禁 / 防路径穿越；corpus 文件名 = <name>.txt
# 整文档归一化最小长度（review #1）：值与 edm_lib.LINE_MIN 对齐，但语义独立——
# 这是入库下限（低于此：行级通道无有效指纹，整段 shingle 单指纹也达不到 EDM_MIN_HITS=2，入库即死规则），
# 不借用 LINE_MIN 以免两处语义耦合演化。
_EDM_MIN_TEXT = 12


def _edm_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _edm_ingest_text(handler, name: str, text: str):
    """EDM 语料入库共用段（粘贴 JSON 与文件直传两条上传路径汇入，issue #48）：
    text 校验 → corpus 原文原子写 → 该文档指纹全量重算并入 fingerprints.json（不动其他文档）
    → 原子写。指纹写失败时回滚删 corpus 文件，两侧不留半更新。"""
    if len(edm_lib.normalize(text)) < _EDM_MIN_TEXT:
        _respond(handler, 400, {"error": f"text 过短：归一化后不足 {_EDM_MIN_TEXT} 字符，无法产生有效行级指纹"
                                          "（整段 shingle 单指纹也达不到命中阈值 2，入库即死规则）"})
        return
    store, lerr = _load_for_write(EDM_FP_PATH, {"version": 1, "docs": {}}, "edm fingerprints")
    if lerr:
        _respond(handler, 500, {"error": lerr})
        return
    if not isinstance(store.get("docs"), dict):  # schema 检查（非 _load_for_write 职责）：docs 必须对象
        _respond(handler, 500, {"error": "edm fingerprints unreadable，拒绝覆盖写入"})
        return
    if name in store["docs"]:
        _respond(handler, 400, {"error": f"文档已存在: {name}"})
        return
    now = _edm_now()
    fps = edm_lib.doc_fingerprints(text)
    os.makedirs(EDM_CORPUS_DIR, exist_ok=True)
    corpus_path = os.path.join(EDM_CORPUS_DIR, name + ".txt")
    try:
        write_text_atomic(corpus_path, text)
    except OSError as e:
        _respond(handler, 500, {"error": f"corpus 写入失败: {e}"})
        return
    store["docs"][name] = {"shingles": fps["shingles"], "lines": fps["lines"], "added_at": now}
    store["updated_at"] = now
    try:
        write_json_atomic(EDM_FP_PATH, store)
    except OSError as e:
        try:
            os.unlink(corpus_path)  # 回滚刚写的 corpus 文件，两侧不留半更新
        except OSError:
            pass
        _respond(handler, 500, {"error": f"fingerprints 写入失败（corpus 已回滚）: {e}"})
        return
    _respond(handler, 200, {"name": name, "shingle_count": len(fps["shingles"]),
                            "line_count": len(fps["lines"]), "added_at": now})


def _edm_corpus_post(handler, _me):
    """POST 新增 EDM 语料文档（issue #34，粘贴路径）：name/text 校验后汇入 _edm_ingest_text。"""
    payload = _read_body(handler, _MAX_EDM_BODY)  # EDM 文档放宽 16MB（review #5）
    if payload is None:
        return
    name = payload.get("name") if isinstance(payload, dict) else None
    if not isinstance(name, str) or not _EDM_NAME_RE.match(name):
        _respond(handler, 400, {"error": "name 必须匹配 [A-Za-z0-9_.-]{1,64}"})
        return
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        _respond(handler, 400, {"error": "text 必须是非空字符串"})
        return
    _edm_ingest_text(handler, name, text)


def _edm_corpus_upload_post(handler, _me):
    """POST 文件直传 EDM 语料（issue #48）：raw bytes body（application/octet-stream），
    ?name=<入库名>&filename=<原始文件名>。doc_extract 按扩展名提取文本
    （PDF/DOCX/XLSX/PPTX 解析，文本类多编码解码，扫描 PDF/图片 Tesseract OCR——issue #50）
    后汇入 _edm_ingest_text；提取不截断——16MB 已由 HTTP 体上限拦截，提取文本 8M 字符上限
    （doc_extract 内，防 zip/流扩张 OOM，issue #49 P1-1）；.doc 等明确拒绝，错误文案用户可见。"""
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    name = (qs.get("name") or [""])[0]
    filename = (qs.get("filename") or [""])[0]
    if not _EDM_NAME_RE.match(name):
        _respond(handler, 400, {"error": "name 必须匹配 [A-Za-z0-9_.-]{1,64}"})
        return
    if not filename:
        _respond(handler, 400, {"error": "缺少 filename 参数（取扩展名决定解析方式）"})
        return
    data = _read_raw_body(handler, _MAX_EDM_BODY)
    if data is None:
        return
    import doc_extract  # 懒加载（issue #49 P2-7）：解析库仅本端点需要，import 失败不波及检测路径
    try:
        # 扫描 PDF/图片内部走 Tesseract OCR（issue #50）；OCR 不可用/全空/超限均抛专用子类
        text = doc_extract.extract_text_from_bytes(filename, data)
    except doc_extract.DocumentExtractionError as e:
        _respond(handler, 400, {"error": str(e)})
        return
    _edm_ingest_text(handler, name, text)


def _edm_corpus_delete_item(handler, _me, name):
    """DELETE 删除 EDM 语料文档（issue #34）：指纹条目（权威）+ corpus 文件；不存在 → 404。
    name 正则校验与 POST 同款（review #4 双保险：薄壳 quote 误放行的分隔符等在此兜底）。
    先写指纹库（检测权威源），corpus 文件缺失容忍（孤儿文件不阻断删除）。"""
    if not _EDM_NAME_RE.match(name):
        _respond(handler, 400, {"error": "name 必须匹配 [A-Za-z0-9_.-]{1,64}"})
        return
    store = _load_json_file(EDM_FP_PATH)
    if not isinstance(store, dict) or not isinstance(store.get("docs"), dict):
        _respond(handler, 500, {"error": "edm fingerprints unreadable"})
        return
    if name not in store["docs"]:
        _respond(handler, 404, {"error": f"文档不存在: {name}"})
        return
    del store["docs"][name]
    store["updated_at"] = _edm_now()
    try:
        write_json_atomic(EDM_FP_PATH, store)
    except OSError as e:
        # 与 POST 对称（review #6）：指纹写失败干净 500，corpus 文件不动（条目仍在库，两侧一致）
        _respond(handler, 500, {"error": f"fingerprints 写入失败: {e}"})
        return
    try:
        os.unlink(os.path.join(EDM_CORPUS_DIR, name + ".txt"))
    except FileNotFoundError:
        pass  # corpus 缺失容忍：指纹库为权威列表
    except OSError as e:
        _respond(handler, 500, {"error": f"指纹已删除但 corpus 文件删除失败（残留孤儿）: {e}"})
        return
    _respond(handler, 200, {"deleted": name})


def _validate_recognizer_fields(rec) -> str | None:
    """recognizer 字段校验（issue #32，POST/PUT 共用）：合法返回 None，非法返回具体原因。
    regex 必须过 re.compile（错误带 re 原因）；score 0~1 数值；entity/replacement 非空。"""
    if not isinstance(rec, dict):
        return "recognizer 必须是对象"
    if not isinstance(rec.get("entity"), str) or not rec["entity"]:
        return "entity 必须是非空字符串"
    patterns = rec.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        return "patterns 必须是非空数组"
    for i, p in enumerate(patterns):
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"]:
            return f"patterns[{i}].name 必须是非空字符串"
        rgx = p.get("regex")
        if not isinstance(rgx, str) or not rgx:
            return f"patterns[{i}].regex 必须是非空字符串"
        try:
            re.compile(rgx)
        except re.error as e:
            return f"patterns[{i}].regex 非法: {e}"
        score = p.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
            return f"patterns[{i}].score 必须是 0~1 数值"
    if not isinstance(rec.get("replacement"), str) or not rec["replacement"]:
        return "replacement 必须是非空字符串"
    if "context" in rec:  # 提供时必须是字符串数组；不提供由端点默认 []
        ctx = rec["context"]
        if not isinstance(ctx, list) or not all(isinstance(c, str) for c in ctx):
            return "context 必须为字符串数组"
    return None


def _recognizers_post(handler, _me):
    """POST 新增一个 recognizer（issue #32）；context 可缺省（默认 []）。
    校验：name 非空且不与现有重复；字段规则见 _validate_recognizer_fields。"""
    payload = _read_body(handler)
    if payload is None:
        return
    data, err = _load_for_write(PII_RECOGNIZERS_PATH, {"version": 1, "recognizers": []}, "recognizers")
    if err:
        _respond(handler, 500, {"error": err})
        return
    recs = data.setdefault("recognizers", [])
    name = payload.get("name") if isinstance(payload, dict) else None
    if not isinstance(name, str) or not name:
        _respond(handler, 400, {"error": "name 必须是非空字符串"})
        return
    if any(isinstance(r, dict) and r.get("name") == name for r in recs):
        _respond(handler, 400, {"error": f"name 与现有 recognizer 重复: {name}"})
        return
    err = _validate_recognizer_fields(payload)
    if err:
        _respond(handler, 400, {"error": err})
        return
    payload.setdefault("context", [])
    recs.append(payload)
    write_json_atomic(PII_RECOGNIZERS_PATH, data)
    _respond(handler, 200, data)


def _recognizer_put_item(handler, _me, name):
    """PUT 替换指定 name 的 recognizer（issue #32）：字段校验同 POST，name 以 URL 为准；不存在 → 404。"""
    payload = _read_body(handler)
    if payload is None:
        return
    data, err = _load_for_write(PII_RECOGNIZERS_PATH, {"version": 1, "recognizers": []}, "recognizers")
    if err:
        _respond(handler, 500, {"error": err})
        return
    recs = data.setdefault("recognizers", [])
    idx = next((i for i, r in enumerate(recs) if isinstance(r, dict) and r.get("name") == name), None)
    if idx is None:
        _respond(handler, 404, {"error": f"recognizer 不存在: {name}"})
        return
    err = _validate_recognizer_fields(payload)
    if err:
        _respond(handler, 400, {"error": err})
        return
    payload.setdefault("context", [])
    payload["name"] = name  # name 以 URL 为准（body 里的 name 字段忽略）
    recs[idx] = payload
    write_json_atomic(PII_RECOGNIZERS_PATH, data)
    _respond(handler, 200, data)


def _recognizer_delete_item(handler, _me, name):
    """DELETE 删除指定 name 的 recognizer（issue #32）；不存在 → 404；删空数组允许。"""
    data, err = _load_for_write(PII_RECOGNIZERS_PATH, {"version": 1, "recognizers": []}, "recognizers")
    if err:
        _respond(handler, 500, {"error": err})
        return
    recs = data.setdefault("recognizers", [])
    kept = [r for r in recs if not (isinstance(r, dict) and r.get("name") == name)]
    if len(kept) == len(recs):
        _respond(handler, 404, {"error": f"recognizer 不存在: {name}"})
        return
    data["recognizers"] = kept
    write_json_atomic(PII_RECOGNIZERS_PATH, data)
    _respond(handler, 200, data)


def _ping(handler, me):
    """内省结果透传（读级）。"""
    _respond(handler, 200, {
        "user_id": me.get("id"),
        "is_owner": bool(me.get("isOwner")),
        "scopes": me.get("scopes") or [],
    })


# ---- 控制台 Key 申请审批（issue #79）：列表读级、点批写级；执行/通知在 key_requests ----
# key_requests 函数级懒加载：其顶层 import alert_poller→admin_api，admin_api 顶层互导会成环


def read_project_header(handler):
    """X-Project-ID 头（issue #89 员工自助面/审批多项目隔离）：合法形 gid://axonhub/Project/<id>
    （控制台 projectStore 存的就是 gid）。返回 gid 或 None（缺失/格式非法——调用方 400 兜底）。
    self 平面同用（self_api import 本模块，无环）。"""
    raw = (handler.headers.get("X-Project-ID") or "").strip()
    if not raw.startswith("gid://axonhub/Project/") or len(raw) <= len("gid://axonhub/Project/"):
        return None
    return raw


def _kr_list(handler, _me):
    """GET 申请列表（新到旧）。issue #89：按管理员当前项目（X-Project-ID）过滤，
    无项目字段的存量申请视为 Default；头缺失/非法 400。"""
    import key_requests
    pid = read_project_header(handler)
    if not pid:
        _respond(handler, 400, {"error": "缺少项目上下文（X-Project-ID 头），请先在控制台选择项目"})
        return
    try:
        reqs = key_requests.list_requests(project_id=pid)
    except Exception as e:
        print(f"[admin] key 申请列表失败: {type(e).__name__}: {e}", flush=True)
        _respond(handler, 503, {"error": "request store unavailable"})
        return
    _respond(handler, 200, {"requests": reqs})


def _kr_resolve(handler, _me, rid, action):
    """approve/reject 共用：状态门幂等（非 pending 返回现状）；approve 执行失败 502 保持 pending。
    issue #81：approve 可带 body {"tier": "<档名>"} 覆盖执行档位（新建默认体验档、提额默认所求档）。"""
    import key_requests
    reason = ""
    tier = ""
    if action in ("approve", "reject"):
        payload = _read_body(handler)
        if payload is None:
            return  # 413/400 已回出
        if isinstance(payload, dict):
            if action == "reject":
                reason = payload.get("reason") or ""
            else:
                tier = payload.get("tier") or ""
    try:
        req, err = key_requests.resolve_request(rid, action, reason, tier_override=tier)
    except Exception as e:
        print(f"[admin] key 申请点批异常 {rid}: {type(e).__name__}: {e}", flush=True)
        _respond(handler, 503, {"error": "request store unavailable"})
        return
    if err:
        _respond(handler, err[0], {"error": err[1]})
        return
    _respond(handler, 200, {"request": req})


def _kr_approve_item(handler, me, rid):
    _kr_resolve(handler, me, rid, "approve")


def _kr_reject_item(handler, me, rid):
    _kr_resolve(handler, me, rid, "reject")


# ---- shadow 判定查询出口（issue #92，读级）：观测闭环数据的管理面出口 ----


def _shadow_verdicts(handler, _me):
    """GET /dlp-admin/shadow-verdicts：三层 stats + 近期判定记录（新到旧，不落原文）。
    query 参数：n（默认 50，1..500 截断）、layer（judge/pg/rules 过滤，非法值 400）。
    issue #101：stats 透出 warned 聚合数（judge warn 试点观察期误报对账口径：
    warned/hits 同窗可比），records 逐条带 warned/model 脱敏字段供核对。
    issue #104：rules 层（注入规则层）判定条同槽透出，records 带 groups 命中模式组名。"""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    try:
        n = int((q.get("n") or ["50"])[0])
    except ValueError:
        n = 50
    n = max(1, min(500, n))
    layer = (q.get("layer") or [""])[0] or None
    if layer not in (None, "judge", "pg", "rules"):
        _respond(handler, 400, {"error": "layer 必须是 judge、pg 或 rules"})
        return
    _respond(handler, 200, {
        "stats": {l: shadow_log.stats(l) for l in ("judge", "pg", "rules")},
        "records": shadow_log.tail(n, layer=layer),
    })


# 路由表：(方法, 路径) -> (鉴权级别 | None, 端点)
_ROUTES = {
    ("GET", "/dlp-admin/healthz"): (None, _healthz),
    ("GET", "/dlp-admin/ping"): ("read", _ping),
    ("GET", "/dlp-admin/wordlist"): ("read", _wordlist_get),
    ("PUT", "/dlp-admin/wordlist"): ("write", _wordlist_put),
    ("GET", "/dlp-admin/recognizers"): ("read", _recognizers_get),
    ("POST", "/dlp-admin/recognizers"): ("write", _recognizers_post),
    ("GET", "/dlp-admin/format-rules"): ("read", _format_rules_get),
    ("PUT", "/dlp-admin/format-rules"): ("write", _format_rules_put),
    ("POST", "/dlp-admin/format-rules/render"): ("write", _format_rules_render_post),
    ("GET", "/dlp-admin/edm/corpus"): ("read", _edm_corpus_get),
    ("POST", "/dlp-admin/edm/corpus"): ("write", _edm_corpus_post),
    ("POST", "/dlp-admin/edm/corpus/upload"): ("write", _edm_corpus_upload_post),
    ("GET", "/dlp-admin/settings"): ("read", _settings_get),
    ("PUT", "/dlp-admin/settings"): ("write", _settings_put),
    ("GET", "/dlp-admin/key-requests"): ("read", _kr_list),  # issue #79：控制台 Key 申请审批
    ("GET", "/dlp-admin/shadow-verdicts"): ("read", _shadow_verdicts),  # issue #92：shadow 观测出口
}


# 参数化路由：/dlp-admin/recognizers/<name>、/dlp-admin/edm/corpus/<name>（URL 末段为 name 参数，PUT/DELETE 用）
_ITEM_ROUTES = {
    ("PUT", "/dlp-admin/recognizers/"): ("write", _recognizer_put_item),
    ("DELETE", "/dlp-admin/recognizers/"): ("write", _recognizer_delete_item),
    ("DELETE", "/dlp-admin/edm/corpus/"): ("write", _edm_corpus_delete_item),
    ("POST", "/dlp-admin/key-requests/approve/"): ("write", _kr_approve_item),  # issue #79
    ("POST", "/dlp-admin/key-requests/reject/"): ("write", _kr_reject_item),  # issue #79
}


def _resolve(method: str, path: str):
    """路由解析：返回 (级别 | None, 端点, 路径参数 | None)；未命中返回 None。
    固定路由端点签名 endpoint(handler, me)；参数化端点 endpoint(handler, me, param)。"""
    route = _ROUTES.get((method, path))
    if route is not None:
        return route[0], route[1], None
    for (m, prefix), (level, endpoint) in _ITEM_ROUTES.items():
        if method == m and path.startswith(prefix):
            name = path[len(prefix):]
            if name and "/" not in name:
                return level, endpoint, name
    return None


def handle(handler, method: str) -> bool:
    """admin 平面分发：/dlp-admin/* 命中即处理并返回 True；非 admin 路径返回 False 交还检测路径。
    无鉴权端点（healthz）直接放行；其余一律先鉴权（未知路径按读级门槛），通过后才做路由查找——
    无凭据探测不得区分路由是否存在（code-review 修复）。"""
    path = handler.path.split("?", 1)[0]
    if path != "/dlp-admin" and not path.startswith("/dlp-admin/"):
        return False
    resolved = _resolve(method, path)
    if resolved is not None and resolved[0] is None:
        resolved[1](handler, None)  # 无鉴权端点
        return True
    level = resolved[0] if resolved is not None else "read"  # 未知路径按读级门槛
    me = _authorize(handler, level)
    if me is None:
        return True  # 错误响应已在守卫内回出
    if resolved is None:
        _respond(handler, 404, {"error": "unknown admin endpoint"})
        return True
    _, endpoint, param = resolved
    if param is None:
        endpoint(handler, me)
    else:
        endpoint(handler, me, param)
    return True


def _write_atomic(path: str, write_fn) -> None:
    """原子写核心（issue #33 从 write_json_atomic 重构）：同目录唯一 tmp + os.replace。
    写前自动备份（issue #31 spec）：现有旧文件复制为 <path>.bak（单层滚动；目标不存在时跳过）。
    读者（shim 每请求重读热更新）只见完整旧版或完整新版；失败时清理 tmp，旧文件不受损。"""
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            write_fn(f)
        if os.path.exists(path):
            shutil.copyfile(path, path + ".bak")  # 写前备份：replace 失败时 .bak 与旧文件同值
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json_atomic(path: str, obj) -> None:
    """原子写 JSON 配置（沿用 issue #29 EDM 纪律）。
    风格对齐词表文件：ensure_ascii=False、indent=2、尾部换行。"""
    def _dump(f):
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    _write_atomic(path, _dump)


def write_text_atomic(path: str, text: str) -> None:
    """原子写文本配置（issue #33：config.yaml 渲染落盘），与 write_json_atomic 共用 .bak 纪律。"""
    _write_atomic(path, lambda f: f.write(text))
