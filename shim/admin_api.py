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
issue #105：judge 注入第二职责 schema——settings judge 段加 inject_enabled（布尔开关，
默认关=先进场 shadow 观察）/inject_prompt_system/inject_prompt_fewshot（注入判定专用
prompt，单一源=settings.json 同 #35 review #2 纪律；关态允许空串占位，开态必须非空——
不给「开+空」运行必 error 的配置放行）三键必填校验；shadow-verdicts 出口 layer 过滤
接受 judge_inject、stats 加 judge_inject 层（判定条带 attack_type 类型标签脱敏字段，
与商密 judge 层分层统计）。
issue #117：auto 智能路由 schema——settings 新增 routing **可选节**（enabled/threshold/
tiers/timeout/max_concurrency 五键；缺席=合法，运行侧 routing.enabled 缺省 false 现网
零变化，兼容未含 routing 节的旧文件与控制台 GET→PUT 往返；出现即整节五键齐全校验，
tiers 两档映射 simple/complex 必填且模型名过响应头白名单字符形态）；shadow-verdicts
出口 layer 过滤接受 router、stats 加 router 层（决策条带 resolved_model/tier/p_complex/
reason/session 字段）。
issue #119：routing 节内再增五个**可选键**（缺席=运行侧内置默认保现网行为；旧五键
必填语义不变）——prompt=分类系统提示（非空字符串）/escalate_conf=升档强置信门槛
（0~1 含边界）/session_ttl=会话存态 TTL 秒（>0）/tool_loop_lock/thinking_lock=两道
锁开关（布尔）；未知键仍 400，非法值 400 不落盘。
issue #129：Key 绕行名单 CRUD——GET/POST /dlp-admin/bypass-keys + PUT/DELETE
/dlp-admin/bypass-keys/<id>（只存 SHA-256(token) 不落明文，id 即哈希；校验错误
不回显 token）；shadow-verdicts 出口 layer 过滤接受 bypass、stats 加 bypass 层
（绕行审计条只带模型名与范围，不落原文不记 token）。
issue #130：shadow-verdicts 出口 layer 过滤接受 block、stats 加 block 层（词表/
归一化 secrets/EDM 内容阻断条，records 带 rule_ids 命中规则族标识——脱敏字段）。
issue #138：graphql-authz 扫描补 GraphQL 字符串反转义归一化（graphql_unescape——
query 文本内 \\uXXXX 等转义在 gqlgen 执行时才还原，不归一化则受限 gid 以转义形态
隐身绕过正则）；白名单 key 服务端匹配 POST /dlp-admin/bypass-keys/match（读语义
read_api_keys 档，携调用方 Bearer 按 idIn 批量取明文算 SHA-256 比对名单，只回匹配条，
不明文落盘落日志）——控制台不再拉全量 key 明文到浏览器。
issue #139：写端点读-改-写串行化——模块级 _RMW_LOCK 保护 wordlist PUT / settings PUT /
recognizers POST·PUT·DELETE / EDM ingest·delete（并发管理写互持 stale 快照覆盖会丢更新）；
Content-Length 非法/负值干净 400（_body_length fail-closed，不再进 read() 异常分支）。
issue #140：L1 格式规则判定收回 shim 单点——format-rules PUT 只写 JSON 不再渲染
config.yaml（render_gateway_block/splice_rendered/_render_to_config 及 DLP-FORMAT-RULES
标记区块随网关侧规则一并撤除），POST /dlp-admin/format-rules/render 端点撤除；
settings PUT 的 l1 联动渲染/回滚同步撤除（l1.enabled 只门控 shim 检测侧，app.py
每请求热读）。format-rules schema 的 gateway_scope 字段保留为 no-op（历史文件兼容）；
gateway_patterns 由 shim 原文直扫通道消费（issue #140 补漏：归一化粘连漏检面兜底，
见 app.py norm_secret_hits raw 通道）。

与检测路径（/request /response 调用链）完全隔离：admin 平面 fail-closed——
内省不可达回 503，不适用检测链的 fail-open 分级（契约 docs/contracts/dlp-webhook-shim.md）。
本模块自身仅标准库；doc_extract 的文档解析依赖第三方库（PyMuPDF/python-docx/openpyxl/
python-pptx/pytesseract/Pillow，全 manylinux wheel），仅 upload 端点函数级引用，
检测路径 app.py 不受影响（仍纯 stdlib）。
"""
import hashlib
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
import bypass_keys  # Key 级 DLP 绕行名单（issue #129）：stdlib-only 无环；/dlp-admin/bypass-keys 消费

# axonhub 内省端点（容器内默认同栈服务名；测试经环境变量指向本地假服务）
AXONHUB_ADMIN_URL = os.environ.get("AXONHUB_ADMIN_URL", "http://axonhub:8090/admin/graphql")
INTROSPECT_TIMEOUT = 3  # 秒；超时按内省失败处理（fail-closed）
_ME_QUERY = "query Me { me { id email isOwner scopes } }"  # issue #79 加 email：控制台 Key 申请通道登记申请人身份用
# issue #128：playground 闸门内省——补项目成员 scopes（beta6 me.projects{projectID scopes} 实证可用）
_ME_PROJECTS_QUERY = "query Me { me { id email isOwner scopes projects { projectID scopes } } }"


def playground_allowed(me: dict, project_gid: str) -> bool:
    """playground 闸门纯函数（issue #128）：owner / 系统档 write_requests / 指定项目成员
    scopes 含 write_requests 放行，其余拒。刻意不做 "*" 通配——与 _authorize 同款精确匹配
    纪律（非 owner 管理账号须持显式 scope）。"""
    if me.get("isOwner"):
        return True
    if "write_requests" in set(me.get("scopes") or []):
        return True
    for p in me.get("projects") or []:
        if p.get("projectID") == project_gid and "write_requests" in set(p.get("scopes") or []):
            return True
    return False

# /graphql-authz 受限模式（2026-09-03，「上游未修网关先行」继 #68 P2-D/#128）：
# beta6 上游 RequestExecution/ChannelProbe/ProviderQuotaStatus/UserRole 四实体未挂 ent
# policy（生成代码零 privacy 调用）——GraphQL node(id:) 任意 JWT 跨项目直读，活栈实证
# 零权限员工不带项目头读到非成员项目执行记录的完整 requestBody（prompt 原文）；
# channelProbeData（探针数据）/checkProviderQuotas（触发全渠道配额外呼）两操作同样无
# scope 校验。控制台自身 node(id:) 仅作用于有 policy 的类型（Request/UsageLog/Channel/
# APIKey/Project），不在此表、不受影响。命中即要求系统级对应 scope——node 直读不带
# 项目上下文，项目级授权无法映射到目标实体所属项目，刻意不认项目级（同 _LEVEL_SCOPES
# 系统档纪律）。
_GRAPHQL_GID_TYPE_SCOPES = {
    "RequestExecution": "read_requests",
    "ChannelProbe": "read_channels",
    "ProviderQuotaStatus": "read_channels",
    "UserRole": "read_roles",
}
_GRAPHQL_OP_SCOPES = {
    "channelProbeData": "read_channels",
    "checkProviderQuotas": "write_channels",
}
# gid 实参必须是字面 gid 字符串（axonhub node resolver 只认 gid:// 形）。纪律（审计B
# 严重1）：正则严禁直接扫请求体原始字节——JSON 字符串的 \u 加四位十六进制转义
# 可让危险 gid/字段名在原文里隐身（斜杠等字符以转义形态出现），经 axonhub JSON
# 解码后还原执行；调用方必须先 json.loads，再用 graphql_strings 递归取全部字符串
# 值后扫描（app.py _graphql_authz 即如此；本函数只对喂入的文本负责）。
# issue #138：JSON 解码后还有第二层——GraphQL 单行字符串字面量自身支持转义
# （\uXXXX/\u{…}/\" \\/ \/ \b \f \n \r \t），query 文本写 node(id:
# "gid://axonhub/R\u0065questExecution/1") 时解码后仍是 6 字符字面 \u0065，
# 正则不命中即放行，gqlgen 执行时按 GraphQL 语义二次反转义还原成受限 gid。
# 故扫描前先经 graphql_unescape 反转义归一化，归一化产物再过正则。
_GRAPHQL_GID_RE = re.compile(r"gid://axonhub/(" + "|".join(_GRAPHQL_GID_TYPE_SCOPES) + r")/")
_GRAPHQL_OP_RE = re.compile(r"\b(" + "|".join(_GRAPHQL_OP_SCOPES) + r")\b")

# GraphQL 单行字符串字面量转义表（issue #138；gqlgen/gqlparser 同款词法语义）
_GQL_SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b",
                       "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_GQL_UESCAPE_RE = re.compile(r"\\u\{([0-9A-Fa-f]{1,6})\}|\\u([0-9A-Fa-f]{4})")


def graphql_unescape(text: str) -> str:
    """GraphQL 字符串反转义归一化（issue #138）：单次左到右扫描，与 gqlgen 解码语义一致
    （"\\\\u0065" 的 "\\\\" 先成字面反斜杠，剩余 u0065 不再二次反转义——不放大攻击面
    也不误报双反斜杠非攻击形态）。\\u{…} 变长形属新规范形态（gqlgen 未必支持——多解一处
    不会漏判：不支持的引擎会拒该 query，语义等价 fail-closed）。非法/不完整转义原样保留
    （gqlgen 会拒该 query，永不执行；保留原文最大化扫描可见性）。不做词法分析（不区分
    转义是否在字符串字面量内）：字面量外的反斜杠在 GraphQL 是语法错误，对永不执行的
    文本多归一化只会多命中（fail-closed），不会漏判。无反斜杠直通（常规流量零成本）。"""
    if "\\" not in text:
        return text
    out = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "\\" or i + 1 >= n:
            out.append(text[i])
            i += 1
            continue
        simple = _GQL_SIMPLE_ESCAPES.get(text[i + 1])
        if simple is not None:
            out.append(simple)
            i += 2
            continue
        if text[i + 1] == "u":
            m = _GQL_UESCAPE_RE.match(text, i)
            if m:
                try:
                    out.append(chr(int(m.group(1) or m.group(2), 16)))
                except (ValueError, OverflowError):
                    out.append(m.group(0))  # 超码点上限：原样保留
                i = m.end()
                continue
        out.append(text[i])  # 非法转义：保留反斜杠，下一字符照常扫描
        i += 1
    return "".join(out)


def graphql_strings(payload):
    """递归产出 GraphQL JSON 负载中的全部字符串值（query/operationName/variables 任意
    嵌套；gqlgen 批量数组形态同覆盖）。"""
    if isinstance(payload, str):
        yield payload
    elif isinstance(payload, dict):
        for v in payload.values():
            yield from graphql_strings(v)
    elif isinstance(payload, list):
        for v in payload:
            yield from graphql_strings(v)


def graphql_required_scopes(body: str) -> list:
    """扫描 GraphQL 请求体原文，返回命中受限模式所需的系统 scope 列表（去重排序）；
    空列表 = 常规查询（控制台全部日常流量），调用方零内省直接放行。
    issue #138：扫描前先 graphql_unescape 反转义归一化（GraphQL 字符串字面量 \\uXXXX
    等转义在 gqlgen 执行时才还原，不归一化则受限 gid 以转义形态隐身）。"""
    if not body:
        return []
    body = graphql_unescape(body)
    required = {_GRAPHQL_GID_TYPE_SCOPES[t] for t in _GRAPHQL_GID_RE.findall(body)}
    required.update(_GRAPHQL_OP_SCOPES[op] for op in _GRAPHQL_OP_RE.findall(body))
    return sorted(required)


def graphql_authz_allowed(me: dict, required) -> bool:
    """命中受限模式后的闸门判定：owner 直通；否则须持全部所需系统 scope（精确匹配，
    同 _authorize/playground_allowed 纪律——"*" 不通配）。required 为空不应走到本函数。"""
    if me.get("isOwner"):
        return True
    have = set(me.get("scopes") or [])
    return all(s in have for s in required)


# 配置文件路径（与 app.py 相同 env/默认值；测试用临时文件覆写模块属性）
WORDLIST_PATH = os.environ.get("WORDLIST_PATH", "/dlp/confidential-terms.json")
PII_RECOGNIZERS_PATH = os.environ.get("PII_RECOGNIZERS_PATH", "/recognizers/pii-zh.json")
FORMAT_RULES_PATH = os.environ.get("FORMAT_RULES_PATH", "/dlp/format-rules.json")
AGENTGW_CONFIG_PATH = os.environ.get("AGENTGW_CONFIG_PATH", "/agentgateway/config.yaml")  # issue #139 token 渲染引用（app.py）；#140 起本模块不再写 config.yaml
EDM_FP_PATH = os.environ.get("EDM_FP_PATH", "/edm/fingerprints.json")  # 与 app.py 同 env/默认
EDM_CORPUS_DIR = os.environ.get("EDM_CORPUS_DIR", "/edm/corpus")
SETTINGS_PATH = os.environ.get("SETTINGS_PATH", "/dlp/settings.json")  # 与 app.py 同 env/默认（issue #35）
_MAX_ADMIN_BODY = 1024 * 1024  # admin 请求体上限（配置文本足够）
_MAX_EDM_BODY = 16 * 1024 * 1024  # EDM corpus POST 放宽（review #5：真实商密文档规模可达数 MB）

# 配置面读-改-写互斥锁（issue #139，bypass_keys._RMW_LOCK 同款模式）：wordlist/recognizers/
# settings/EDM 语料的「加载→校验→原子写」整体串行化——ThreadingHTTPServer 下并发写裸跑
# 会 stale 读覆盖丢更新（先写者的变更被后写者的旧快照盖掉）。低频管理操作，单锁无性能顾虑；
# 请求体读取/JSON 解析在锁外（慢客户端不持锁）。
_RMW_LOCK = threading.Lock()

# 端点级别 → 所需系统 scope（2026-08-06 定案：读 read_channels / 写 write_channels；isOwner 直通）。
# issue #138：bypass-keys/match 为读语义但读的是 key 面数据，用 read_api_keys 档
# （与白名单面板页面路由同 scope；_authorize 机制本身任意 scope 名通用，加档即支持）。
_LEVEL_SCOPES = {"read": "read_channels", "write": "write_channels", "read_api_keys": "read_api_keys"}


def _respond(handler, code: int, obj) -> None:
    """admin 平面自用的 JSON 应答（不依赖 app.Handler._json，模块边界自包含）。"""
    body = json.dumps(obj, ensure_ascii=False).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _introspect(token: str, query: str = _ME_QUERY):
    """caller Bearer token 透传 axonhub 内省（无缓存，每请求一次——admin 调用低频，KISS）。
    query 可换形（issue #128 /playground-authz 用 _ME_PROJECTS_QUERY 带项目成员 scopes）。
    返回 (me, None) 或 (None, 错误码)：非 200 或 me 为空 → 401；
    网络错误/超时 → 503（admin 平面 fail-closed，不适用检测链 fail-open）。"""
    body = json.dumps({"query": query}).encode()
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


def _body_length(headers):
    """admin 平面声明体长解析（issue #139）：非法/负值 Content-Length → None（调用方回干净
    400，不读流）。对照 app.py 同名函数——检测路径非法值当 0（fail-open：5xx=全层放行），
    admin 平面 fail-closed 不适用该语义，非法即拒；负值 rfile.read 会读流至 EOF 悬挂
    （客户端等响应、服务端等 body），必须拒。"""
    try:
        n = int(headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _read_raw_body(handler, max_body):
    """读 admin 请求体原始字节；声明体长非法/负值 → 已回 400；超限 → 已回 413 并返回 None。
    超限先分块 drain 再响应——否则客户端发送中途断连，看到 BrokenPipe 而非干净 413。"""
    raw_len = _body_length(handler.headers)
    if raw_len is None:
        _respond(handler, 400, {"error": "Content-Length 非法（须为非负整数）"})
        return None
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
    """PUT 整体替换 terms（issue #32）：保留文件原 version/_comment；非法 400 带原因。
    读-改-写持 _RMW_LOCK（issue #139）：并发 PUT 串行，后写者基于先写者落盘结果再改。"""
    payload = _read_body(handler)
    if payload is None:
        return
    err = _validate_terms(payload.get("terms") if isinstance(payload, dict) else None)
    if err:
        _respond(handler, 400, {"error": err})
        return
    with _RMW_LOCK:
        data, err = _load_for_write(WORDLIST_PATH, {"version": 1}, "wordlist")
        if err:
            _respond(handler, 500, {"error": err})
            return
        data["terms"] = payload["terms"]
        write_json_atomic(WORDLIST_PATH, data)
    _audit(_me, "put_wordlist", [f"terms({len(payload['terms'])})"])  # 词值不落盘（机密词本体）
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
_SETTINGS_TOP_KEYS = {"version", "_comment", "judge", "edm", "pg", "rules", "l1", "l2", "response",
                      "routing"}  # routing：issue #117 auto 智能路由（可选节，见下方校验）
_SETTINGS_JUDGE_KEYS = {"enabled", "model", "base_url", "timeout", "prompt_system", "prompt_fewshot",
                        "threshold", "action",  # threshold/action：issue #94 置信度门槛与动作分级
                        "sample_rate", "max_concurrency",  # issue #93 判定采样率与并发预算
                        "inject_enabled", "inject_prompt_system", "inject_prompt_fewshot"}  # issue #105 注入第二职责
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
# issue #127：l2.opf 可选子节（privacy-filter 第二检测器预接入，开关缺省关）——
# 出席才校验（缺席=运行侧缺省关，兼容旧文件/控制台 GET→PUT 往返，对齐 routing 节语义）；
# 出席即三键齐全（防部分更新静默丢键，与必填段同精神）；
# url 为可选增补键（缺席=运行侧 env OPF_URL/内置默认，对齐 judge.base_url 层级语义）
_SETTINGS_L2_OPTIONAL_KEYS = {"opf"}
_SETTINGS_OPF_KEYS = {"enabled", "timeout_ms", "max_chars"}
_SETTINGS_OPF_OPTIONAL_KEYS = {"url"}
# auto 智能路由（issue #117）：enabled=层开关（默认关，新层进场先关验证后再开）/
# threshold=p_complex 判 complex 门槛（默认 0.5，#114 评测推荐默认工作点）/
# tiers=两档→真实模型映射（simple/complex 两键必填；映射目标只选全量开放模型池，
# 避免与 axonhub profile 白名单耦合——池成员资格靠评审约束，此处只校验字符形态）/
# timeout=分类调用超时（默认 4s，超时 fail-open 落旗舰）/
# max_concurrency=分类并发预算（默认 2，独立于 judge.max_concurrency 预算键，不与
# 商密/注入 judge 互挤——#114 §8）
_SETTINGS_ROUTING_KEYS = {"enabled", "threshold", "tiers", "timeout", "max_concurrency"}
# issue #119 可选增补键（节内出席才校验，缺席=运行侧内置默认保现网行为，对齐 routing
# 节自身可选语义；与上方必填五键分开计数——missing 检查只管必填集）：
# prompt=分类系统提示（默认=app.py ROUTER_PROMPT_SYSTEM 常量逐字，#114 评测获胜版）/
# escalate_conf=升档强置信门槛（默认 0.85，0~1 含边界）/session_ttl=会话存态 TTL 秒
#（默认 3600，>0）/tool_loop_lock/thinking_lock=tool-loop 与 thinking 两道锁开关
#（默认 true 保现网锁定行为）
_SETTINGS_ROUTING_OPTIONAL_KEYS = {"prompt", "escalate_conf", "session_ttl",
                                   "tool_loop_lock", "thinking_lock"}
_SETTINGS_ROUTING_TIERS = {"simple", "complex"}
# tiers 映射值进 extAuthz 响应头（x-resolved-model）——白名单字符形态与 app.py
# _CLASSIFY_MODEL_SAFE 同款（admin_api 不 import app，自包含复述；防响应拆分纪律一致）
_SETTINGS_MODEL_SAFE = re.compile(r"[A-Za-z0-9._:-]{1,128}")


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
    # issue #127：可选子节白名单（未知键检查放行，missing 只管必填集）
    optional = {"l2": _SETTINGS_L2_OPTIONAL_KEYS}
    for section, keys in allowed.items():
        sec = data.get(section)
        if not isinstance(sec, dict):
            return f"{section} 必须是对象"
        for k in sec:
            if k not in keys | optional.get(section, set()):
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
    # issue #105：注入第二职责三键——inject_enabled 必填布尔；注入 prompt 必填字符串，
    # 关态允许空串占位（旧文件 normalize 补默认后能存），开态必须非空
    # （「开+空」运行侧 judge_inject_text 必返 None 落 error 条，不给这种配置放行）
    if not isinstance(judge["inject_enabled"], bool):
        return "judge.inject_enabled 必须是布尔值"
    for k in ("inject_prompt_system", "inject_prompt_fewshot"):
        if not isinstance(judge[k], str):
            return f"judge.{k} 必须是字符串"
        if judge["inject_enabled"] and not judge[k]:
            return f"judge.{k} 在 inject_enabled=true 时必须非空"
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
    # issue #127：l2.opf 可选子节——出席即整节校验（三键齐全 + 类型/范围）；
    # 显式 null 不放行（对齐 routing 节评审纪律：要缺席请省略该键）
    if "opf" in data["l2"]:
        opf = data["l2"]["opf"]
        if not isinstance(opf, dict):
            return "l2.opf 必须是对象（缺席请省略该键，不接受 null）"
        for k in opf:
            if k not in _SETTINGS_OPF_KEYS | _SETTINGS_OPF_OPTIONAL_KEYS:
                return f"l2.opf 未知字段: {k}"
        missing = _SETTINGS_OPF_KEYS - set(opf)
        if missing:
            return f"l2.opf 缺字段: {', '.join(sorted(missing))}"
        if not isinstance(opf["enabled"], bool):
            return "l2.opf.enabled 必须是布尔值"
        if not _is_number(opf["timeout_ms"]) or opf["timeout_ms"] <= 0:
            return "l2.opf.timeout_ms 必须是 >0 数值"
        if not isinstance(opf["max_chars"], int) or isinstance(opf["max_chars"], bool) or opf["max_chars"] < 1:
            return "l2.opf.max_chars 必须是 ≥1 整数"
        # url 可选增补键：出席才校验（缺席=运行侧 env OPF_URL/内置默认）
        if "url" in opf and (not isinstance(opf["url"], str) or not opf["url"]):
            return "l2.opf.url 必须是非空字符串"
    # issue #117：routing 为**可选节**（与上方必填段语义不同）——缺席=合法（运行侧
    # routing.enabled 缺省 false，现网零变化；不强制旧文件/控制台往返补节）；出现即
    # 整节校验：五键齐全（防部分更新静默丢键，与必填段同精神）+ 类型 + tiers 形态。
    # issue #119：节内 prompt/escalate_conf/session_ttl/tool_loop_lock/thinking_lock
    # 为可选增补键——缺席合法（运行侧内置默认保现网），出席才校验类型/范围。
    # 评审 P2-2：键在值判——"routing": null 不放行（get 默认 None 会穿透 is not None
    # 护栏），显式 null 落 not isinstance 分支 400，fail-closed（要缺席请省略该键）。
    routing = data.get("routing")
    if "routing" in data:
        if not isinstance(routing, dict):
            return "routing 必须是对象（缺席请省略该键，不接受 null）"
        for k in routing:
            if k not in _SETTINGS_ROUTING_KEYS | _SETTINGS_ROUTING_OPTIONAL_KEYS:
                return f"routing 未知字段: {k}"
        # 必填集只管旧五键（issue #119 新键为可选增补，缺席合法——参考顶层 version/
        # _comment 可选键先例：出席才校验类型/范围）
        missing = _SETTINGS_ROUTING_KEYS - set(routing)
        if missing:
            return f"routing 缺字段: {', '.join(sorted(missing))}"
        if not isinstance(routing["enabled"], bool):
            return "routing.enabled 必须是布尔值"
        if not _is_number(routing["threshold"]) or not 0 <= routing["threshold"] <= 1:
            return "routing.threshold 必须是 0~1 数值"
        tiers = routing["tiers"]
        if not isinstance(tiers, dict):
            return "routing.tiers 必须是对象"
        for k in tiers:
            if k not in _SETTINGS_ROUTING_TIERS:
                return f"routing.tiers 未知档位: {k}"
        missing_tiers = _SETTINGS_ROUTING_TIERS - set(tiers)
        if missing_tiers:
            return f"routing.tiers 缺档位: {', '.join(sorted(missing_tiers))}"
        for k in sorted(_SETTINGS_ROUTING_TIERS):
            v = tiers[k]
            if not isinstance(v, str) or not v:
                return f"routing.tiers.{k} 必须是非空字符串"
            if not _SETTINGS_MODEL_SAFE.fullmatch(v):
                return f"routing.tiers.{k} 含非法字符（模型名须匹配 [A-Za-z0-9._:-]{{1,128}}，进响应头防拆分）"
        if not _is_number(routing["timeout"]) or routing["timeout"] <= 0:
            return "routing.timeout 必须是 >0 数值"
        if not isinstance(routing["max_concurrency"], int) or isinstance(routing["max_concurrency"], bool) or routing["max_concurrency"] < 1:
            return "routing.max_concurrency 必须是 ≥1 整数"
        # issue #119 可选增补键：出席才严校（缺席=运行侧默认，不落校验）
        if "prompt" in routing and (not isinstance(routing["prompt"], str) or not routing["prompt"]):
            return "routing.prompt 必须是非空字符串"
        if "escalate_conf" in routing and (not _is_number(routing["escalate_conf"])
                                           or not 0 <= routing["escalate_conf"] <= 1):
            return "routing.escalate_conf 必须是 0~1 数值"
        if "session_ttl" in routing and (not _is_number(routing["session_ttl"])
                                         or routing["session_ttl"] <= 0):
            return "routing.session_ttl 必须是 >0 数值"
        for k in ("tool_loop_lock", "thinking_lock"):
            if k in routing and not isinstance(routing[k], bool):
                return f"routing.{k} 必须是布尔值"
    return None


def _audit(me, op, changed=None):
    """配置面写操作审计（layer=admin，shadow_log 同槽）：actor=email（缺省 id，再缺 unknown），
    changed=变更键路径注解列表。配置值不落盘（settings 含 prompt 半敏感文本、词表值即机密词），
    只记「谁、何时、改了哪些键」。跟随 shadow_log 永不抛纪律——审计失败不影响配置写本身。"""
    actor = (me or {}).get("email") or (me or {}).get("id") or "unknown"
    shadow_log.record("admin", op=op, actor=actor, changed=changed or [])


def _diff_settings(old, new) -> list:
    """settings 两层键路径 diff（段.键，值不落盘）；version/_comment 噪音不计；
    旧文件缺失/损坏按全量新建（返回 ["*"]）；无实质变更返回 []。"""
    if not isinstance(old, dict):
        return ["*"]
    out = []
    for k in sorted(set(old) | set(new)):
        if k == "version" or k.startswith("_"):
            continue
        ov, nv = old.get(k), new.get(k)
        if ov == nv:
            continue
        if isinstance(ov, dict) and isinstance(nv, dict):
            for sk in sorted(set(ov) | set(nv)):
                if ov.get(sk) != nv.get(sk):
                    out.append(f"{k}.{sk}")
        else:
            out.append(k)
    return out


def _settings_put(handler, _me):
    """PUT 整体替换 settings（issue #35）：校验 → write_json_atomic。
    shim 检测路径每请求重读 settings.json，写入即热生效，无需重启。
    issue #140：l1.enabled 只门控 shim 检测侧（app.py 每请求热读），不再联动渲染
    config.yaml——原 l1 翻转重渲染/回滚联动（issue #40）随网关侧格式规则一并撤除。
    读旧→写入整体持 _RMW_LOCK（issue #139，并发 PUT 串行不丢更新）。"""
    payload = _read_body(handler)
    if payload is None:
        return
    err = _validate_settings(payload)
    if err:
        _respond(handler, 400, {"error": err})
        return
    with _RMW_LOCK:  # issue #139：读旧→写入整体串行（并发 PUT 丢更新）
        old_settings = _load_json_file(SETTINGS_PATH)  # 审计 diff 基准（缺失 → None 记全量新建）
        try:
            write_json_atomic(SETTINGS_PATH, payload)
        except OSError as e:
            _respond(handler, 500, {"error": f"settings 写入失败: {e}"})
            return
    _audit(_me, "put_settings", _diff_settings(old_settings, payload))
    _respond(handler, 200, payload)


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
    全部 patterns 过 re.compile；gateway_patterns 禁 Rust regex 不支持构造（lookaround/backreference）。
    issue #140：gateway_scope 已无消费方（不再渲染 config.yaml），保留为历史文件兼容字段；
    gateway_patterns 改由 shim 原文直扫通道消费（app.py norm_secret_hits raw 通道）——Rust 构造
    禁令原样保留（约束更严无害，且兼容未来任何 RE2 消费方回归），校验整体原样防脏数据落盘。"""
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
        # issue #140：gateway_scope 已无消费方（不再渲染 config.yaml），仅保留类型校验
        # 防脏数据落盘；原 _VALID_SCOPES 枚举白名单（issue #126）随渲染链路一并撤除。
        gs = r.get("gateway_scope")
        if gs is not None:
            if not isinstance(gs, list) or any(not isinstance(s, str) for s in gs):
                return f"rules[{i}].gateway_scope 必须是字符串数组"
    return None


def _format_rules_put(handler, _me):
    """PUT 整体替换 format-rules（issue #33）：校验 → 写 JSON。
    shim 检测路径每请求重读 format-rules.json，写入即热生效，无需重启。
    issue #140：判定收回 shim 单点，不再渲染 config.yaml（原 splice/渲染后校验/回滚
    联动随网关侧格式规则一并撤除）——写入失败以外的故障面只剩校验 400。"""
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
        _respond(handler, 500, {"error": f"format-rules 写入失败: {e}"})
        return
    _audit(_me, "put_format_rules", [f"rules({len(payload['rules'])})"])
    _respond(handler, 200, payload)


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


def _edm_ingest_text(handler, name: str, text: str, me=None):
    """EDM 语料入库共用段（粘贴 JSON 与文件直传两条上传路径汇入，issue #48）：
    text 校验 → corpus 原文原子写 → 该文档指纹全量重算并入 fingerprints.json（不动其他文档）
    → 原子写。指纹写失败时回滚删 corpus 文件，两侧不留半更新。
    加载→查重→corpus 写→指纹库改写整体持 _RMW_LOCK（issue #139）：并发入库串行——
    裸跑时两篇同库文档互持 stale 快照覆盖，先入库者的指纹条目被丢（corpus 原文成孤儿）。"""
    if len(edm_lib.normalize(text)) < _EDM_MIN_TEXT:
        _respond(handler, 400, {"error": f"text 过短：归一化后不足 {_EDM_MIN_TEXT} 字符，无法产生有效行级指纹"
                                          "（整段 shingle 单指纹也达不到命中阈值 2，入库即死规则）"})
        return
    with _RMW_LOCK:
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
    _audit(me, "edm_ingest", [f"name={name}", f"shingles({len(fps['shingles'])})",
                              f"lines({len(fps['lines'])})"])
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
    _edm_ingest_text(handler, name, text, me=_me)


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
    _edm_ingest_text(handler, name, text, me=_me)


def _edm_corpus_delete_item(handler, _me, name):
    """DELETE 删除 EDM 语料文档（issue #34）：指纹条目（权威）+ corpus 文件；不存在 → 404。
    name 正则校验与 POST 同款（review #4 双保险：薄壳 quote 误放行的分隔符等在此兜底）。
    先写指纹库（检测权威源），corpus 文件缺失容忍（孤儿文件不阻断删除）。
    读-改-写持 _RMW_LOCK（issue #139）：与 _edm_ingest_text 同写 fingerprints.json，不互斥则
    并发 ingest/delete 互持 stale 快照覆盖，丢条目或复活已删文档。"""
    if not _EDM_NAME_RE.match(name):
        _respond(handler, 400, {"error": "name 必须匹配 [A-Za-z0-9_.-]{1,64}"})
        return
    with _RMW_LOCK:
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
    _audit(_me, "edm_delete", [f"name={name}"])
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
    校验：name 非空且不与现有重复；字段规则见 _validate_recognizer_fields。
    读-改-写持 _RMW_LOCK（issue #139）：并发 POST 串行，查重基于先写者落盘结果。"""
    payload = _read_body(handler)
    if payload is None:
        return
    with _RMW_LOCK:
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
    """PUT 替换指定 name 的 recognizer（issue #32）：字段校验同 POST，name 以 URL 为准；不存在 → 404。
    读-改-写持 _RMW_LOCK（issue #139）：与 POST/DELETE 互斥，后写者基于先写者落盘结果再改。"""
    payload = _read_body(handler)
    if payload is None:
        return
    with _RMW_LOCK:
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
    """DELETE 删除指定 name 的 recognizer（issue #32）；不存在 → 404；删空数组允许。
    读-改-写持 _RMW_LOCK（issue #139）：与 POST/PUT 互斥，后写者基于先写者落盘结果再改。"""
    with _RMW_LOCK:
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
    issue #81：approve 可带 body {"tier": "<档名>"} 覆盖执行档位（新建默认体验档、提额默认所求档）。
    issue #128：approve 可带 body {"project_override": "<项目 gid>"} 指定正式项目（仅新建接受，
    upgrade 带 → store 层 400；解析风格同 tier/reason——缺省/空值落空串走默认语义）。"""
    import key_requests
    reason = ""
    tier = ""
    project_override = ""
    if action in ("approve", "reject"):
        payload = _read_body(handler)
        if payload is None:
            return  # 413/400 已回出
        if isinstance(payload, dict):
            if action == "reject":
                reason = payload.get("reason") or ""
            else:
                tier = payload.get("tier") or ""
                project_override = payload.get("project_override") or ""
    try:
        req, err = key_requests.resolve_request(rid, action, reason, tier_override=tier,
                                                project_override=project_override)
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


# ---- block 层身份反查（issue #134）：key_hash → key 名/用户邮箱 ----
# 落条侧只存 SHA-256 指纹（app.py key_hash_from_headers，与 bypass_keys 同纪律不明文落盘）；
# 读侧（本出口）用 admin token 查 apiKeys 现算哈希比对回填 key_name/user_email。
# 读路径低频，60s 缓存 key 清单；反查失败 fail-open 原样透出（不臆造身份）。

_APIKEYS_FOR_ENRICH_QUERY = """
  query BlockEnrichKeys($first: Int) {
    apiKeys(first: $first) {
      edges { node { key name user { email } } }
    }
  }
"""

_keymap_cache = {"ts": 0.0, "map": {}}


def _fetch_all_apikeys() -> list:
    """admin token 拉全量 key（含明文，仅内存比对不落盘）。懒 import 防环（self_api→admin_api）。"""
    import self_api
    data = self_api._get_ax().gql(_APIKEYS_FOR_ENRICH_QUERY, {"first": 200})
    return [e["node"] for e in (data.get("apiKeys") or {}).get("edges") or []]


def _block_key_map(fetch_keys) -> dict:
    """key_hash → {key_name, user_email}；60s 缓存。fetch_keys 注入便于测试。"""
    now = time.time()
    if now - _keymap_cache["ts"] < 60:
        return _keymap_cache["map"]
    m = {}
    for k in fetch_keys() or []:
        raw = k.get("key") or ""
        if raw:
            m[hashlib.sha256(raw.encode()).hexdigest()] = {
                "key_name": k.get("name") or "",
                "user_email": (k.get("user") or {}).get("email") or "",
            }
    _keymap_cache["ts"], _keymap_cache["map"] = now, m
    return m


def _enrich_block_records(records: list, fetch_keys=None) -> list:
    """block 条回填 key_name/user_email（返回副本不改原记录）；
    无 key_hash / 哈希对不上 / 反查失败 → 不标注不臆造。"""
    fetch_keys = fetch_keys or _fetch_all_apikeys
    if not any(r.get("key_hash") for r in records):
        return records
    try:
        km = _block_key_map(fetch_keys)
    except Exception as e:
        print(f"[admin] block 身份反查失败（原样透出）: {type(e).__name__}", flush=True)
        return records
    out = []
    for r in records:
        kh = r.get("key_hash")
        if kh and kh in km:
            r = {**r, **km[kh]}
        out.append(r)
    return out


def _shadow_verdicts(handler, _me):
    """GET /dlp-admin/shadow-verdicts：各层 stats + 近期判定记录（新到旧，不落原文）。
    query 参数：n（默认 50，1..500 截断）、layer（judge/pg/rules/judge_inject/router/bypass/block 过滤，非法值 400）。
    issue #101：stats 透出 warned 聚合数（judge warn 试点观察期误报对账口径：
    warned/hits 同窗可比），records 逐条带 warned/model 脱敏字段供核对。
    issue #104：rules 层（注入规则层）判定条同槽透出，records 带 groups 命中模式组名。
    issue #105：judge_inject 层（judge 注入第二职责）判定条同槽透出，records 带
    attack_type 攻击类型标签；与商密 judge 层分层统计（独立层名，天然不串档）。
    issue #117：router 层（auto 智能路由）决策条同槽透出，records 带 resolved_model/
    tier/p_complex/reason/session 字段（决策日志供阈值校准回放）。
    issue #129：bypass 层（Key 绕行审计）条同槽透出，records 带 reason 范围说明。
    issue #130：block 层（词表/归一化 secrets/EDM 内容阻断）条同槽透出，records 带
    rule_ids 命中规则族标识。
    issue #134：block 层增强——records 另带 side（request/response）、key_hash
    （SHA-256 指纹）与读侧回填的 key_name/user_email（身份反查：admin GraphQL 拉
    key 清单现算哈希比对，60s 缓存，失败 fail-open 不标注）、excerpts（命中摘录：
    词表原样/secrets 掩码）。"""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    try:
        n = int((q.get("n") or ["50"])[0])
    except ValueError:
        n = 50
    n = max(1, min(500, n))
    layer = (q.get("layer") or [""])[0] or None
    if layer not in (None, "judge", "pg", "rules", "judge_inject", "router", "bypass", "block", "admin"):
        _respond(handler, 400, {"error": "layer 必须是 judge、pg、rules、judge_inject、router、bypass、block 或 admin"})
        return
    records = shadow_log.tail(n, layer=layer)
    if layer in (None, "block"):
        records = _enrich_block_records(records)
    _respond(handler, 200, {
        "stats": {l: shadow_log.stats(l) for l in ("judge", "pg", "rules", "judge_inject", "router", "bypass", "block", "admin")},
        "records": records,
    })


# ---- Key 级 DLP 绕行名单（issue #129）：只存哈希不落明文；名单增删改走写级鉴权 ----


def _bypass_keys_get(handler, _me):
    """GET /dlp-admin/bypass-keys（读级）：名单全量（条目无 token 明文，id 即 SHA-256）。"""
    _respond(handler, 200, bypass_keys.load())


def _bypass_keys_post(handler, me):
    """POST 登记绕行 key：{token,label,scope,layers?} → 201 {entry}。
    token 只用于算哈希，校验/应答/日志一律不回显明文（对齐词表 dup 不 echo 纪律）。"""
    payload = _read_body(handler)
    if payload is None:
        return
    if not isinstance(payload, dict):
        _respond(handler, 400, {"error": "body 必须是 JSON 对象"})
        return
    try:
        entry = bypass_keys.add(payload.get("token") or "", payload.get("label") or "",
                                payload.get("scope") or "", payload.get("layers"),
                                (me or {}).get("id") or "")
    except ValueError as e:
        _respond(handler, 400, {"error": str(e)})
        return
    _audit(me, "bypass_add", [f"label={entry['label']}", f"scope={entry['scope']}",
                              f"layers={','.join(entry['layers'])}", f"id={entry['id'][:8]}"])
    _respond(handler, 201, {"entry": entry})


def _bypass_keys_put_item(handler, _me, kid):
    """PUT 改指定条：{label?,scope?,layers?,enabled?}（缺席键保持，{}=校验性 no-op 返回现状）；未知 id → 404。"""
    payload = _read_body(handler)
    if payload is None:
        return
    if not isinstance(payload, dict):
        _respond(handler, 400, {"error": "body 必须是 JSON 对象"})
        return
    try:
        entry = bypass_keys.update(kid, payload)
    except KeyError:
        _respond(handler, 404, {"error": "绕行条目不存在"})
        return
    except ValueError as e:
        _respond(handler, 400, {"error": str(e)})
        return
    _audit(_me, "bypass_update", [f"id={kid[:8]}"] + [f"{k}=" for k in sorted(payload)])
    _respond(handler, 200, {"entry": entry})


def _bypass_keys_delete_item(handler, _me, kid):
    """DELETE 移除指定条（未知 id 幂等 200，对齐删空数组允许纪律）。"""
    bypass_keys.remove(kid)
    _audit(_me, "bypass_remove", [f"id={kid[:8]}"])
    _respond(handler, 200, {"ok": True})


# issue #138：白名单 key 服务端匹配——控制台不再拉全量 key 明文到浏览器算哈希比对
# （名单只存哈希，明文只在 shim 内存过手）。APIKeyWhereInput.idIn 2026-09-08 活栈内省
# 实证支持（[ID!] 列表过滤），一次批量查询；不存在/无权限的 id 不在 edges 出现——
# 自然静默跳过。keyIds 上限与 apiKeys first 对齐（idIn ≤500 ⇒ first:500 必取全）。
_MATCH_MAX_IDS = 500
_APIKEYS_BY_IDS_QUERY = (
    "query($ids: [ID!]!) { apiKeys(first: 500, where: {idIn: $ids}) { edges { node { id key } } } }"
)


def _query_apikeys_by_ids(token: str, ids: list):
    """携调用方 Bearer 按 id 批量查 axonhub key 明文（issue #138；与 _introspect 同款
    Bearer 透传纪律，不加客服端权——调用方看不见的 key 上游自然不返回）。
    返回 node 列表；传输/报文失败或应答无 data.apiKeys → None（调用方 fail-closed 503）。
    明文只在本函数与调用点内存过手：不落盘不落日志。"""
    body = json.dumps({"query": _APIKEYS_BY_IDS_QUERY, "variables": {"ids": ids}}).encode()
    req = urllib.request.Request(
        AXONHUB_ADMIN_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=INTROSPECT_TIMEOUT) as r:
            payload = json.load(r)
    except Exception:
        return None
    conn = (payload.get("data") or {}).get("apiKeys")
    if not isinstance(conn, dict) or not isinstance(conn.get("edges"), list):
        return None
    return [e.get("node") or {} for e in conn["edges"]]


def _bypass_keys_match_post(handler, me):
    """POST /dlp-admin/bypass-keys/match（issue #138，读语义 read_api_keys 档）：
    {"keyIds": [gid, ...]}（>500 → 400）→ 携调用方 Bearer 批量取明文，服务端算
    SHA-256 与绕行名单比对，只回匹配条（keyId/entryId/label/scope/enabled，不含明文，
    停用条照常回报 enabled=false 由前端展示）；未命中/查不到的 id 静默跳过，整体仍 200。"""
    payload = _read_body(handler)
    if payload is None:
        return
    ids = payload.get("keyIds") if isinstance(payload, dict) else None
    if not isinstance(ids, list) or any(not isinstance(x, str) or not x for x in ids):
        _respond(handler, 400, {"error": "keyIds 必须是非空字符串数组"})
        return
    if len(ids) > _MATCH_MAX_IDS:
        _respond(handler, 400, {"error": f"keyIds 超上限: {len(ids)} > {_MATCH_MAX_IDS}"})
        return
    if not ids:
        _respond(handler, 200, {"matches": []})
        return
    auth = handler.headers.get("Authorization") or ""
    _, _, token = auth.partition(" ")  # _authorize 已校验形态，此处重取调用方 token 透传
    nodes = _query_apikeys_by_ids(token, ids)
    if nodes is None:
        _respond(handler, 503, {"error": "axonhub key 查询不可用"})
        return
    entries = {k.get("id"): k for k in bypass_keys.load().get("keys") or []}
    matches = []
    for node in nodes:
        raw = node.get("key") or ""
        if not raw:
            continue
        entry = entries.get(hashlib.sha256(raw.encode("utf-8")).hexdigest())
        if entry is not None:
            matches.append({"keyId": node.get("id"), "entryId": entry.get("id"),
                            "label": entry.get("label"), "scope": entry.get("scope"),
                            "enabled": bool(entry.get("enabled"))})
    matches.sort(key=lambda m: str(m["keyId"]))  # 确定性输出
    _respond(handler, 200, {"matches": matches})


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
    ("GET", "/dlp-admin/edm/corpus"): ("read", _edm_corpus_get),
    ("POST", "/dlp-admin/edm/corpus"): ("write", _edm_corpus_post),
    ("POST", "/dlp-admin/edm/corpus/upload"): ("write", _edm_corpus_upload_post),
    ("GET", "/dlp-admin/settings"): ("read", _settings_get),
    ("PUT", "/dlp-admin/settings"): ("write", _settings_put),
    ("GET", "/dlp-admin/key-requests"): ("read", _kr_list),  # issue #79：控制台 Key 申请审批
    ("GET", "/dlp-admin/shadow-verdicts"): ("read", _shadow_verdicts),  # issue #92：shadow 观测出口
    ("GET", "/dlp-admin/bypass-keys"): ("read", _bypass_keys_get),  # issue #129：Key 绕行名单
    ("POST", "/dlp-admin/bypass-keys"): ("write", _bypass_keys_post),
    ("POST", "/dlp-admin/bypass-keys/match"): ("read_api_keys", _bypass_keys_match_post),  # issue #138：服务端匹配
}


# 参数化路由：/dlp-admin/recognizers/<name>、/dlp-admin/edm/corpus/<name>（URL 末段为 name 参数，PUT/DELETE 用）
_ITEM_ROUTES = {
    ("PUT", "/dlp-admin/recognizers/"): ("write", _recognizer_put_item),
    ("DELETE", "/dlp-admin/recognizers/"): ("write", _recognizer_delete_item),
    ("DELETE", "/dlp-admin/edm/corpus/"): ("write", _edm_corpus_delete_item),
    ("POST", "/dlp-admin/key-requests/approve/"): ("write", _kr_approve_item),  # issue #79
    ("POST", "/dlp-admin/key-requests/reject/"): ("write", _kr_reject_item),  # issue #79
    ("PUT", "/dlp-admin/bypass-keys/"): ("write", _bypass_keys_put_item),  # issue #129
    ("DELETE", "/dlp-admin/bypass-keys/"): ("write", _bypass_keys_delete_item),  # issue #129
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
    """原子写文本配置，与 write_json_atomic 共用 .bak 纪律。
    现消费方：EDM 语料落盘（本模块）、#139 SHIM-LOCAL-TOKEN 启动渲染（app.py）。"""
    _write_atomic(path, lambda f: f.write(text))
