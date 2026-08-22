#!/usr/bin/env python3
"""员工自助端点（issue #74）：GET /self/keys —— 本人名下 Key 列表（含明文，见下）。

背景：issue #68 收掉员工 key 自管权限后，员工在控制台看不到自己名下的 key——上游
UserPersonalAPIKeyReadRule 对 type≠personal 的 key 不做属主隔离，read_api_keys 下发即
见全项目 key（looplj/axonhub#2281 挂账）；personal 类型「只能创建者修改」会断管理员
提档/停启，不可绕行。故走 shim 自助端点：caller 身份经 me 内省确认后，用 admin token
服务端按 userID=me.id 过滤查询，只回白名单字段——绕过上游缺陷而不放开任何权限。

鉴权：admin_api._introspect 同款（caller Bearer 透传 axonhub me 内省，无缓存）。
任何有效登录用户即可（不要 scope——员工 users.scopes=[] 是 #68 后常态）；
内省失败 401/axonhub 不可达 503，不降级。响应经字段白名单塑形，上游即使多返回也剥掉。
明文可见性（issue #81 拍板）：key 明文对本人可见——唯一闸门=服务端 userID=me.id 等值过滤，
他人/未登录一律拿不到；审批私信之外的查看兜底（私信丢失地/换机场景）。
本模块不进检测路径；单请求失败只影响本请求。
"""
import admin_api  # _introspect/_respond 复用（issue #74 实现约定）
import alert_poller  # Axonhub gql 客户端复用（login + 401 重登重试）；import 不起线程
import key_requests  # 控制台申请通道（issue #79）：store/校验/通知；顶层 import 无环（其依赖 admin_api←alert_poller）

# 本人 key 查询：服务端 userID 等值过滤（APIKeyWhereInput.userID 2026-08-21 内省实证支持）。
# 约束：first:100 硬上限——个人名下 key 数量级为个位数，超限需改 after 分页。
# key 明文：issue #81 起对本人下发（唯一闸门=上面的 userID=me.id 过滤）。
_SELF_KEYS_QUERY = (
    "query($uid: ID!) { apiKeys(first: 100, where: {userID: $uid}) { edges { node { "
    "id name key status createdAt profiles { activeProfile profiles { name quota { requests totalTokens cost } } }"
    " } } } }"
)
# 响应白名单字段（expiresAt 在本版上游 schema 不存在——APIKey 字段内省无此项，不落响应；
# id 为 GID，供前端列表 React key 用——评审 P2；key=明文，issue #81 本人可见）
_KEY_FIELDS = ("id", "name", "key", "status", "createdAt", "profiles")

_ax = None  # 模块级 Axonhub 单例（token 缓存；login 惰性）


def _get_ax():
    global _ax
    if _ax is None:
        _ax = alert_poller.Axonhub()
    return _ax


def _shape_key(node: dict) -> dict:
    """白名单塑形：只保留白名单字段（含 key 明文，issue #81 本人可见），其余一律剥掉。"""
    return {k: node.get(k) for k in _KEY_FIELDS}


def query_own_keys(user_gid: str) -> list:
    """admin token 查本人名下 key（含 enabled/disabled/archived 全状态，状态由页面展示）。"""
    data = _get_ax().gql(_SELF_KEYS_QUERY, {"uid": user_gid})
    return [_shape_key(e["node"]) for e in data["apiKeys"]["edges"]]


def _self_keys(handler, me: dict):
    try:
        keys = query_own_keys(me["id"])
    except Exception as e:
        print(f"[self] 本人 key 查询失败: {type(e).__name__}: {e}", flush=True)
        admin_api._respond(handler, 503, {"error": "key query unavailable"})
        return
    admin_api._respond(handler, 200, {"keys": keys})


def _self_key_requests_get(handler, me: dict):
    """本人申请列表（issue #79）：email 服务端过滤，只见本人申请。
    fail-closed（评审 P1）：caller 无 email 时 list_requests("") 不过滤=返回全部申请，
    属主隔离即失效——与 create_request 同语义，直接 502。"""
    email = me.get("email") or ""
    if not email:
        admin_api._respond(handler, 502, {"error": "caller 身份无 email，无法过滤本人申请"})
        return
    try:
        reqs = key_requests.list_requests(email=email)
    except Exception as e:
        print(f"[self] 本人申请查询失败: {type(e).__name__}: {e}", flush=True)
        admin_api._respond(handler, 503, {"error": "request query unavailable"})
        return
    admin_api._respond(handler, 200, {"requests": reqs})


def _self_key_requests_post(handler, me: dict):
    """发起申请（issue #79）：落待办 + 推管理员审批卡。校验失败 400；冲突 409；存储失败 503。"""
    payload = admin_api._read_body(handler)
    if payload is None:
        return  # 413/400 已在 _read_body 内回出
    kind, purpose, tier, err = key_requests.validate_payload(payload)
    if err:
        admin_api._respond(handler, 400, {"error": err})
        return
    try:
        req, kerr = key_requests.create_request(me, kind, purpose, tier)
    except Exception as e:
        print(f"[self] 申请创建失败: {type(e).__name__}: {e}", flush=True)
        admin_api._respond(handler, 503, {"error": "request store unavailable"})
        return
    if kerr:
        admin_api._respond(handler, kerr[0], {"error": kerr[1]})
        return
    admin_api._respond(handler, 201, {"request": req})


def _self_key_request_cancel(handler, me: dict, rid: str):
    """撤回本人申请（issue #80）：仅 pending 可撤，幂等。
    fail-closed email 判定对齐 GET（评审 P1）：无 email 无法判定归属，直接 502。"""
    email = me.get("email") or ""
    if not email:
        admin_api._respond(handler, 502, {"error": "caller 身份无 email，无法判定申请归属"})
        return
    try:
        req, kerr = key_requests.cancel_request(rid, email)
    except Exception as e:
        print(f"[self] 申请撤回失败 {rid}: {type(e).__name__}: {e}", flush=True)
        admin_api._respond(handler, 503, {"error": "request store unavailable"})
        return
    if kerr:
        admin_api._respond(handler, kerr[0], {"error": kerr[1]})
        return
    admin_api._respond(handler, 200, {"request": req})


def handle(handler, method: str) -> bool:
    """/self/* 分发（与 admin_api.handle 同约定）：命中即处理返回 True，否则 False 交还。
    鉴权=有效登录用户（内省通过即可，无 scope 门槛）。
    先鉴权再分流（对齐 admin 平面 code-review 教训，评审 P2）：未知路径/非 GET 也要先
    内省鉴权再 404——未鉴权探测不得区分路由是否存在。"""
    path = handler.path.split("?", 1)[0]
    if path != "/self" and not path.startswith("/self/"):
        return False
    auth = handler.headers.get("Authorization") or ""
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        admin_api._respond(handler, 401, {"error": "missing bearer token"})
        return True
    me, err = admin_api._introspect(token)
    if err is not None:
        admin_api._respond(handler, err, {"error": "introspection unavailable" if err == 503 else "unauthorized"})
        return True
    if method == "GET" and path == "/self/keys":
        _self_keys(handler, me)
    elif method == "GET" and path == "/self/key-requests":
        _self_key_requests_get(handler, me)
    elif method == "POST" and path == "/self/key-requests":
        _self_key_requests_post(handler, me)
    elif method == "POST" and path.startswith("/self/key-requests/") and path.endswith("/cancel"):
        # 撤回（issue #80）：/self/key-requests/<id>/cancel；畸形 id 由 cancel_request 自然 404
        rid = path[len("/self/key-requests/"):-len("/cancel")].strip("/")
        _self_key_request_cancel(handler, me, rid)
    else:
        admin_api._respond(handler, 404, {"error": "unknown self endpoint"})
    return True
