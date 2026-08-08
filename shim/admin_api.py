#!/usr/bin/env python3
"""DLP 统一配置 admin 平面：/dlp-admin/* 路由 + axonhub 内省鉴权 + 原子写工具。

issue #31 骨架：healthz/ping、内省鉴权（读 read_channels / 写 write_channels，isOwner 直通）、write_json_atomic（.bak 滚动）。
issue #32 配置面 CRUD：wordlist GET/PUT（整体替换 terms）、recognizers GET/POST/PUT/DELETE（regex 过 re.compile 校验）。

与检测路径（/request /response 调用链）完全隔离：admin 平面 fail-closed——
内省不可达回 503，不适用检测链的 fail-open 分级（契约 docs/contracts/dlp-webhook-shim.md）。
依赖仅标准库（同 app.py 纪律，镜像 python:3.12-alpine）。
"""
import json
import os
import re
import shutil
import threading
import urllib.error
import urllib.request

# axonhub 内省端点（容器内默认同栈服务名；测试经环境变量指向本地假服务）
AXONHUB_ADMIN_URL = os.environ.get("AXONHUB_ADMIN_URL", "http://axonhub:8090/admin/graphql")
INTROSPECT_TIMEOUT = 3  # 秒；超时按内省失败处理（fail-closed）
_ME_QUERY = "query Me { me { id isOwner scopes } }"

# 配置文件路径（与 app.py 相同 env/默认值；测试用临时文件覆写模块属性）
WORDLIST_PATH = os.environ.get("WORDLIST_PATH", "/dlp/confidential-terms.json")
PII_RECOGNIZERS_PATH = os.environ.get("PII_RECOGNIZERS_PATH", "/recognizers/pii-zh.json")
FORMAT_RULES_PATH = os.environ.get("FORMAT_RULES_PATH", "/dlp/format-rules.json")
AGENTGW_CONFIG_PATH = os.environ.get("AGENTGW_CONFIG_PATH", "/agentgateway/config.yaml")
_MAX_ADMIN_BODY = 1024 * 1024  # admin 请求体上限（配置文本足够）

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


def _read_body(handler):
    """读 admin 请求体 JSON；非法 → 已回 400，返回 None。"""
    try:
        length = min(int(handler.headers.get("Content-Length") or 0), _MAX_ADMIN_BODY)
        return json.loads(handler.rfile.read(length) or b"{}")
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
    rerr = _render_to_config(payload["rules"])
    if rerr:
        _rollback_format_rules_json()
        _respond(handler, 500, {"error": f"{rerr}（JSON 已回滚）"})
        return
    _respond(handler, 200, payload)


def _yaml_single_quote(s: str) -> str:
    """YAML 单引号标量（内嵌单引号翻倍）。"""
    return "'" + s.replace("'", "''") + "'"


def render_gateway_block(rules: list) -> str:
    """渲染 promptGuard request 段的 - regex 条目文本（issue #33）。
    缩进对齐现网（条目 12 空格级，模板化确定性构造兜底 stdlib 无 YAML 解析器）；
    enabled=false 或 gateway_patterns 为空（shim-only）的规则不渲染进网关。"""
    lines = []
    for r in rules:
        patterns = r.get("gateway_patterns") or []
        if not r.get("enabled") or not patterns:
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
    return "\n".join(lines) + "\n"


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


def _verify_spliced(text: str, rules: list) -> None:
    """渲染后校验（issue #33）：标记完整 + 每条启用规则的 gateway_patterns 与 reject code 均在文本中。"""
    if _BEGIN_MARK not in text or _END_MARK not in text:
        raise ValueError("渲染后校验失败: 标记缺失")
    for r in rules:
        if not r.get("enabled"):
            continue
        for p in r.get("gateway_patterns") or []:
            # pattern 原文含 ' 时渲染进 YAML 单引号标量会翻倍（review #3）：比对转义后形态，否则恒假误 500
            if p.replace("'", "''") not in text:
                raise ValueError(f"渲染后校验失败: pattern 未落文本 ({r.get('code')})")
        if r.get("action") == "reject" and f'"code":"{r["code"]}"' not in text:
            raise ValueError(f"渲染后校验失败: rejection code 未落文本 ({r.get('code')})")


def _render_to_config(rules) -> str | None:
    """读 config.yaml → 渲染 splice 标记区块 → 渲染后校验 → 原子写盘（PUT/render 共用，review #6）。
    成功返回 None；渲染/校验失败或写盘失败返回错误消息（渲染失败时未落盘）。"""
    try:
        with open(AGENTGW_CONFIG_PATH, encoding="utf-8") as f:
            config_text = f.read()
        new_config = splice_rendered(config_text, render_gateway_block(rules))
        _verify_spliced(new_config, rules)
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
    用于区块被手改漂移后的修复；JSON 损坏/缺标记拒绝渲染，不落盘。"""
    data = _load_json_file(FORMAT_RULES_PATH)
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        _respond(handler, 500, {"error": "format-rules unreadable，拒绝渲染"})
        return
    rules = data["rules"]
    rerr = _render_to_config(rules)
    if rerr:
        _respond(handler, 500, {"error": rerr})
        return
    rendered = sum(1 for r in rules if r.get("enabled") and r.get("gateway_patterns"))
    _respond(handler, 200, {"rendered": rendered})


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
}


# 参数化路由：/dlp-admin/recognizers/<name>（URL 末段为 name 参数，PUT/DELETE 用）
_ITEM_ROUTES = {
    ("PUT", "/dlp-admin/recognizers/"): ("write", _recognizer_put_item),
    ("DELETE", "/dlp-admin/recognizers/"): ("write", _recognizer_delete_item),
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
