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


def write_json_atomic(path: str, obj) -> None:
    """原子写 JSON 配置（沿用 issue #29 EDM 纪律）：同目录唯一 tmp + os.replace。
    写前自动备份（issue #31 spec）：现有旧文件复制为 <path>.bak（单层滚动；目标不存在时跳过）。
    读者（shim 每请求重读热更新）只见完整旧版或完整新版；失败时清理 tmp，旧文件不受损。
    风格对齐词表文件：ensure_ascii=False、indent=2、尾部换行。"""
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        if os.path.exists(path):
            shutil.copyfile(path, path + ".bak")  # 写前备份：replace 失败时 .bak 与旧文件同值
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
