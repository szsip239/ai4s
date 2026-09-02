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
用量展示（issue #83）：员工直查 apiKeyQuotaUsages 被上游 FORBIDDEN，本模块在 key 集已锁死
本人的前提下用 admin token 逐 key 代查内嵌（usage 字段）；与管理员侧 profiles 对话框同一
上游聚合，展示进度与 403 拦截同源。单 key 用量失败置 None，不拖垮列表。
多项目隔离（issue #89）：/self/keys 与 /self/key-requests（GET/POST）要求 X-Project-ID 头
（admin_api.read_project_header 校验，缺失/非法 400）；key 列表按 userID+projectID 过滤，
申请列表按项目过滤（存量无项目字段视为 Default）；POST 非项目成员 403（key_requests 校验）。
本模块不进检测路径；单请求失败只影响本请求。
"""
import urllib.parse

import admin_api  # _introspect/_respond 复用（issue #74 实现约定）
import alert_poller  # Axonhub gql 客户端复用（login + 401 重登重试）；import 不起线程
import key_requests  # 控制台申请通道（issue #79）：store/校验/通知；顶层 import 无环（其依赖 admin_api←alert_poller）

# 本人 key 查询：服务端 userID 等值过滤（APIKeyWhereInput.userID 2026-08-21 内省实证支持）。
# issue #89：叠加 projectID 等值过滤（同日内省实证）——「我的 Key」按控制台当前项目隔离；
# 项目上下文来自 X-Project-ID 头（admin_api.read_project_header 校验），缺失/非法 400。
# 约束：first:100 硬上限——个人名下 key 数量级为个位数，超限需改 after 分页。
# key 明文：issue #81 起对本人下发（唯一闸门=上面的 userID=me.id 过滤）。
_SELF_KEYS_QUERY = (
    "query($uid: ID!, $projectID: ID!) { apiKeys(first: 100, where: {userID: $uid, projectID: $projectID}) { edges { node { "
    "id name key status createdAt profiles { activeProfile profiles { name quota { requests totalTokens cost } } }"
    " } } } }"
)
# issue #83：员工用量展示——员工直查 apiKeyQuotaUsages 被上游 FORBIDDEN（只认系统级 scope，
# 与 #68 同源），由 shim admin token 代查。安全闸门不变：key 集已由上一条查询按 userID=me.id
# 锁死本人，这里只对本人 key id 逐个取用量（数量级个位数，N+1 可接受；与巡检循环同模式）。
# 与管理员侧 profiles 对话框同一上游查询/同一聚合，展示与 403 拦截天然同源。
_QUOTA_USAGES_QUERY = (
    "query($apiKeyId: ID!) { apiKeyQuotaUsages(apiKeyId: $apiKeyId) { profileName "
    "quota { requests totalTokens cost period { type pastDuration { value unit } calendarDuration { unit } } } "
    "window { start end } usage { requestCount totalTokens totalCost } } }"
)
# 响应白名单字段（expiresAt 在本版上游 schema 不存在——APIKey 字段内省无此项，不落响应；
# id 为 GID，供前端列表 React key 用——评审 P2；key=明文，issue #81 本人可见；
# usage=各档用量，issue #83 代查内嵌，None=用量暂不可用不影响列表）
_KEY_FIELDS = ("id", "name", "key", "status", "createdAt", "profiles", "usage")
# 用量条目白名单（与管理员侧 APIKeyProfileQuotaUsage 同形，原样透传这四个键）
_USAGE_FIELDS = ("profileName", "quota", "window", "usage")
# 窗口 token 用量（不设限档也显示用量）：代查 apiKeyTokenUsageStats（与管理员侧
# token chart 同一上游查询），input 透传 apiKeyIds/createdAtGTE；白名单透传五键。
# 员工直查同 quotaUsages 一样 FORBIDDEN，属主闸门=key gid ∈ 本人本项目 key 集。
_USAGE_STATS_QUERY = (
    "query($input: APIKeyTokenUsageStatsInput) { apiKeyTokenUsageStats(input: $input) { "
    "apiKeyId inputTokens outputTokens cachedTokens reasoningTokens "
    "topModels { modelId inputTokens outputTokens cachedTokens reasoningTokens } } }"
)
_USAGE_STATS_FIELDS = ("inputTokens", "outputTokens", "cachedTokens", "reasoningTokens", "topModels")
_USAGE_STATS_WINDOWS = ("day", "month", "all")

_ax = None  # 模块级 Axonhub 单例（token 缓存；login 惰性）


def _get_ax():
    global _ax
    if _ax is None:
        _ax = alert_poller.Axonhub()
    return _ax


def _shape_key(node: dict) -> dict:
    """白名单塑形：只保留白名单字段（含 key 明文，issue #81 本人可见），其余一律剥掉。"""
    return {k: node.get(k) for k in _KEY_FIELDS}


def query_key_usages(key_gid: str) -> list:
    """admin token 代查单 key 各档用量（issue #83）。条目经白名单塑形后原样透传。"""
    data = _get_ax().gql(_QUOTA_USAGES_QUERY, {"apiKeyId": key_gid})
    return [{k: u.get(k) for k in _USAGE_FIELDS} for u in (data.get("apiKeyQuotaUsages") or [])]


def query_own_key_ids(user_gid: str, project_id: str = alert_poller.KEY_PROJECT_ID) -> list:
    """本人本项目 key gid 集（轻量属主校验用——不内嵌 usage 代查，免 N+1）。"""
    data = _get_ax().gql(_SELF_KEYS_QUERY, {"uid": user_gid, "projectID": project_id})
    return [e["node"]["id"] for e in data["apiKeys"]["edges"]]


def _window_gte(window: str, tz_offset_min: int = 0):
    """窗口起点 ISO8601（带偏移）：day=今日 00:00 / month=本月 1 号 00:00 / all → None。
    tz_offset_min 取浏览器 getTimezoneOffset 语义（本地=UTC-offset，如 UTC+8 传 -480）。"""
    import datetime
    if window == "all":
        return None
    tz = datetime.timezone(datetime.timedelta(minutes=-tz_offset_min))
    now = datetime.datetime.now(tz)
    if window == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:  # month
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat()


def query_usage_stats(key_gid: str, created_at_gte: str | None = None) -> dict:
    """admin token 代查单 key 时间窗 token 用量（apiKeyTokenUsageStats）。
    白名单五键透传；上游无该 key 条（空窗）兜底全零。"""
    input_ = {"apiKeyIds": [key_gid]}
    if created_at_gte:
        input_["createdAtGTE"] = created_at_gte
    data = _get_ax().gql(_USAGE_STATS_QUERY, {"input": input_})
    for s in data.get("apiKeyTokenUsageStats") or []:
        if s.get("apiKeyId") == key_gid:
            return {k: s.get(k) for k in _USAGE_STATS_FIELDS}
    return {"inputTokens": 0, "outputTokens": 0, "cachedTokens": 0, "reasoningTokens": 0,
            "topModels": []}


def query_own_keys(user_gid: str, project_id: str = alert_poller.KEY_PROJECT_ID) -> list:
    """admin token 查本人名下 key（含 enabled/disabled/archived 全状态，状态由页面展示）。
    issue #89：project_id 限定项目（多项目隔离；默认 Default 常量兜底直调）。
    issue #83：每把 key 内嵌各档用量；单 key 用量失败只置 None 不拖垮列表
    （key 列表是主功能，用量是增强展示；身份闸门不受此影响——用量挂在已锁死本人的 key 上）。"""
    data = _get_ax().gql(_SELF_KEYS_QUERY, {"uid": user_gid, "projectID": project_id})
    keys = []
    for e in data["apiKeys"]["edges"]:
        node = e["node"]
        shaped = _shape_key(node)
        try:
            shaped["usage"] = query_key_usages(node["id"])
        except Exception as ex:
            print(f"[self] key 用量代查失败 {node['id']}: {type(ex).__name__}: {ex}", flush=True)
            shaped["usage"] = None
        keys.append(shaped)
    return keys


def _project_or_400(handler) -> str:
    """issue #89：读 X-Project-ID 项目上下文；缺失/非法即回 400 并返回 None。"""
    pid = admin_api.read_project_header(handler)
    if not pid:
        admin_api._respond(handler, 400, {"error": "缺少项目上下文（X-Project-ID 头），请先在控制台选择项目"})
    return pid


def _self_keys(handler, me: dict):
    pid = _project_or_400(handler)
    if not pid:
        return
    try:
        keys = query_own_keys(me["id"], pid)
    except Exception as e:
        print(f"[self] 本人 key 查询失败: {type(e).__name__}: {e}", flush=True)
        admin_api._respond(handler, 503, {"error": "key query unavailable"})
        return
    admin_api._respond(handler, 200, {"keys": keys})


def _self_key_requests_get(handler, me: dict):
    """本人申请列表（issue #79）：email 服务端过滤，只见本人申请。
    fail-closed（评审 P1）：caller 无 email 时 list_requests("") 不过滤=返回全部申请，
    属主隔离即失效——与 create_request 同语义，直接 502。
    issue #89：再按 X-Project-ID 项目过滤（存量无项目字段申请视为 Default）。"""
    email = me.get("email") or ""
    if not email:
        admin_api._respond(handler, 502, {"error": "caller 身份无 email，无法过滤本人申请"})
        return
    pid = _project_or_400(handler)
    if not pid:
        return
    try:
        reqs = key_requests.list_requests(email=email, project_id=pid)
    except Exception as e:
        print(f"[self] 本人申请查询失败: {type(e).__name__}: {e}", flush=True)
        admin_api._respond(handler, 503, {"error": "request query unavailable"})
        return
    admin_api._respond(handler, 200, {"requests": reqs})


def _self_key_requests_post(handler, me: dict):
    """发起申请（issue #79）：落待办 + 推管理员审批卡。校验失败 400；冲突 409；存储失败 503。
    issue #89：项目上下文来自 X-Project-ID（缺失 400）；非该项目成员 403（key_requests 校验）。"""
    payload = admin_api._read_body(handler)
    if payload is None:
        return  # 413/400 已在 _read_body 内回出
    kind, purpose, tier, key_ids, err = key_requests.validate_payload(payload)
    if err:
        admin_api._respond(handler, 400, {"error": err})
        return
    pid = _project_or_400(handler)
    if not pid:
        return
    try:
        req, kerr = key_requests.create_request(me, kind, purpose, tier, key_ids=key_ids, project_id=pid)
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


def _self_key_usage_stats(handler, me: dict):
    """GET /self/key-usage-stats?key=<gid>&window=day|month|all&tz=<offset_min>：
    本人 key 时间窗 token 用量（不设限档也有用量可显示）。属主闸门：gid ∈ 本人本项目 key 集，
    否则 404（不区分存在性）；window/tz 非法 400；代查失败 503。"""
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    key_gid = (qs.get("key") or [""])[0]
    window = (qs.get("window") or ["all"])[0]
    if not key_gid:
        admin_api._respond(handler, 400, {"error": "缺少 key 参数"})
        return
    if window not in _USAGE_STATS_WINDOWS:
        admin_api._respond(handler, 400, {"error": f"window 必须是 {'/'.join(_USAGE_STATS_WINDOWS)}"})
        return
    try:
        tz = int((qs.get("tz") or ["0"])[0])
        if abs(tz) > 840:  # 合法时区偏移上限 ±14h
            raise ValueError
    except ValueError:
        admin_api._respond(handler, 400, {"error": "tz 必须是 ±840 内的整数（分钟）"})
        return
    pid = _project_or_400(handler)
    if not pid:
        return
    try:
        ids = query_own_key_ids(me["id"], pid)
    except Exception as e:
        print(f"[self] 本人 key id 查询失败: {type(e).__name__}: {e}", flush=True)
        admin_api._respond(handler, 503, {"error": "key query unavailable"})
        return
    if key_gid not in ids:
        admin_api._respond(handler, 404, {"error": "key 不存在"})
        return
    try:
        stats = query_usage_stats(key_gid, created_at_gte=_window_gte(window, tz))
    except Exception as e:
        print(f"[self] 用量统计代查失败 {key_gid}: {type(e).__name__}: {e}", flush=True)
        admin_api._respond(handler, 503, {"error": "usage stats unavailable"})
        return
    admin_api._respond(handler, 200, {"stats": stats, "window": window,
                                      "since": _window_gte(window, tz)})


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
    elif method == "GET" and path == "/self/key-usage-stats":
        _self_key_usage_stats(handler, me)
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
