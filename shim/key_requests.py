#!/usr/bin/env python3
"""控制台 Key 申请 → 管理员审批 → 自动执行（issue #79，第二通道；飞书审批定义通道并存）。

流程：员工在「我的 Key」页提交（POST /self/key-requests，caller Bearer 内省拿 me）→
本模块落待办申请（状态文件原子写）→ app bot 私信管理员审批卡（link 按钮直达控制台审批页）→
管理员在控制台点批（admin 平面 /dlp-admin/key-requests/*）→ 复用 #72/#19 执行体
（alert_poller.ensure_emp_key / apply_tier_to_user）→ 回执（卡片更新 + 申请人私信/页面状态）。

卡片回调选型（调研结论，issue 已记录取舍）：飞书卡片按钮原生回调（card.action.trigger
长连接）需要 (a) 开放平台后台逐 App 配置回调订阅——不在代码库/IaC 内，本环境无法验证，
配错即按钮静默无响应；(b) shim 引入 lark-oapi + 常驻 WS 重连——与镜像 stdlib 优先 +
pin + 懒加载纪律相悖；(c) 卡片必须 app bot 发送（webhook 自定义机器人卡片不支持回调）。
故选最简可靠替代：卡片仅通知（link 按钮）+ 管理员控制台点批——全部走已实证通道，
本环境端到端可测，零新依赖零后台配置。

交付分流：申请人 email 以 @casdoor.oidc 结尾（飞书身份）→ 明文私信申请人；
否则（本地/钉钉/企微账号）→ 明文只私信管理员，申请人侧页面显示「已通过，请联系管理员领取」。

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
import urllib.request

import admin_api  # write_json_atomic 原子写复用（唯一 tmp + .bak 滚动 + finally 清理）
import alert_poller  # 执行体/飞书 helper 复用（import 不起线程；feishu_dm/ensure_emp_key 等）

REQUESTS_PATH = os.environ.get(
    "KEY_REQUESTS_PATH",
    os.path.join(os.path.dirname(alert_poller.STATE_PATH) or ".", "key-requests.json"),
)
# 管理员审批卡接收人（app bot 私信）；未配置降级为群 webhook 文本通知
FEISHU_ADMIN_OPEN_ID = os.environ.get("FEISHU_ADMIN_OPEN_ID", "")
# 审批卡 link 按钮落点（控制台审批页）；env 置空/未设都回退默认（compose 空串注入场景）
CONSOLE_URL = os.environ.get("KEY_REQUEST_CONSOLE_URL") or "http://localhost:3000/key-requests"
if not os.environ.get("KEY_REQUEST_CONSOLE_URL"):
    # 评审 P2：未配置时卡片 link 落点是 localhost 默认值，管理员点按钮打到的是自己本机——
    # 模块加载（懒 import 于首次请求/巡检）时显式 warning，提示运维配置
    print(f"[keyreq] KEY_REQUEST_CONSOLE_URL 未配置，审批卡 link 落点回退默认 {CONSOLE_URL}"
          "（非本机管理员不可达，请在 deploy/.env 配置控制台地址）", flush=True)
# 待办申请超时：超时置 expired + 回执（巡检线程 sweep_expired 每轮检查）
REQUEST_TTL = alert_poller._env_int("KEY_REQUEST_TTL", 72 * 3600)
# 提额目标档白名单（与 alert_poller.parse_tier 可识别的两档一致）
TIERS = ("标准档", "高档")
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


def list_requests(email: str = "") -> list:
    """申请列表（新到旧）。email 非空=员工侧本人过滤。"""
    with _lock:
        reqs = _load()
    if email:
        reqs = [r for r in reqs if (r.get("applicant") or {}).get("email") == email]
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
    """更新已发出的卡片（回执：状态+结果，无明文）。"""
    try:
        body = {"msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)}
        req = urllib.request.Request(
            f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Authorization": f"Bearer {alert_poller.feishu_tenant_token()}",
                     "Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        if d.get("code") == 0:
            return True
        print(f"[keyreq] 卡片更新非零: code={d.get('code')}", flush=True)
    except Exception as e:
        print(f"[keyreq] 卡片更新失败: {type(e).__name__}: {e}", flush=True)
    return False


_STATUS_LABEL = {"pending": "待审批", "approved": "已通过", "rejected": "已拒绝", "expired": "已超时"}
_STATUS_COLOR = {"pending": "orange", "approved": "green", "rejected": "red", "expired": "grey"}


def _detail_line(req: dict) -> str:
    if req["kind"] == "new":
        return f"**用途**: {req.get('purpose') or '—'}"
    return f"**目标档**: {req.get('tier') or '—'}（作用于其全部 enabled Key）"


def _request_card(req: dict) -> dict:
    """待审批卡：摘要 + 「前往控制台审批」link 按钮（不含任何明文）。"""
    kind_label = "新建 Key" if req["kind"] == "new" else "额度提额"
    content = (
        f"**申请人**: {(req.get('applicant') or {}).get('email')}\n"
        f"{_detail_line(req)}\n"
        f"**申请 ID**: {req['id']}\n**时间**: {req.get('createdAt')}"
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
                "url": CONSOLE_URL,
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
            return
        print(f"[keyreq] 审批卡未送达，降级群通知: {req['id']}", flush=True)
    kind_label = "新建 Key" if req["kind"] == "new" else "额度提额"
    alert_poller.send_feishu(
        f"[ai4s Key 申请] 待审批（{kind_label}）\n"
        f"申请人: {(req.get('applicant') or {}).get('email')}\n"
        f"{_detail_line(req).replace('**', '')}\n"
        f"申请 ID: {req['id']}\n请到控制台 Key 审批页处理: {CONSOLE_URL}"
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


# ---- 生命周期 ----


def validate_payload(payload) -> tuple:
    """POST body 校验：返回 (kind, purpose, tier, err)。err 非 None 即 400 文案。"""
    if not isinstance(payload, dict):
        return None, None, None, "body 必须是 JSON 对象"
    kind = payload.get("kind")
    if kind not in ("new", "upgrade"):
        return None, None, None, "kind 必须是 new 或 upgrade"
    purpose = (payload.get("purpose") or "").strip()
    tier = (payload.get("tier") or "").strip()
    if kind == "new":
        if not purpose:
            return None, None, None, "新建申请必须填用途 purpose"
        if len(purpose) > MAX_PURPOSE:
            return None, None, None, f"purpose 超长（>{MAX_PURPOSE} 字符）"
    else:
        if tier not in TIERS:
            return None, None, None, f"tier 必须是 {'/'.join(TIERS)}"
    return kind, purpose, tier, None


def create_request(me: dict, kind: str, purpose: str, tier: str):
    """落一条待办申请 + 通知管理员。返回 (req_public, err)；err 形如 (status, 文案)。
    反 spam：同申请人同 kind 已有 pending → 409。"""
    email = me.get("email") or ""
    if not email:
        return None, (502, "caller 身份无 email，无法登记申请")
    now = time.time()
    with _lock:
        reqs = _load()
        dup = next((r for r in reqs
                    if r["status"] == "pending" and r["kind"] == kind
                    and (r.get("applicant") or {}).get("email") == email), None)
        if dup:
            return None, (409, f"已有待审批的同类申请（{dup['id']}），请等待处理或联系管理员")
        req = {
            "id": f"kr-{time.strftime('%Y%m%d', time.gmtime(now))}-{secrets.token_hex(3)}",
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
            "cardMessageId": None,
        }
        reqs.append(req)
        _save(reqs)
    _notify_admin_new_request(req)  # 锁外慢调用；卡片 message_id 回写内部自拿锁
    return shape_public(req), None


def _deliver_new_key(req: dict, name: str, plain: str, owner_note: str):
    """明文交付分流。返回 (result 摘要[无明文], applicant_dm_text 或 None——None 表示交付步骤已自行私信）。"""
    open_id = (req.get("applicant") or {}).get("openId")
    email = (req.get("applicant") or {}).get("email")
    if open_id:
        dm_ok = alert_poller.feishu_dm(open_id, (
            f"[ai4s] 你的 API Key 已创建（控制台申请 {req['id']}）\n"
            f"Key 名称: {name}\n档位: {alert_poller.KEY_INIT_TIER}（要更高档请在「我的 Key」页发起申请提额）\n"
            f"明文（仅此一条消息，请立即复制保存）:\n{plain}\n"
            "保管提醒: 明文只出现这一次，勿转发勿提交到代码仓库；丢失不补办，重新发起申请即可。"
        ))
        if dm_ok:
            return f"已建 Key {name}（{alert_poller.KEY_INIT_TIER}）{owner_note}，明文已私信申请人", None
        return (f"已建 Key {name}（{alert_poller.KEY_INIT_TIER}）{owner_note}；私信未送达，"
                f"Key 尾号 …{plain[-4:]}，请联系管理员领取"), None
    # 非飞书账号（本地/钉钉/企微）：明文只给管理员，申请人页面提示联系管理员领取
    admin_note = ""
    if FEISHU_ADMIN_OPEN_ID:
        dm_ok = alert_poller.feishu_dm(FEISHU_ADMIN_OPEN_ID, (
            f"[ai4s] 控制台 Key 申请已批准（{req['id']}）\n"
            f"申请人: {email}（非飞书账号，无法私信交付）\n"
            f"Key 名称: {name}\n档位: {alert_poller.KEY_INIT_TIER}\n"
            f"明文（请线下交付申请人，勿群发勿入库）:\n{plain}"
        ))
        admin_note = "，明文已私信管理员" if dm_ok else "，管理员私信未送达"
    return (f"已建 Key {name}（{alert_poller.KEY_INIT_TIER}）{owner_note}；申请人为非飞书账号"
            f"{admin_note}——已通过，请联系管理员领取"), None


def _execute(req: dict):
    """approve 执行体（复用 #72/#19 primitives）。返回 (result 摘要, key_name 或 None, applicant_dm_text)。
    异常上抛——调用方保持 pending 供重试。"""
    email = (req.get("applicant") or {}).get("email") or ""
    user = alert_poller.find_user_by_email(_get_ax(), email)
    if not user:
        return f"axonhub 中无 {email} 用户（已删除？），未执行", None, None
    if req["kind"] == "new":
        # seed：飞书身份用 open_id（与 #72 命名一致），非飞书用 u<uid>（同名幂等不受影响——tail 是申请 id）
        seed = (req.get("applicant") or {}).get("openId") or f"u{str(user['id']).rsplit('/', 1)[-1]}"
        day = time.strftime("%Y%m%d", time.gmtime(req.get("ts") or time.time()))
        name, plain, owner_note = alert_poller.ensure_emp_key(
            _get_ax(), user, seed, req.get("purpose") or "", day, req["id"])
        result, dm_text = _deliver_new_key(req, name, plain, owner_note)
        return result, name, dm_text
    result = alert_poller.apply_tier_to_user(_get_ax(), user, req.get("tier") or "")
    dm = f"[ai4s] 你的提额申请已通过（{req['id']}）\n{result}"
    return result, None, dm


def resolve_request(rid: str, action: str, reason: str = ""):
    """管理员点批：approve=执行 + 回执；reject=标记 + 回执。
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
        try:
            result, key_name, dm_text = _execute(req)
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
