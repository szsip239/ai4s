#!/usr/bin/env python3
"""key_requests 控制台 Key 申请通道测试（issue #79）。

seam 纪律与 test_self_api/test_alert_poller 同款：执行体与飞书发送在线路边界 mock
（alert_poller.find_user_by_email/ensure_emp_key/apply_tier_to_user/feishu_dm/send_feishu、
key_requests._get_ax/_feishu_card_send/_feishu_card_update），状态文件用临时目录覆写
模块级 REQUESTS_PATH（admin_api 测试钦定方式），不 mock 模块内部塑形/校验函数。
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

# 让测试可 import shim 目录下的 key_requests（discover 从 shim/tests 启动）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import key_requests as kr

_ME_FEISHU = {"id": "gid://axonhub/User/2", "email": "ou_emp001@casdoor.oidc", "isOwner": False, "scopes": []}
_ME_LOCAL = {"id": "gid://axonhub/User/9", "email": "user@example.com", "isOwner": False, "scopes": []}
_USER_FEISHU = {"id": "gid://axonhub/User/2", "email": "ou_emp001@casdoor.oidc", "status": "activated"}
_USER_LOCAL = {"id": "gid://axonhub/User/9", "email": "user@example.com", "status": "activated"}


class _Base(unittest.TestCase):
    """每用例独立状态文件 + 常用线路 mock（飞书发送/卡片全静默记录）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_path = kr.REQUESTS_PATH
        self._saved_admin = kr.FEISHU_ADMIN_OPEN_ID
        kr.REQUESTS_PATH = os.path.join(self._tmp.name, "key-requests.json")
        kr.FEISHU_ADMIN_OPEN_ID = ""  # 默认无管理员 open_id → 审批通知走群 webhook（mock 记录）
        self.dms = []      # (open_id, text)
        self.group = []    # send_feishu 文本
        self.cards = []    # (open_id, card) 审批卡
        self.card_updates = []  # (message_id, card) 回执更新
        self._patchers = [
            mock.patch.object(kr.alert_poller, "feishu_dm",
                              side_effect=lambda oid, text: self.dms.append((oid, text)) or True),
            mock.patch.object(kr.alert_poller, "send_feishu",
                              side_effect=lambda text: self.group.append(text) or True),
            mock.patch.object(kr, "_feishu_card_send",
                              side_effect=lambda oid, card: self.cards.append((oid, card)) or "mid-1"),
            mock.patch.object(kr, "_feishu_card_update",
                              side_effect=lambda mid, card: self.card_updates.append((mid, card)) or True),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        kr.REQUESTS_PATH = self._saved_path
        kr.FEISHU_ADMIN_OPEN_ID = self._saved_admin
        self._tmp.cleanup()

    def _create(self, me=_ME_FEISHU, kind="new", purpose="项目联调", tier=""):
        req, err = kr.create_request(me, kind, purpose, tier)
        self.assertIsNone(err)
        return req


class TestValidate(_Base):
    def test_bad_kind(self):
        self.assertEqual(kr.validate_payload({"kind": "x"})[3], "kind 必须是 new 或 upgrade")

    def test_new_requires_purpose(self):
        self.assertIn("purpose", kr.validate_payload({"kind": "new"})[3])

    def test_upgrade_tier_whitelist(self):
        self.assertIn("标准档", kr.validate_payload({"kind": "upgrade", "tier": "无敌档"})[3])
        self.assertIsNone(kr.validate_payload({"kind": "upgrade", "tier": "高档"})[3])

    def test_body_not_dict(self):
        self.assertIn("JSON", kr.validate_payload("not-a-dict")[3])


class TestCreate(_Base):
    def test_create_and_list_own_only(self):
        self._create(_ME_FEISHU)
        self._create(_ME_LOCAL)
        own = kr.list_requests(email=_ME_FEISHU["email"])
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["applicant"]["email"], _ME_FEISHU["email"])
        self.assertEqual(len(kr.list_requests()), 2)
        # 员工可见形状不含内部字段（卡片 message_id / ts）
        self.assertNotIn("cardMessageId", own[0])
        self.assertNotIn("ts", own[0])

    def test_feishu_open_id_derived(self):
        req = self._create(_ME_FEISHU)
        self.assertEqual(req["applicant"]["openId"], "ou_emp001")
        req2 = self._create(_ME_LOCAL)
        self.assertIsNone(req2["applicant"]["openId"])

    def test_dup_pending_same_kind_409(self):
        self._create(_ME_FEISHU)
        _, err = kr.create_request(_ME_FEISHU, "new", "再来一个", "")
        self.assertEqual(err[0], 409)
        _, err2 = kr.create_request(_ME_FEISHU, "upgrade", "", "高档")  # 不同 kind 不拦
        self.assertIsNone(err2)

    def test_admin_notify_fallback_group(self):
        # 未配置管理员 open_id → 降级群 webhook 文本（含申请 ID 与控制台链接）
        req = self._create(_ME_FEISHU)
        self.assertEqual(len(self.group), 1)
        self.assertIn(req["id"], self.group[0])
        self.assertEqual(self.cards, [])

    def test_admin_notify_card_and_message_id_saved(self):
        kr.FEISHU_ADMIN_OPEN_ID = "ou_admin"
        with mock.patch.object(kr.alert_poller, "FEISHU_APP_ID", "cli_x"), \
             mock.patch.object(kr.alert_poller, "FEISHU_APP_SECRET", "s"):
            req = self._create(_ME_FEISHU)
        self.assertEqual(self.cards[0][0], "ou_admin")
        # message_id 已回写状态文件（不回读邮件内容，直接看内部存储）
        stored = [r for r in kr._load() if r["id"] == req["id"]][0]
        self.assertEqual(stored["cardMessageId"], "mid-1")
        # 卡片绝不含明文语义：此时还没有 key；按钮是 link（控制台审批页），无回调 value
        card = self.cards[0][1]
        self.assertIn(req["id"], json.dumps(card, ensure_ascii=False))

    def test_card_mid_writeback_race_updates_terminal_card(self):
        # 评审 P2 窄竞态：审批卡发送期间申请已被撤回（当时回执因无 cardMessageId 走降级群文本），
        # 回写 mid 时发现已非 pending 必须补一次终态卡片更新——否则管理员手里留永不更新的待审批卡
        kr.FEISHU_ADMIN_OPEN_ID = "ou_admin"

        def _send_during_cancel(oid, card):
            # 发卡慢：发送期间申请人撤回完成（等价 cancel 锁内段的终态翻转）
            with kr._lock:
                reqs = kr._load()
                r = next(x for x in reqs if x["status"] == "pending")
                r["status"] = "canceled"
                r["resolvedAt"] = "2026-08-22T00:00:00Z"
                r["result"] = "申请人撤回"
                kr._save(reqs)
            return "mid-race"

        with mock.patch.object(kr.alert_poller, "FEISHU_APP_ID", "cli_x"), \
             mock.patch.object(kr.alert_poller, "FEISHU_APP_SECRET", "s"), \
             mock.patch.object(kr, "_feishu_card_send", side_effect=_send_during_cancel):
            req, err = kr.create_request(_ME_FEISHU, "new", "竞态", "")
        self.assertIsNone(err)
        self.assertEqual(len(self.card_updates), 1)  # 回写补偿：终态卡更新已补发
        mid, card = self.card_updates[0]
        self.assertEqual(mid, "mid-race")
        self.assertIn("已撤回", json.dumps(card, ensure_ascii=False))
        stored = [r for r in kr._load() if r["id"] == req["id"]][0]
        self.assertEqual(stored["cardMessageId"], "mid-race")


class TestResolve(_Base):
    def _approve_ready(self, me=_ME_FEISHU, user=_USER_LOCAL, kind="new", tier=""):
        req = self._create(me, kind=kind, tier=tier) if kind == "upgrade" else self._create(me)
        return req

    def test_approve_new_feishu_dm_plaintext(self):
        req = self._create(_ME_FEISHU)
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_FEISHU), \
             mock.patch.object(kr.alert_poller, "ensure_emp_key", return_value=("emp-x", "ah-plain-1234", "")), \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            out, err = kr.resolve_request(req["id"], "approve")
        self.assertIsNone(err)
        self.assertEqual(out["status"], "approved")
        self.assertEqual(out["keyName"], "emp-x")
        self.assertIn("明文已私信申请人", out["result"])
        self.assertNotIn("ah-plain-1234", json.dumps(out, ensure_ascii=False))  # 响应/结果无明文
        # 明文只出现在给申请人的私信里
        self.assertEqual(self.dms[0][0], "ou_emp001")
        self.assertIn("ah-plain-1234", self.dms[0][1])

    def test_approve_new_nonfeishu_plaintext_to_admin_only(self):
        kr.FEISHU_ADMIN_OPEN_ID = "ou_admin"
        req = self._create(_ME_LOCAL)
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_LOCAL), \
             mock.patch.object(kr.alert_poller, "ensure_emp_key", return_value=("emp-y", "ah-plain-9999", "")), \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            out, err = kr.resolve_request(req["id"], "approve")
        self.assertIsNone(err)
        self.assertIn("我的 Key", out["result"])  # issue #81：页面本人可见明文，不再依赖管理员线下领取
        # 非飞书：申请人收不到私信；明文只进管理员私信（备付）
        self.assertEqual(len(self.dms), 1)
        self.assertEqual(self.dms[0][0], "ou_admin")
        self.assertIn("ah-plain-9999", self.dms[0][1])
        self.assertIn("备付", self.dms[0][1])

    def test_approve_new_tier_override(self):
        # issue #81：批准时管理员改定档位——覆盖默认体验档，贯穿 ensure_emp_key 与交付文案
        req = self._create(_ME_FEISHU)
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_FEISHU), \
             mock.patch.object(kr.alert_poller, "ensure_emp_key", return_value=("emp-x", "ah-plain-1234", "")) as ek, \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            out, err = kr.resolve_request(req["id"], "approve", tier_override="标准档")
        self.assertIsNone(err)
        self.assertEqual(ek.call_args.kwargs["tier"], "标准档")
        self.assertIn("标准档", out["result"])
        self.assertIn("标准档", self.dms[0][1])
        self.assertNotIn("提额", self.dms[0][1])  # 非默认档不带提额提示

    def test_approve_new_default_tier_init(self):
        # 缺省（不带 tier）= 体验档默认，行为与 #79 一致
        req = self._create(_ME_FEISHU)
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_FEISHU), \
             mock.patch.object(kr.alert_poller, "ensure_emp_key", return_value=("emp-x", "ah-x", "")) as ek, \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            out, err = kr.resolve_request(req["id"], "approve")
        self.assertIsNone(err)
        self.assertEqual(ek.call_args.kwargs["tier"], kr.alert_poller.KEY_INIT_TIER)
        self.assertIn("提额", self.dms[0][1])  # 默认档带提额引导

    def test_approve_upgrade_tier_override(self):
        # issue #81：提额批准时改定档覆盖所求档
        req = self._create(_ME_FEISHU, kind="upgrade", tier="标准档")
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_FEISHU), \
             mock.patch.object(kr.alert_poller, "apply_tier_to_user", return_value="已将 2 个 Key 换挂 高档") as at, \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            out, err = kr.resolve_request(req["id"], "approve", tier_override="高档")
        self.assertIsNone(err)
        self.assertEqual(at.call_args[0][2], "高档")

    def test_approve_invalid_tier_400(self):
        # 白名单外档位直接 400，不执行、状态保持 pending
        req = self._create(_ME_FEISHU)
        with mock.patch.object(kr.alert_poller, "ensure_emp_key") as ek:
            out, err = kr.resolve_request(req["id"], "approve", tier_override="超档")
        self.assertEqual(err[0], 400)
        self.assertIn("tier", err[1])
        self.assertEqual(out["status"], "pending")
        ek.assert_not_called()

    def test_approve_upgrade_calls_apply_tier(self):
        req = self._create(_ME_FEISHU, kind="upgrade", tier="标准档")
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_FEISHU), \
             mock.patch.object(kr.alert_poller, "apply_tier_to_user", return_value="已将 2 个 Key 换挂 标准档") as at, \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            out, err = kr.resolve_request(req["id"], "approve")
        self.assertIsNone(err)
        self.assertEqual(out["status"], "approved")
        at.assert_called_once()
        self.assertEqual(at.call_args[0][2], "标准档")
        self.assertIn("换挂", self.dms[0][1])  # 飞书申请人收到生效通知

    def test_idempotent_reapprove_no_reexecute(self):
        req = self._create(_ME_FEISHU)
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_FEISHU), \
             mock.patch.object(kr.alert_poller, "ensure_emp_key", return_value=("emp-x", "ah-x", "")) as ek, \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            kr.resolve_request(req["id"], "approve")
            out, err = kr.resolve_request(req["id"], "approve")  # 重复回调/重复点批
        self.assertIsNone(err)
        self.assertEqual(out["status"], "approved")
        self.assertEqual(ek.call_count, 1)  # 状态门：不重复执行

    def test_execute_failure_stays_pending_502(self):
        req = self._create(_ME_FEISHU)
        with mock.patch.object(kr.alert_poller, "find_user_by_email", side_effect=RuntimeError("gql down")), \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            out, err = kr.resolve_request(req["id"], "approve")
        self.assertEqual(err[0], 502)
        self.assertEqual(out["status"], "pending")  # 保持 pending 可重试（#72 失败不标记同语义）

    def test_reject_with_reason(self):
        req = self._create(_ME_FEISHU)
        out, err = kr.resolve_request(req["id"], "reject", "预算不足")
        self.assertIsNone(err)
        self.assertEqual(out["status"], "rejected")
        self.assertIn("预算不足", out["result"])
        self.assertIn("未通过", self.dms[0][1])
        # 拒绝后不可再批（幂等现状）
        out2, _ = kr.resolve_request(req["id"], "approve")
        self.assertEqual(out2["status"], "rejected")

    def test_not_found_404(self):
        out, err = kr.resolve_request("kr-20990101-000000", "approve")
        self.assertIsNone(out)
        self.assertEqual(err[0], 404)

    def test_approve_past_ttl_auto_expires(self):
        req = self._create(_ME_FEISHU)
        with kr._lock:  # 把 createdAt/ts 拨回超时前
            reqs = kr._load()
            reqs[0]["ts"] = time.time() - kr.REQUEST_TTL - 10
            kr._save(reqs)
        with mock.patch.object(kr.alert_poller, "ensure_emp_key") as ek:
            out, err = kr.resolve_request(req["id"], "approve")
        self.assertIsNone(err)
        self.assertEqual(out["status"], "expired")
        ek.assert_not_called()  # 超时申请不执行
        self.assertIn("超时", self.dms[0][1])


class TestResolveConcurrency(_Base):
    """评审 P2：approve 锁外执行期间，_executing 内存标记挡并发 reject/重复 approve/sweep。"""

    def _start_blocked_approve(self, req):
        """起线程执行 approve（find_user_by_email 阻塞在线路边界以模拟慢执行）。
        返回 (thread, release, box)：release.set() 放行，join 后 box["result"]=(out, err)。"""
        entered = threading.Event()
        release = threading.Event()
        box = {}

        def _slow_find(ax, email):
            entered.set()
            release.wait(5)
            return _USER_FEISHU

        def _run():
            with mock.patch.object(kr.alert_poller, "find_user_by_email", side_effect=_slow_find), \
                 mock.patch.object(kr.alert_poller, "ensure_emp_key", return_value=("emp-x", "ah-x", "")), \
                 mock.patch.object(kr, "_get_ax", return_value=object()):
                box["result"] = kr.resolve_request(req["id"], "approve")

        t = threading.Thread(target=_run)
        t.start()
        self.assertTrue(entered.wait(5))  # 确认执行已进入锁外段（_executing 已登记）
        return t, release, box

    def test_reject_while_executing_409(self):
        req = self._create(_ME_FEISHU)
        t, release, box = self._start_blocked_approve(req)
        try:
            out, err = kr.resolve_request(req["id"], "reject", "想撤回")
            self.assertEqual(err[0], 409)
            self.assertEqual(out["status"], "pending")  # 执行中不得被改终态
        finally:
            release.set()
            t.join(5)
        out, err = box["result"]
        self.assertIsNone(err)
        self.assertEqual(out["status"], "approved")  # 放行后执行正常落终态
        stored = [r for r in kr._load() if r["id"] == req["id"]][0]
        self.assertEqual(stored["status"], "approved")

    def test_second_approve_while_executing_409(self):
        req = self._create(_ME_FEISHU)
        t, release, box = self._start_blocked_approve(req)
        try:
            out, err = kr.resolve_request(req["id"], "approve")
            self.assertEqual(err[0], 409)
            self.assertEqual(out["status"], "pending")
        finally:
            release.set()
            t.join(5)
        out, err = box["result"]
        self.assertIsNone(err)
        self.assertEqual(out["status"], "approved")

    def test_sweep_skips_executing(self):
        req = self._create(_ME_FEISHU)
        t, release, box = self._start_blocked_approve(req)
        try:
            with kr._lock:  # 执行期间跨过 TTL 边界
                reqs = kr._load()
                reqs[0]["ts"] = time.time() - kr.REQUEST_TTL - 5
                kr._save(reqs)
            kr.sweep_expired()
            stored = [r for r in kr._load() if r["id"] == req["id"]][0]
            self.assertEqual(stored["status"], "pending")  # 执行中不被超时兜底改终态
            self.assertEqual(self.dms, [])
        finally:
            release.set()
            t.join(5)
        out, err = box["result"]
        self.assertIsNone(err)
        self.assertEqual(out["status"], "approved")  # 执行完成后正常落终态（TTL 不再追溯）

    def test_cancel_while_executing_409(self):
        # issue #80：撤回与 approve 执行同一道 _executing 门——执行中撤回 409，不得半途改终态
        req = self._create(_ME_FEISHU)
        t, release, box = self._start_blocked_approve(req)
        try:
            out, err = kr.cancel_request(req["id"], _ME_FEISHU["email"])
            self.assertEqual(err[0], 409)
            self.assertEqual(out["status"], "pending")
        finally:
            release.set()
            t.join(5)
        out, err = box["result"]
        self.assertIsNone(err)
        self.assertEqual(out["status"], "approved")


class TestCancel(_Base):
    """issue #80：申请人撤回（仅本人 + 仅 pending，幂等，管理员回执）。"""

    def test_cancel_pending_group_fallback_receipt(self):
        # 默认无 FEISHU_ADMIN_OPEN_ID（_Base）→ 创建走群通知、无 cardMessageId → 撤回回执降级群文本
        req = self._create(_ME_FEISHU)
        out, err = kr.cancel_request(req["id"], _ME_FEISHU["email"])
        self.assertIsNone(err)
        self.assertEqual(out["status"], "canceled")
        self.assertEqual(out["result"], "申请人撤回")
        self.assertIsNotNone(out["resolvedAt"])
        # 创建 1 条群通知 + 撤回 1 条群回执
        self.assertEqual(len(self.group), 2)
        self.assertIn("已撤回", self.group[-1])
        self.assertIn(req["id"], self.group[-1])
        self.assertEqual(self.dms, [])  # 申请人本人操作，不再私信申请人

    def test_cancel_with_card_updates_receipt(self):
        kr.FEISHU_ADMIN_OPEN_ID = "ou_admin"
        with mock.patch.object(kr.alert_poller, "FEISHU_APP_ID", "cli_x"), \
             mock.patch.object(kr.alert_poller, "FEISHU_APP_SECRET", "s"):
            req = self._create(_ME_FEISHU)
        out, err = kr.cancel_request(req["id"], _ME_FEISHU["email"])
        self.assertIsNone(err)
        self.assertEqual(out["status"], "canceled")
        self.assertEqual(len(self.card_updates), 1)  # 有卡场景：审批卡更新为已撤回
        mid, card = self.card_updates[0]
        self.assertEqual(mid, "mid-1")
        self.assertIn("已撤回", json.dumps(card, ensure_ascii=False))
        self.assertEqual(len(self.group), 0)  # 有卡不再降级群文本

    def test_cancel_other_user_404(self):
        req = self._create(_ME_LOCAL)  # 他人申请
        out, err = kr.cancel_request(req["id"], _ME_FEISHU["email"])
        self.assertIsNone(out)
        self.assertEqual(err[0], 404)  # 与未找到同码：不泄露他人申请存在性
        stored = [r for r in kr._load() if r["id"] == req["id"]][0]
        self.assertEqual(stored["status"], "pending")  # 他人申请不受影响

    def test_cancel_not_found_404(self):
        out, err = kr.cancel_request("kr-20990101-000000", _ME_FEISHU["email"])
        self.assertIsNone(out)
        self.assertEqual(err[0], 404)

    def test_cancel_non_pending_returns_current(self):
        req = self._create(_ME_FEISHU)
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_FEISHU), \
             mock.patch.object(kr.alert_poller, "ensure_emp_key", return_value=("emp-x", "ah-x", "")), \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            kr.resolve_request(req["id"], "approve")
        dms_before = len(self.dms)
        out, err = kr.cancel_request(req["id"], _ME_FEISHU["email"])  # 已 approved 不可撤
        self.assertIsNone(err)  # 幂等现状，不是错误
        self.assertEqual(out["status"], "approved")
        self.assertEqual(len(self.dms), dms_before)  # 不重复通知
        self.assertEqual(len(self.group), 1)  # 仅创建时那一条

    def test_cancel_twice_idempotent(self):
        req = self._create(_ME_FEISHU)
        kr.cancel_request(req["id"], _ME_FEISHU["email"])
        out, err = kr.cancel_request(req["id"], _ME_FEISHU["email"])  # 重复撤回
        self.assertIsNone(err)
        self.assertEqual(out["status"], "canceled")
        self.assertEqual(len(self.group), 2)  # 回执不重复（创建 1 + 撤回 1）
        # 撤回后管理员点批被状态门拒（审批页不可再操作的 shim 侧保证）
        out2, _ = kr.resolve_request(req["id"], "approve")
        self.assertEqual(out2["status"], "canceled")


class TestCardUpdateShape(unittest.TestCase):
    """评审 P2 锁死（issue #80）：更新卡片消息必须 PATCH 且 body 仅含 content——
    PUT + msg_type=interactive 恒 400（230001 invalid msg_type）静默失败，#79 回执曾因此从未送达。
    独立 TestCase 不继承 _Base——_Base 把 _feishu_card_update 本身 mock 掉了，形状测试要调真实函数。"""

    def test_update_uses_patch_and_content_only(self):
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["method"] = req.get_method()
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data)
            return io.BytesIO(b'{"code": 0, "data": {}}')

        with mock.patch.object(kr.alert_poller, "feishu_tenant_token", return_value="tt-x"), \
             mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            ok = kr._feishu_card_update("mid-1", {"header": {"template": "grey"}})
        self.assertTrue(ok)
        self.assertEqual(captured["method"], "PATCH")
        self.assertEqual(set(captured["body"]), {"content"})  # 仅 content，无 msg_type
        self.assertEqual(json.loads(captured["body"]["content"]), {"header": {"template": "grey"}})
        self.assertIn("/im/v1/messages/mid-1", captured["url"])


class TestSweepExpired(_Base):
    def test_sweep_marks_and_notifies(self):
        req = self._create(_ME_FEISHU)
        fresh = self._create(_ME_LOCAL)
        with kr._lock:
            reqs = kr._load()
            for r in reqs:
                if r["id"] == req["id"]:
                    r["ts"] = time.time() - kr.REQUEST_TTL - 5
            kr._save(reqs)
        kr.sweep_expired()
        states = {r["id"]: r["status"] for r in kr.list_requests()}
        self.assertEqual(states[req["id"]], "expired")
        self.assertEqual(states[fresh["id"]], "pending")
        self.assertEqual(len(self.dms), 1)  # 只给超时申请人发私信
        self.assertIn("超时", self.dms[0][1])
        kr.sweep_expired()  # 幂等：不再重复通知
        self.assertEqual(len(self.dms), 1)


if __name__ == "__main__":
    unittest.main()
