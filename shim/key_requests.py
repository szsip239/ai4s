#!/usr/bin/env python3
"""控制台 Key 申请 → 管理员审批 → 自动执行（issue #79，第二通道；飞书审批定义通道并存）。

流程：员工在「我的 Key」页提交（POST /self/key-requests，caller Bearer 内省拿 me）→
本模块落待办申请（状态文件原子写）→ app bot 私信管理员审批卡（link 按钮直达控制台审批页）→
管理员在控制台点批（admin 平面 /dlp-admin/key-requests/*）→ 复用 #72/#19 执行体
（alert_poller.ensure_emp_key / apply_tier_to_user）→ 回执（卡片更新 + 申请人私信/页面状态）。
issue #85 提额方向守卫：申请侧 fail-closed 三拒 400（已是最高档 / 目标档秩 ≤ 当前档秩——
防降档空转 / 名下无 enabled Key 引导先新建），当前档查询异常 fail-open 放行 + 记日志
（执行侧兜底）；审批侧 upgrade 的 tier_override 白名单收窄为 TIERS（标准/高档，提额语义
不含体验档），new 仍 ALLOWED_TIERS 全集；执行侧逐 key never-downgrade 守卫见
alert_poller.apply_tier_to_user（两通道同享）。
issue #86 按 Key 勾选：upgrade payload 带 keyIds（非空、全部属于本人且 enabled——守卫
一次查询同时完成归属校验与方向评估）；req 存 keyIds + keyNames 名称快照（审批卡/控制台
审批页/申请列表显示目标 Key 名称，审批期间改名不影响）；执行按子集换挂（审批期间被归档/
删除的 Key 跳过并在结果文本列明）；存量无 keyIds 字段的申请回退「全部 enabled Key」语义。
新建申请不变。
issue #89 多项目隔离：申请单存 projectId/projectName 快照（self 平面 X-Project-ID 头传入）；
创建时校验申请人是该项目成员（非成员 403、查询异常 502，fail-closed），方向守卫按
（本人, 本项目）评估；批准执行落在申请单记录的项目（与管理员当前项目解耦），新建执行时
复查成员资格（被移出项目不建 Key，结果文本说明）；列表（self/admin 平面）按项目过滤，
无项目字段的存量申请视为 Default；飞书审批老通道无项目上下文，维持落 Default 不变。
issue #90：dup 判定键从（email, kind）补为（email, kind, projectId）——同项目同 kind
仍只允许一条 pending（防 spam 不变），跨项目互不阻塞；409 文案带冲突项目名。
issue #91 P2-1：审批卡按钮与降级群文本的 URL 追加 ?project=<urlencoded gid>
（_console_url，存量申请按 Default）——管理员点开即切到申请所属项目，不再面对空列表。
issue #128 审批指定正式项目：邀请注册的外部人员落「隔离项目」（空 scopes、零能力），管理员
批准新建申请时可指定正式项目（approve body project_override=项目 gid，仅 kind=new 接受；
upgrade 带 override → 400——提额不涉及项目变更）。gid 先经 myProjects 存在性校验（慢调用故
锁外、_executing 登记后执行；不存在 400、查询异常 502，均保持 pending 可重试），通过后落
projectOverride/projectNameOverride 快照并持久化；执行落 override 项目——申请人非成员先以
scopes=[] 零能力入项（与员工自动入项 PROJECT_MEMBER_SCOPES 的 read/write_requests 刻意不同：
审批只解决「落在哪」，不白送请求读写能力，能力由项目管理员后续按需下发）再建 Key，已是成员
直接建；无 override 时 #89 fail-closed 成员复查原样不动。
申请人撤回（issue #80）：POST /self/key-requests/<id>/cancel，仅本人 + 仅 pending，
置 canceled + 管理员回执（卡片更新/无卡降级群文本），幂等不重复通知。

卡片回调选型（调研结论，issue 已记录取舍）：飞书卡片按钮原生回调（card.action.trigger
长连接）需要 (a) 开放平台后台逐 App 配置回调订阅——不在代码库/IaC 内，本环境无法验证，
配错即按钮静默无响应；(b) shim 引入 lark-oapi + 常驻 WS 重连——与镜像 stdlib 优先 +
pin + 懒加载纪律相悖；(c) 卡片必须 app bot 发送（webhook 自定义机器人卡片不支持回调）。
故选最简可靠替代：卡片仅通知（link 按钮）+ 管理员控制台点批——全部走已实证通道，
本环境端到端可测，零新依赖零后台配置。

交付分流：申请人 email 以 @casdoor.oidc 结尾（飞书身份）→ 明文私信申请人；
否则（本地/钉钉/企微账号）→ 明文私信管理员备付。无论哪种身份，申请人都可在「我的 Key」页
查看本人 Key 明文（issue #81 项4，/self/keys 服务端 userID=me 过滤）。

幂等：状态门（非 pending 不再执行，重复回调/重复点批返回现状）+ ensure_emp_key 按名找回
（key 名由申请 id 派生，确定性）。执行失败状态保持 pending（管理员可重试），与 #72
「失败不标记」同语义。并发（评审 P2）：approve 锁外执行期间以 _executing 内存标记把
reject/重复 approve/sweep 超时挡在门外（409/让位）——执行有真实副作用，不得半途改终态。

状态文件：默认 /state/key-requests.json（与 alert-state.json 同目录，deploy/.local/alert-state
挂载，gitignored）；admin_api 原子写 + 模块锁保护读改写（HTTP 线程与巡检线程并发）。
本模块不进检测路径；单请求失败只影响本请求。
"""
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request

import admin_api  # write_json_atomic 原子写复用（唯一 tmp + .bak 滚动 + finally 清理）
import alert_poller  # 执行体/飞书 helper 复用（import 不起线程；feishu_dm/ensure_emp_key 等）
import feishu_lib  # 卡面时间东八 helper（iso_to_cst/day_key_cst）

REQUESTS_PATH = os.environ.get(
    "KEY_REQUESTS_PATH",
    os.path.join(os.path.dirname(alert_poller.STATE_PATH) or ".", "key-requests.json"),
)
# 管理员审批卡接收人（app bot 私信）；未配置降级为群 webhook 文本通知
FEISHU_ADMIN_OPEN_ID = os.environ.get("FEISHU_ADMIN_OPEN_ID", "")
# 审批卡 link 按钮落点（控制台审批页）；env 置空/未设都回退默认（compose 空串注入场景）
# 约定：必须是裸路径（不含 query）——_console_url 会按申请项目追加 ?project=<gid>（issue #91）
CONSOLE_URL = os.environ.get("KEY_REQUEST_CONSOLE_URL") or "http://localhost:3000/key-requests"
if not os.environ.get("KEY_REQUEST_CONSOLE_URL"):
    # 评审 P2：未配置时卡片 link 落点是 localhost 默认值，管理员点按钮打到的是自己本机——
    # 模块加载（懒 import 于首次请求/巡检）时显式 warning，提示运维配置
    print(f"[keyreq] KEY_REQUEST_CONSOLE_URL 未配置，审批卡 link 落点回退默认 {CONSOLE_URL}"
          "（非本机管理员不可达，请在 deploy/.env 配置控制台地址）", flush=True)
# 待办申请超时：超时置 expired + 回执（巡检线程 sweep_expired 每轮检查）
REQUEST_TTL = alert_poller._env_int("KEY_REQUEST_TTL", 72 * 3600)
# 提额目标档白名单（与 alert_poller.parse_tier 可识别的两档一致）；
# issue #85 起审批侧提额申请的 tier_override 也收窄到此集合（提额语义不含体验档，
# 与执行侧 apply_tier_to_user 逐 key never-downgrade 守卫双保险）
TIERS = ("标准档", "高档")
# 审批可选档全集（issue #81 管理员同意时可改选）：仅新建申请用；提额申请收窄为 TIERS（issue #85）
# 与前端审批弹窗 web/src/ai4s/pages/key-requests/Ai4sKeyRequestsPage.tsx APPROVE_TIERS/UPGRADE_TIERS 双向同源，改动需两侧同步
ALLOWED_TIERS = (alert_poller.KEY_INIT_TIER,) + TIERS
MAX_PURPOSE = 200
MAX_REASON = 200
# 状态文件滚动上限（与 approval_done[-200:] 同款纪律）
MAX_REQUESTS = 200

_lock = threading.Lock()
# 执行中申请 id 集合（评审 P2 竞态修复）：approve 锁外执行期间登记，reject/重复 approve
# 得 409、sweep 超时让位。只作内存标记不持久化——容器重启即清空，被打断的执行保持
# pending 可重试，无残留风险。
_executing = set()

_ax = None  # 模块级 Axonhub 单例（token 缓存；login 惰性）——self_api 同款


def _get_ax():
    global _ax
    if _ax is None:
        _ax = alert_poller.Axonhub()
    return _ax


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _open_id_of(email: str):
    """飞书身份（JIT 账号 email = ou_*@casdoor.oidc）→ open_id；否则 None（sso-oidc.md 约定）。"""
    suffix = "@casdoor.oidc"
    return email[: -len(suffix)] if (email or "").endswith(suffix) else None


# ---- 状态存取（锁 + 原子写）----


def _load() -> list:
    try:
        with open(REQUESTS_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("requests") or []
    except Exception:
        return []


def _save(reqs: list):
    d = os.path.dirname(REQUESTS_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    admin_api.write_json_atomic(REQUESTS_PATH, {"version": 1, "requests": reqs[-MAX_REQUESTS:]})


def shape_public(req: dict) -> dict:
    """对外形状（self/admin 平面同一份）：剥内部字段（cardMessageId、ts）。"""
    return {k: v for k, v in req.items() if k not in ("cardMessageId", "ts")}


def _req_project_id(req: dict) -> str:
    """申请单所属项目（issue #89）：无项目字段的存量申请视为 Default（过滤与执行同此口径）。"""
    return req.get("projectId") or alert_poller.KEY_PROJECT_ID


def list_requests(email: str = "", project_id: str = None) -> list:
    """申请列表（新到旧）。email 非空=员工侧本人过滤；project_id 非空=按项目过滤（issue #89）。"""
    with _lock:
        reqs = _load()
    if email:
        reqs = [r for r in reqs if (r.get("applicant") or {}).get("email") == email]
    if project_id:
        reqs = [r for r in reqs if _req_project_id(r) == project_id]
    return [shape_public(r) for r in reversed(reqs)]


# ---- 飞书卡片（app bot；webhook 自定义机器人卡片不支持回调，通知卡用 link 按钮）----


def _feishu_card_send(open_id: str, card: dict):
    """私信交互卡；成功返回 message_id（后续回执更新用），失败 None（best-effort）。"""
    try:
        body = {"receive_id": open_id, "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False)}
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Authorization": f"Bearer {alert_poller.feishu_tenant_token()}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        if d.get("code") == 0:
            return (d.get("data") or {}).get("message_id")
        print(f"[keyreq] 审批卡发送非零: code={d.get('code')}", flush=True)
    except Exception as e:
        print(f"[keyreq] 审批卡发送失败: {type(e).__name__}: {e}", flush=True)
    return None


def _feishu_card_update(message_id: str, card: dict) -> bool:
    """更新已发出的卡片（回执：状态+结果，无明文）。
    接口实证（issue #80 冒烟抓出 #79 存量 bug）：更新应用卡片消息用 PATCH /im/v1/messages/:id
    且 body 只含 content；PUT + msg_type=interactive 恒 400（230001 invalid msg_type）——
    best-effort 静默失败，#79 的 approve/reject 回执卡更新实际从未成功。"""
    try:
        body = {"content": json.dumps(card, ensure_ascii=False)}
        req = urllib.request.Request(
            f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Authorization": f"Bearer {alert_poller.feishu_tenant_token()}",
                     "Content-Type": "application/json"},
            method="PATCH",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        if d.get("code") == 0:
            return True
        print(f"[keyreq] 卡片更新非零: code={d.get('code')}", flush=True)
    except Exception as e:
        print(f"[keyreq] 卡片更新失败: {type(e).__name__}: {e}", flush=True)
    return False


_STATUS_LABEL = {"pending": "待审批", "approved": "已通过", "rejected": "已拒绝", "expired": "已超时",
                 "canceled": "已撤回"}  # canceled: issue #80 申请人撤回
_STATUS_COLOR = {"pending": "orange", "approved": "green", "rejected": "red", "expired": "grey",
                 "canceled": "grey"}


def _detail_line(req: dict) -> str:
    # issue #89：审批卡/回执显示项目名（管理员不再只能去控制台辨认目标项目）；
    # 存量申请无快照 → Default（与过滤/执行同口径）；
    # issue #128：批准时指定了正式项目则覆盖显示 override 名（回执卡/结果可核对落点）
    project = f"**项目**: {req.get('projectNameOverride') or req.get('projectName') or 'Default'}"
    if req["kind"] == "new":
        return f"{project}\n**用途**: {req.get('purpose') or '—'}"
    # issue #86：提额按所选 Key 列名（名称快照）；fail-open 无快照但有 keyIds 时回退列 id；
    # 两者皆无（真存量申请）回退原全量语义文案
    names = req.get("keyNames") or []
    ids = req.get("keyIds") or []
    if names:
        scope = f"，目标 Key: {', '.join(names)}"
    elif ids:
        scope = f"，目标 Key: {', '.join(ids)}（名称快照缺失）"
    else:
        scope = "（作用于其全部 enabled Key）"
    return f"{project}\n**目标档**: {req.get('tier') or '—'}{scope}"


def _console_url(req: dict) -> str:
    """审批页落点 URL 带项目参数（issue #91 P2-1）：审批卡按钮/降级群文本直达申请所属项目
    （web 审批页 validateSearch 读 project 并切换 projectStore）；存量无字段申请按 Default
    （_req_project_id 口径）。"""
    return f"{CONSOLE_URL}?project={urllib.parse.quote(_req_project_id(req), safe='')}"


def _request_card(req: dict) -> dict:
    """待审批卡：摘要 + 「前往控制台审批」link 按钮（不含任何明文）。"""
    kind_label = "新建 Key" if req["kind"] == "new" else "额度提额"
    content = (
        f"**申请人**: {(req.get('applicant') or {}).get('email')}\n"
        f"{_detail_line(req)}\n"
        f"**申请 ID**: {req['id']}\n**时间**: {feishu_lib.iso_to_cst(req.get('createdAt'))}"
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "orange",
                   "title": {"tag": "plain_text", "content": f"[ai4s] Key 申请待审批（{kind_label}）"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": content}},
            {"tag": "action", "actions": [{
                "tag": "button", "type": "primary",
                "text": {"tag": "plain_text", "content": "前往控制台审批"},
                "url": _console_url(req),
            }]},
        ],
    }


def _receipt_card(req: dict) -> dict:
    """回执卡（更新原审批卡）：终态 + 结果摘要；绝不含明文。"""
    kind_label = "新建 Key" if req["kind"] == "new" else "额度提额"
    status = req.get("status") or "pending"
    content = (
        f"**申请人**: {(req.get('applicant') or {}).get('email')}\n"
        f"{_detail_line(req)}\n"
        f"**申请 ID**: {req['id']}\n**结果**: {req.get('result') or '—'}"
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": _STATUS_COLOR.get(status, "grey"),
                   "title": {"tag": "plain_text",
                             "content": f"[ai4s] Key 申请{_STATUS_LABEL.get(status, status)}（{kind_label}）"}},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
    }


def _notify_admin_new_request(req: dict):
    """新申请通知管理员：配置了 FEISHU_ADMIN_OPEN_ID 走私信审批卡，否则降级群 webhook 文本。"""
    if FEISHU_ADMIN_OPEN_ID and alert_poller.FEISHU_APP_ID and alert_poller.FEISHU_APP_SECRET:
        mid = _feishu_card_send(FEISHU_ADMIN_OPEN_ID, _request_card(req))
        if mid:
            with _lock:
                reqs = _load()
                cur = next((r for r in reqs if r["id"] == req["id"]), None)
                if cur is not None:
                    cur["cardMessageId"] = mid
                    _save(reqs)
            if cur is not None and cur["status"] != "pending":
                # 发卡期间已被点批/撤回（评审 P2 窄竞态）：当时的回执因无 cardMessageId 走了
                # 降级通道，这里补一次卡片更新——否则管理员手里留一张永不更新的待审批卡
                _feishu_card_update(mid, _receipt_card(cur))
            return
        print(f"[keyreq] 审批卡未送达，降级群通知: {req['id']}", flush=True)
    kind_label = "新建 Key" if req["kind"] == "new" else "额度提额"
    alert_poller.send_feishu(
        f"[ai4s Key 申请] 待审批（{kind_label}）\n"
        f"申请人: {(req.get('applicant') or {}).get('email')}\n"
        f"{_detail_line(req).replace('**', '')}\n"
        f"申请 ID: {req['id']}\n请到控制台 Key 审批页处理: {_console_url(req)}"
    )


def _notify_resolved(req: dict, applicant_dm_text: str = ""):
    """终态回执：更新管理员审批卡（有 cardMessageId）+ 申请人飞书私信（仅飞书身份且给了文案时）。
    best-effort，失败只记日志。"""
    mid = req.get("cardMessageId")
    if mid:
        _feishu_card_update(mid, _receipt_card(req))
    open_id = (req.get("applicant") or {}).get("openId")
    if open_id and applicant_dm_text:
        alert_poller.feishu_dm(open_id, applicant_dm_text)


def _notify_canceled(req: dict):
    """撤回回执给管理员（issue #80）：有审批卡（cardMessageId）→ 更新为已撤回；无卡
    （未配 FEISHU_ADMIN_OPEN_ID 的降级群通知场景）→ 群文本注明。申请人本人操作，不再私信申请人。
    best-effort，失败只记日志。"""
    mid = req.get("cardMessageId")
    if mid:
        _feishu_card_update(mid, _receipt_card(req))
        return
    kind_label = "新建 Key" if req["kind"] == "new" else "额度提额"
    alert_poller.send_feishu(
        f"[ai4s Key 申请] 申请人已撤回（{kind_label}）\n"
        f"申请人: {(req.get('applicant') or {}).get('email')}\n"
        f"申请 ID: {req['id']}"
    )


# ---- 生命周期 ----


def validate_payload(payload) -> tuple:
    """POST body 校验：返回 (kind, purpose, tier, key_ids, err)。err 非 None 即 400 文案。
    issue #86：kind=upgrade 必须带 keyIds（非空 Key id 列表，按 Key 勾选的目标子集）。"""
    if not isinstance(payload, dict):
        return None, None, None, None, "body 必须是 JSON 对象"
    kind = payload.get("kind")
    if kind not in ("new", "upgrade"):
        return None, None, None, None, "kind 必须是 new 或 upgrade"
    purpose = (payload.get("purpose") or "").strip()
    tier = (payload.get("tier") or "").strip()
    if kind == "new":
        if not purpose:
            return None, None, None, None, "新建申请必须填用途 purpose"
        if len(purpose) > MAX_PURPOSE:
            return None, None, None, None, f"purpose 超长（>{MAX_PURPOSE} 字符）"
        return kind, purpose, tier, None, None
    if tier not in TIERS:
        return None, None, None, None, f"tier 必须是 {'/'.join(TIERS)}"
    raw_ids = payload.get("keyIds")
    if (not isinstance(raw_ids, list) or not raw_ids
            or not all(isinstance(x, str) and x.strip() for x in raw_ids)):
        return None, None, None, None, "keyIds 必须是非空 Key id 列表"
    return kind, purpose, tier, list(dict.fromkeys(x.strip() for x in raw_ids)), None  # 去重保序


def _upgrade_direction_guard(me: dict, tier: str, key_ids: list, project_id: str):
    """issue #85 提额方向守卫（申请侧 fail-closed；执行侧 apply_tier_to_user 逐 key 守卫兜底）。
    issue #86：按所选 Key 子集评估——一次 USER_ENABLED_KEYS_QUERY 同时完成归属/启用校验
    （keyIds 必须全部命中本人 enabled 集合，否则 400，不区分他人/不存在/未启用以免泄露）
    与方向评估（所选全部已 ≥ 目标档才拒收，部分低于目标放行）。
    issue #89：评估集限定（本人, project_id）——归属校验不再跨项目放行别项目的同名/同主 key。
    返回 ((status, 文案) 或 None, 所选 key 列表或 None)——key 列表供 create_request 存名称快照；
    fail-open（查询异常）时 (None, None)：放行 + 记日志，名称快照缺失时显示侧回退 id。"""
    if not key_ids:  # validate_payload 已拦；直调防御（fail-closed 同形文案）
        return (400, "keyIds 必须是非空 Key id 列表"), None
    uid = me.get("id")
    if not uid:
        print("[keyreq] 提额守卫：caller 无 id，无从查档，放行（执行侧兜底）", flush=True)
        return None, None
    try:
        keys = alert_poller.query_user_enabled_keys(_get_ax(), uid, project_id)
    except Exception as e:
        print(f"[keyreq] 提额当前档查询异常，fail-open 放行: {type(e).__name__}: {e}", flush=True)
        return None, None
    if not keys:
        return (400, "本项目下无启用中的 Key，请先申请新建 Key"), None
    enabled_ids = {k["id"] for k in keys}
    if any(kid not in enabled_ids for kid in key_ids):
        return (400, "所选 Key 不存在或未启用，请刷新后重选"), None
    selected = [k for k in keys if k["id"] in set(key_ids)]
    target_rank = alert_poller.TIER_RANK[tier]
    ranks = [alert_poller.TIER_RANK.get(alert_poller.key_tier_name(k)) for k in selected]
    if all(r is not None and r >= target_rank for r in ranks):  # 未挂档（None）视为可升，放行
        if all(r == alert_poller.TIER_RANK["高档"] for r in ranks):
            return (400, "所选 Key 已是最高档（高档），无需提额"), None
        return (400, f"所选 Key 当前档位均已不低于 {tier}，无需提额（防降档/空转）"), None
    return None, selected


def create_request(me: dict, kind: str, purpose: str, tier: str, key_ids=None, project_id: str = None):
    """落一条待办申请 + 通知管理员。返回 (req_public, err)；err 形如 (status, 文案)。
    反 spam：同申请人同 kind 已有 pending → 409。
    issue #85：kind=upgrade 先过方向守卫（锁外慢查询，不落锁）。
    issue #86：upgrade 带 keyIds 目标子集；req 存 keyIds + keyNames 快照（显示用，
    审批期间改名不影响；fail-open 无快照时显示侧回退 id）。
    issue #89：project_id=目标项目（self 平面 X-Project-ID 头传入；None=Default 兜底
    直调/存量语义）。req 存 projectId + projectName 快照；申请人必须是该项目成员
    （一次 USER_PROJECTS_QUERY 完成校验+名称快照），非成员 403、查询异常 502——
    成员关系是安全闸门 fail-closed（区别于方向守卫的 fail-open），两 kind 同查。"""
    email = me.get("email") or ""
    if not email:
        return None, (502, "caller 身份无 email，无法登记申请")
    pid = project_id or alert_poller.KEY_PROJECT_ID
    uid = me.get("id")
    if not uid:
        return None, (502, "caller 身份无 id，无法校验项目成员")
    try:
        projs = alert_poller.query_user_projects(_get_ax(), uid)
    except Exception as e:
        print(f"[keyreq] 项目成员校验查询异常: {type(e).__name__}: {e}", flush=True)
        return None, (502, "项目成员校验暂不可用，请稍后重试")
    proj = next((p for p in projs if p.get("id") == pid), None)
    if not proj:
        return None, (403, "你不是该项目成员，请切换到所属项目后再发起申请")
    key_names = None
    if kind == "upgrade":
        err, selected = _upgrade_direction_guard(me, tier, key_ids, pid)
        if err:
            return None, err
        if selected:
            name_by_id = {k["id"]: k["name"] for k in selected}
            key_names = [name_by_id.get(kid, kid) for kid in key_ids]
    now = time.time()
    with _lock:
        reqs = _load()
        dup = next((r for r in reqs
                    if r["status"] == "pending" and r["kind"] == kind
                    and (r.get("applicant") or {}).get("email") == email
                    and _req_project_id(r) == pid), None)  # issue #90：dup 键补项目维度
        if dup:
            # 文案带项目名（存量无快照回退 Default，与 _req_project_id 口径一致）——
            # 列表按项目过滤后用户看不到别项目的冲突申请，必须指明冲突位置
            return None, (409, f"项目 {dup.get('projectName') or 'Default'} "
                               f"已有待审批的同类申请（{dup['id']}），请等待处理或联系管理员")
        req = {
            "id": f"kr-{feishu_lib.day_key_cst(now)}-{secrets.token_hex(3)}",
            "kind": kind,
            "purpose": purpose,
            "tier": tier,
            "applicant": {"id": me.get("id"), "email": email, "openId": _open_id_of(email)},
            "status": "pending",
            "createdAt": _iso(now),
            "ts": now,
            "resolvedAt": None,
            "result": "",
            "keyName": None,
            "keyIds": key_ids,      # issue #86：提额目标 Key 子集（new/存量申请为 None=全部 enabled）
            "keyNames": key_names,  # 名称快照（显示用；fail-open 无快照为 None）
            "projectId": pid,                # issue #89：目标项目（执行/过滤同以此为据）
            "projectName": proj.get("name"), # 项目名快照（显示用，审批期间改名不影响）
            "projectOverride": None,      # issue #128：管理员批准时指定的正式项目 gid（None=落申请项目）
            "projectNameOverride": None,  # override 项目名快照（显示用；仅批准指定后非 None）
            "cardMessageId": None,
        }
        reqs.append(req)
        _save(reqs)
    _notify_admin_new_request(req)  # 锁外慢调用；卡片 message_id 回写内部自拿锁
    return shape_public(req), None


def _deliver_new_key(req: dict, name: str, plain: str, owner_note: str, tier_name: str):
    """明文交付分流。返回 (result 摘要[无明文], applicant_dm_text 或 None——None 表示交付步骤已自行私信）。
    tier_name=实际挂档（issue #81 审批可改选）；明文兜底=「我的 Key」页本人可见（issue #81 项4）。"""
    open_id = (req.get("applicant") or {}).get("openId")
    email = (req.get("applicant") or {}).get("email")
    upgrade_hint = ("（要更高档请在「我的 Key」页发起申请提额）"
                    if tier_name == alert_poller.KEY_INIT_TIER else "")
    if open_id:
        dm_ok = alert_poller.feishu_dm(open_id, (
            f"[ai4s] 你的 API Key 已创建（控制台申请 {req['id']}）\n"
            f"Key 名称: {name}\n档位: {tier_name}{upgrade_hint}\n"
            f"明文（请立即复制保存）:\n{plain}\n"
            "保管提醒: 明文勿转发勿提交代码仓库；本消息之外也可在「我的 Key」页查看复制。"
        ))
        if dm_ok:
            return f"已建 Key {name}（{tier_name}）{owner_note}，明文已私信申请人", None
        return (f"已建 Key {name}（{tier_name}）{owner_note}；私信未送达，"
                "可在「我的 Key」页查看明文"), None
    # 非飞书账号（本地/钉钉/企微）：无法私信申请人——明文私信管理员备付，
    # 申请人照常可在「我的 Key」页查看明文（issue #81 项4 后不再依赖管理员线下交付）
    admin_note = ""
    if FEISHU_ADMIN_OPEN_ID:
        dm_ok = alert_poller.feishu_dm(FEISHU_ADMIN_OPEN_ID, (
            f"[ai4s] 控制台 Key 申请已批准（{req['id']}）\n"
            f"申请人: {email}（非飞书账号，明文同时保留给管理员备付）\n"
            f"Key 名称: {name}\n档位: {tier_name}\n"
            f"明文（勿群发勿入库）:\n{plain}"
        ))
        admin_note = ("，明文已私信管理员备份" if dm_ok
                      else "，管理员私信未送达（备付失败，不影响申请人——明文可在其「我的 Key」页查看）")
    return (f"已建 Key {name}（{tier_name}）{owner_note}；申请人为非飞书账号"
            f"{admin_note}——已通过，请在「我的 Key」页查看明文"), None


def _exit_quarantine(ax, user: dict, req: dict, pid: str) -> str:
    """批准迁入正式项目 = 转正：执行成功后将申请人迁出隔离区（External-Quarantine，按名解析
    gid，与前端邀请对话同名契约）。仅当申请源项目=隔离区且落点≠隔离区时触发；best-effort——
    Key 已建不回滚，迁出失败只记日志并在结果摘要注明可人工移除。返回结果摘要追加段（无操作为空串）。"""
    src = _req_project_id(req)
    if src == pid:
        return ""  # 落点即源项目（无迁移语义）
    projs = ax.gql(alert_poller.MY_PROJECTS_QUERY)["myProjects"] or []
    quar = next((p["id"] for p in projs
                 if p.get("name") == alert_poller.QUARANTINE_PROJECT_NAME), None)
    if not quar or src != quar:
        return ""
    try:
        ax.gql(alert_poller.REMOVE_USER_FROM_PROJECT_MUTATION,
               {"input": {"projectId": quar, "userId": user["id"]}})
        return "；已迁出隔离项目"
    except Exception as e:
        print(f"[keyreq] 迁出隔离项目失败 {req['id']}: {type(e).__name__}: {e}", flush=True)
        return "；迁出隔离项目失败，可在项目页人工移除"


def _execute(req: dict, tier_override: str = ""):
    """approve 执行体（复用 #72/#19 primitives）。返回 (result 摘要, key_name 或 None, applicant_dm_text)。
    tier_override=管理员批准时改定的档位（issue #81）：新建覆盖默认体验档、提额覆盖所求档；空串=原默认。
    issue #89：执行落在申请单记录的项目（与管理员当前所在项目解耦，切错项目不批错单）；
    无项目字段的存量申请视为 Default。kind=new 执行时复查成员资格——审批期间被移出项目
    则不建 Key，结果文本说明（参照上方「无用户」先例，申请照常转 approved 落结果）。
    issue #128：req.projectOverride 非空（管理员批准时指定正式项目）时执行落该项目——
    申请人非成员先以 scopes=[] 零能力入项（与员工自动入项 PROJECT_MEMBER_SCOPES 的
    read/write_requests 刻意不同：审批只解决「落在哪」，不白送请求读写能力，能力由项目
    管理员后续按需下发）再建 Key；已是成员直接建；projectOverride 为空时上方 #89
    fail-closed 成员复查原样不动。
    异常上抛——调用方保持 pending 供重试。"""
    email = (req.get("applicant") or {}).get("email") or ""
    user = alert_poller.find_user_by_email(_get_ax(), email)
    if not user:
        return f"axonhub 中无 {email} 用户（已删除？），未执行", None, None
    pid = req.get("projectOverride") or _req_project_id(req)  # issue #128：override 优先
    if req["kind"] == "new":
        projs = alert_poller.query_user_projects(_get_ax(), user["id"])  # 异常上抛=保持 pending 重试
        if req.get("projectOverride"):
            # issue #128：override 项目——申请人非成员先零能力入项（scopes=[]，语义见 docstring）
            # 再建 Key；入项 gql 失败上抛=保持 pending 重试（与成员复查查询同纪律）
            if pid not in {p.get("id") for p in projs}:
                _get_ax().gql(alert_poller.ADD_USER_TO_PROJECT_MUTATION, {"input": {
                    "projectId": pid, "userId": user["id"], "isOwner": False, "scopes": []}})
        elif pid not in {p.get("id") for p in projs}:
            return (f"申请人已不在项目 {req.get('projectName') or pid} 中（审批期间被移除），未建 Key",
                    None, None)
        # seed：飞书身份用 open_id（与 #72 命名一致），非飞书用 u<uid>（同名幂等不受影响——tail 是申请 id）
        seed = (req.get("applicant") or {}).get("openId") or f"u{str(user['id']).rsplit('/', 1)[-1]}"
        day = feishu_lib.day_key_cst(req.get("ts") or time.time())
        tier_name = tier_override or alert_poller.KEY_INIT_TIER
        name, plain, owner_note = alert_poller.ensure_emp_key(
            _get_ax(), user, seed, req.get("purpose") or "", day, req["id"],
            tier=tier_name, project_id=pid)
        result, dm_text = _deliver_new_key(req, name, plain, owner_note, tier_name)
        if req.get("projectOverride"):
            # issue #128：结果摘要体现落点项目（回执卡/申请列表可核对批到了哪个项目）
            result += f"，项目: {req.get('projectNameOverride') or pid}"
            # 批准迁入正式项目 = 转正：源项目是隔离区则迁出（best-effort，详见 _exit_quarantine）
            result += _exit_quarantine(_get_ax(), user, req, pid)
        return result, name, dm_text
    result = alert_poller.apply_tier_to_user(
        _get_ax(), user, tier_override or (req.get("tier") or ""),
        key_ids=req.get("keyIds"),  # issue #86：None（存量申请无字段）回退「全部 enabled Key」语义
        project_id=pid)             # issue #89：只动申请单项目内所选 Key（不再跨项目拉齐）
    dm = f"[ai4s] 你的提额申请已通过（{req['id']}）\n{result}"
    return result, None, dm


def resolve_request(rid: str, action: str, reason: str = "", tier_override: str = "",
                    project_override: str = ""):
    """管理员点批：approve=执行 + 回执；reject=标记 + 回执。
    tier_override=批准时改定的档位（issue #81）：非空必须在白名单内，否则 400——
    kind=new 为 ALLOWED_TIERS 全集，kind=upgrade 收窄为 TIERS（issue #85，提额语义不含体验档）；
    空串=默认（新建体验档/提额所求档）。
    project_override=批准时指定的正式项目 gid（issue #128）：仅 kind=new 接受——upgrade 带
    override 直接 400（廉价校验与 tier 白名单同位，提额不涉及项目变更）；非空先经 myProjects
    存在性校验（慢调用不能进锁，放在 _executing 登记后的锁外段——校验期间并发 reject/
    重复 approve/sweep 已被挡让位；不存在 400、查询异常 502，均保持 pending 可重试，
    fail-closed 不猜项目），通过后锁内落 projectOverride/projectNameOverride 快照再执行；
    空串=落申请单记录的项目（#89 语义不变）。
    返回 (req_public 或 None, (status, 文案) 或 None)：
      - (None, (404, ...)) 未找到；(req, None) 成功/幂等现状；执行失败 (req, (502, ...)) 保持 pending；
        目标正在执行 approve（并发点批）→ (req, (409, ...))。
    幂等：非 pending 直接返回现状，不再执行（重复回调不重复建 key 的状态门）。
    并发（评审 P2）：approve 锁外执行期间 rid 登记 _executing，reject/重复 approve 得 409、
    sweep 超时让位——new 类执行有真实副作用（key 已建 + 明文已私信），执行中不得被改终态。"""
    with _lock:
        reqs = _load()
        req = next((r for r in reqs if r["id"] == rid), None)
        if req is None:
            return None, (404, "request not found")
        if req["status"] != "pending":
            return shape_public(req), None  # 幂等：重复点批/回调返回现状
        if rid in _executing:
            return shape_public(req), (409, "该申请正在执行通过操作，请稍后刷新查看结果")
        if action == "approve" and tier_override:
            # issue #85：提额语义不含体验档——upgrade 白名单收窄为 TIERS（防审批批成降档，
            # 与执行侧守卫双保险）；new 仍 ALLOWED_TIERS 全集（#81 语义不变）
            allowed = ALLOWED_TIERS if req["kind"] == "new" else TIERS
            if tier_override not in allowed:
                return shape_public(req), (400, f"tier 必须是 {'/'.join(allowed)}")
        if action == "approve" and project_override and req["kind"] != "new":
            # issue #128：提额不涉及项目变更——upgrade 带 project_override 直接 400
            # （廉价校验与 tier 白名单同位：不执行、状态保持 pending，不触发任何慢调用）
            return shape_public(req), (400, "提额申请不支持 project_override（指定正式项目仅新建申请可用）")
        if action == "approve" and time.time() - req.get("ts", 0) > REQUEST_TTL:
            # 超时申请不可批：就地转 expired（sweep 未跑到时的兜底），回执后返回现状
            req["status"] = "expired"
            req["resolvedAt"] = _iso(time.time())
            req["result"] = "超时未审批，自动关闭"
            _save(reqs)
            snap = dict(req)
        else:
            snap = None
            if action == "approve":
                _executing.add(rid)  # 执行期标记，下方 finally 摘除
    if snap is not None:
        _notify_resolved(snap, f"[ai4s] 你的 Key 申请已超时关闭（{snap['id']}），如需请重新发起")
        return shape_public(snap), None
    if action == "reject":
        reason = (reason or "").strip()[:MAX_REASON]
        with _lock:
            reqs = _load()
            req = next((r for r in reqs if r["id"] == rid), None)
            if req is None or req["status"] != "pending":
                return (shape_public(req), None) if req else (None, (404, "request not found"))
            req["status"] = "rejected"
            req["resolvedAt"] = _iso(time.time())
            req["result"] = f"管理员拒绝{('：' + reason) if reason else ''}"
            _save(reqs)
            snap = dict(req)
        _notify_resolved(snap, f"[ai4s] 你的 Key 申请未通过（{snap['id']}）\n{snap['result']}")
        return shape_public(snap), None
    # approve：执行在锁外（gql/飞书慢调用）；rid 已登记 _executing，并发 reject/重复 approve/sweep 让位
    try:
        if project_override:
            # issue #128：override 项目存在性校验——myProjects 是慢调用不能进锁，故放在
            # _executing 登记后的锁外段（与执行同一段并发纪律：校验期间 reject/重复 approve/
            # sweep 让位；本段任何 return 都经 finally 摘除标记）。校验通过落 override 快照
            # （锁内持久化 + 同步内存 req 供 _execute 同据）；不存在 400 / 查询异常 502
            # 都保持 pending 可重试（fail-closed 不猜项目，语义同 tier 白名单 400/执行 502）。
            try:
                projs = _get_ax().gql(alert_poller.MY_PROJECTS_QUERY)["myProjects"] or []
            except Exception as e:
                print(f"[keyreq] override 项目校验查询异常 {rid}: {type(e).__name__}: {e}", flush=True)
                return shape_public(req), (502, f"项目校验暂不可用（状态保持待审批，可重试）: "
                                                f"{type(e).__name__}: {str(e)[:120]}")
            proj = next((p for p in projs if p.get("id") == project_override), None)
            if proj is None:
                return shape_public(req), (400, f"项目不存在: {project_override}")
            with _lock:
                reqs = _load()
                cur = next((r for r in reqs if r["id"] == rid), None)
                if cur is None:
                    return None, (404, "request not found")
                if cur["status"] != "pending":
                    # 防御性让位：_executing 已挡 reject/重复 approve/sweep，正常到不了这里；
                    # 万一到达（未来改动破坏约定），尚未产生任何副作用，只让位并记日志
                    print(f"[keyreq] override 落快照时申请已非 pending {rid}"
                          f"（status={cur['status']}），让位", flush=True)
                    return shape_public(cur), None
                cur["projectOverride"] = proj["id"]
                cur["projectNameOverride"] = proj.get("name")
                _save(reqs)
            req["projectOverride"] = proj["id"]  # 执行用内存快照同步（_execute 以 req 为据）
            req["projectNameOverride"] = proj.get("name")
        try:
            result, key_name, dm_text = _execute(req, tier_override)
        except Exception as e:
            print(f"[keyreq] 申请执行失败 {rid}: {type(e).__name__}: {e}", flush=True)
            return shape_public(req), (502, f"执行失败（状态保持待审批，可重试）: {type(e).__name__}: {str(e)[:120]}")
        with _lock:
            reqs = _load()
            req = next((r for r in reqs if r["id"] == rid), None)
            if req is None:
                return None, (404, "request not found")
            if req["status"] != "pending":
                # 防御性让位：reject/重复 approve/sweep 均已被 _executing 挡住，正常到不了这里；
                # 万一到达（未来改动破坏约定），副作用已真实发生（new 类 key 已建、明文已私信），
                # 只让位不补偿并显式记日志，遗留 key 由管理员在 Key 管理页归档
                print(f"[keyreq] 执行完成但申请已被并发关闭 {rid}（status={req['status']}），副作用不回收", flush=True)
                return shape_public(req), None
            req["status"] = "approved"
            req["resolvedAt"] = _iso(time.time())
            req["result"] = result
            req["keyName"] = key_name
            _save(reqs)
            snap = dict(req)
        _notify_resolved(snap, dm_text or "")
        return shape_public(snap), None
    finally:
        with _lock:
            _executing.discard(rid)


def cancel_request(rid: str, email: str):
    """申请人撤回（issue #80）：仅本人 + 仅 pending。返回 (req_public 或 None, (status, 文案) 或 None)：
      - (None, (404, ...)) 未找到或非本人（同码不区分，不泄露他人申请存在性）；
      - (req, (409, ...)) 目标正在执行 approve（与 resolve_request 同一 _executing 门）；
      - (req, None) 撤回成功/幂等现状（非 pending 直接返回，不重复通知）。
    回执：审批卡更新为已撤回；无卡场景（降级群通知）→ 群文本注明。"""
    with _lock:
        reqs = _load()
        req = next((r for r in reqs if r["id"] == rid
                    and (r.get("applicant") or {}).get("email") == email), None)
        if req is None:
            return None, (404, "request not found")
        if req["status"] != "pending":
            return shape_public(req), None  # 幂等：重复撤回/已被处理都返现状
        if rid in _executing:
            return shape_public(req), (409, "该申请正在执行通过操作，请稍后刷新查看结果")
        req["status"] = "canceled"
        req["resolvedAt"] = _iso(time.time())
        req["result"] = "申请人撤回"
        _save(reqs)
        snap = dict(req)
    _notify_canceled(snap)
    return shape_public(snap), None


def sweep_expired():
    """巡检线程每轮调用：pending 超 TTL → expired + 回执（卡片更新 + 申请人私信）。best-effort。"""
    now = time.time()
    with _lock:
        reqs = _load()
        due = [r for r in reqs if r["status"] == "pending" and now - r.get("ts", now) > REQUEST_TTL
               and r["id"] not in _executing]  # 执行中的 approve 让位：执行秒级完成，下轮再判超时
        if not due:
            return
        for r in due:
            r["status"] = "expired"
            r["resolvedAt"] = _iso(now)
            r["result"] = "超时未审批，自动关闭"
        _save(reqs)
        snaps = [dict(r) for r in due]
    for r in snaps:
        try:
            _notify_resolved(r, f"[ai4s] 你的 Key 申请已超时关闭（{r['id']}），如需请重新发起")
        except Exception as e:
            print(f"[keyreq] 超时回执失败 {r['id']}: {type(e).__name__}: {e}", flush=True)
