#!/usr/bin/env python3
"""DLP 统一配置 admin 平面（issue #31 骨架）：/dlp-admin/* 路由 + axonhub 内省鉴权 + 原子写工具。

与检测路径（/request /response 调用链）完全隔离：admin 平面 fail-closed——
内省不可达回 503，不适用检测链的 fail-open 分级（契约 docs/contracts/dlp-webhook-shim.md）。
依赖仅标准库（同 app.py 纪律，镜像 python:3.12-alpine）。
"""
import json
import os
import shutil
import threading
import urllib.error
import urllib.request

# axonhub 内省端点（容器内默认同栈服务名；测试经环境变量指向本地假服务）
AXONHUB_ADMIN_URL = os.environ.get("AXONHUB_ADMIN_URL", "http://axonhub:8090/admin/graphql")
INTROSPECT_TIMEOUT = 3  # 秒；超时按内省失败处理（fail-closed）
_ME_QUERY = "query Me { me { id isOwner scopes } }"

# 端点级别 → 所需系统 scope（2026-08-06 定案：读 read_channels / 写 write_channels；isOwner 直通）。
# 本票只有读端点，写级为后续票预留。
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
}


def handle(handler, method: str) -> bool:
    """admin 平面分发：/dlp-admin/* 命中即处理并返回 True；非 admin 路径返回 False 交还检测路径。
    无鉴权端点（healthz）直接放行；其余一律先鉴权（未知路径按读级门槛），通过后才做路由查找——
    无凭据探测不得区分路由是否存在（code-review 修复）。"""
    path = handler.path.split("?", 1)[0]
    if path != "/dlp-admin" and not path.startswith("/dlp-admin/"):
        return False
    route = _ROUTES.get((method, path))
    if route is not None and route[0] is None:
        route[1](handler, None)  # 无鉴权端点
        return True
    level = route[0] if route is not None else "read"  # 未知路径按读级门槛
    me = _authorize(handler, level)
    if me is None:
        return True  # 错误响应已在守卫内回出
    if route is None:
        _respond(handler, 404, {"error": "unknown admin endpoint"})
        return True
    route[1](handler, me)
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
