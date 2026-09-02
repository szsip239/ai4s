#!/usr/bin/env python3
"""key_requests 控制台 Key 申请通道测试（issue #79）。

seam 纪律与 test_self_api/test_alert_poller 同款：执行体与飞书发送在线路边界 mock
（alert_poller.find_user_by_email/ensure_emp_key/apply_tier_to_user/feishu_dm/send_feishu、
key_requests._get_ax/_feishu_card_send/_feishu_card_update），状态文件用临时目录覆写
模块级 REQUESTS_PATH（admin_api 测试钦定方式），不 mock 模块内部塑形/校验函数。
issue #85：create_request 的 upgrade 方向守卫会查当前档（query_user_enabled_keys），
_Base 默认 mock 成体验档 key（任何提额目标放行）；具体档位场景在 TestUpgradeDirectionGuard 内自行重挂。
issue #89 增补：多项目隔离——TestProjectScope 覆盖成员校验（403/502）、项目快照、守卫/执行
项目过滤、存量申请 Default 回退、list_requests 项目过滤与详情行项目显示。
issue #90 增补：dup 判定项目维度——同项目同 kind 409（文案含项目名）、跨项目放行、
存量无字段申请参与 Default 维度去重。
issue #91 增补（P2-1）：审批卡按钮/降级群文本 URL 带 ?project= 参数（存量申请 Default）。
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
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
            # issue #85：upgrade 方向守卫的当前档查询默认返回体验档 key（提额任何目标放行）；
            # 需要特定档位的用例自行重挂本 mock。issue #89：第三参 project_id（项目过滤入查询）
            mock.patch.object(kr.alert_poller, "query_user_enabled_keys",
                              side_effect=lambda ax, uid, project_id=None: [
                                  {"id": "gid://axonhub/APIKey/101", "name": "emp-k", "userID": uid,
                                   "profiles": {"activeProfile": "体验档"}}]),
            # issue #89：项目成员校验默认全员 Default 成员；非成员/查询异常用例自行重挂
            mock.patch.object(kr.alert_poller, "query_user_projects",
                              side_effect=lambda ax, uid: [
                                  {"id": kr.alert_poller.KEY_PROJECT_ID, "name": "Default"}]),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        kr.REQUESTS_PATH = self._saved_path
        kr.FEISHU_ADMIN_OPEN_ID = self._saved_admin
        self._tmp.cleanup()

    def _create(self, me=_ME_FEISHU, kind="new", purpose="项目联调", tier="", key_ids=None):
        if kind == "upgrade" and key_ids is None:
            key_ids = ["gid://axonhub/APIKey/101"]  # _Base 默认 mock 的体验档 key（issue #86 必填）
        req, err = kr.create_request(me, kind, purpose, tier, key_ids=key_ids)
        self.assertIsNone(err)
        return req


class TestValidate(_Base):
    def test_bad_kind(self):
        self.assertEqual(kr.validate_payload({"kind": "x"})[4], "kind 必须是 new 或 upgrade")

    def test_new_requires_purpose(self):
        self.assertIn("purpose", kr.validate_payload({"kind": "new"})[4])

    def test_upgrade_tier_whitelist(self):
        self.assertIn("标准档", kr.validate_payload({"kind": "upgrade", "tier": "无敌档"})[4])
        self.assertIsNone(kr.validate_payload({"kind": "upgrade", "tier": "高档", "keyIds": ["k1"]})[4])

    def test_body_not_dict(self):
        self.assertIn("JSON", kr.validate_payload("not-a-dict")[4])

    def test_upgrade_requires_key_ids(self):
        # issue #86：提额必须带非空 keyIds 列表；合法输入去重保序
        self.assertIn("keyIds", kr.validate_payload({"kind": "upgrade", "tier": "高档"})[4])
        self.assertIn("keyIds", kr.validate_payload({"kind": "upgrade", "tier": "高档", "keyIds": []})[4])
        self.assertIn("keyIds", kr.validate_payload({"kind": "upgrade", "tier": "高档", "keyIds": "k1"})[4])
        self.assertIn("keyIds", kr.validate_payload({"kind": "upgrade", "tier": "高档", "keyIds": ["", "  "]})[4])
        kind, _, tier, key_ids, err = kr.validate_payload(
            {"kind": "upgrade", "tier": "高档", "keyIds": ["k1", "k1", " k2 "]})
        self.assertIsNone(err)
        self.assertEqual((kind, tier, key_ids), ("upgrade", "高档", ["k1", "k2"]))


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
        _, err2 = kr.create_request(_ME_FEISHU, "upgrade", "", "高档",
                                    key_ids=["gid://axonhub/APIKey/101"])  # 不同 kind 不拦
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


class TestUpgradeDirectionGuard(_Base):
    """issue #85 申请侧方向守卫（fail-closed 三拒 + 查询异常 fail-open）；
    issue #86 起按所选 Key 子集评估（keyIds 随 create_request 传入，归属校验同查询完成）。
    _Base 默认当前档=体验档；本类各用例按场景重挂 query_user_enabled_keys。"""

    def _guard_keys(self, *tiers):
        return [{"id": f"gid://axonhub/APIKey/1{i}", "name": f"k{i}", "userID": _ME_LOCAL["id"],
                 "profiles": {"activeProfile": t}} for i, t in enumerate(tiers)]

    def _ids(self, n):
        return [f"gid://axonhub/APIKey/1{i}" for i in range(n)]

    def test_top_tier_rejected_400(self):
        # 所选全部已是最高档 → 单独文案
        with mock.patch.object(kr.alert_poller, "query_user_enabled_keys",
                               side_effect=lambda ax, uid, project_id=None: self._guard_keys("高档")):
            req, err = kr.create_request(_ME_LOCAL, "upgrade", "", "高档", key_ids=self._ids(1))
        self.assertIsNone(req)
        self.assertEqual(err[0], 400)
        self.assertIn("已是最高档", err[1])

    def test_downgrade_and_sideways_rejected_400(self):
        # 所选高档申标准（实际降档）→ 最高档文案优先；所选标准申标准（平档空转）→ 方向文案
        with mock.patch.object(kr.alert_poller, "query_user_enabled_keys",
                               side_effect=lambda ax, uid, project_id=None: self._guard_keys("高档")):
            _, err = kr.create_request(_ME_LOCAL, "upgrade", "", "标准档", key_ids=self._ids(1))
        self.assertEqual(err[0], 400)
        self.assertIn("已是最高档", err[1])
        with mock.patch.object(kr.alert_poller, "query_user_enabled_keys",
                               side_effect=lambda ax, uid, project_id=None: self._guard_keys("标准档")):
            _, err2 = kr.create_request(_ME_LOCAL, "upgrade", "", "标准档", key_ids=self._ids(1))
        self.assertEqual(err2[0], 400)
        self.assertIn("均已不低于", err2[1])

    def test_no_enabled_key_rejected_400(self):
        with mock.patch.object(kr.alert_poller, "query_user_enabled_keys",
                               side_effect=lambda ax, uid, project_id=None: []):
            req, err = kr.create_request(_ME_LOCAL, "upgrade", "", "标准档", key_ids=self._ids(1))
        self.assertIsNone(req)
        self.assertEqual(err[0], 400)
        self.assertIn("新建", err[1])  # 引导先申请新建

    def test_trial_can_upgrade_pass(self):
        # 体验档申标准/高档 → 放行（_Base 默认 mock 即体验档，显式重挂以自文档化）
        with mock.patch.object(kr.alert_poller, "query_user_enabled_keys",
                               side_effect=lambda ax, uid, project_id=None: self._guard_keys("体验档")):
            req, err = kr.create_request(_ME_LOCAL, "upgrade", "", "标准档", key_ids=self._ids(1))
            self.assertIsNone(err)
            self.assertEqual(req["status"], "pending")
            kr.cancel_request(req["id"], _ME_LOCAL["email"])  # 清掉 pending 再申高档
            req2, err2 = kr.create_request(_ME_LOCAL, "upgrade", "", "高档", key_ids=self._ids(1))
        self.assertIsNone(err2)

    def test_query_failure_fail_open(self):
        # 当前档查询异常（axonhub 不可达）→ 放行 + 记日志（执行侧守卫兜底）；无名称快照
        with mock.patch.object(kr.alert_poller, "query_user_enabled_keys",
                               side_effect=RuntimeError("gql down")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                req, err = kr.create_request(_ME_LOCAL, "upgrade", "", "高档", key_ids=self._ids(1))
        self.assertIsNone(err)
        self.assertEqual(req["status"], "pending")
        self.assertIn("fail-open", buf.getvalue())
        self.assertIsNone(req["keyNames"])  # 显示侧回退

    def test_unprofiled_key_passes(self):
        # enabled 但未挂档（activeProfile 空）→ 秩次无从比较，放行（执行侧守卫兜底）
        with mock.patch.object(kr.alert_poller, "query_user_enabled_keys",
                               side_effect=lambda ax, uid, project_id=None: self._guard_keys(None)):
            req, err = kr.create_request(_ME_LOCAL, "upgrade", "", "标准档", key_ids=self._ids(1))
        self.assertIsNone(err)
        self.assertEqual(req["status"], "pending")


class TestUpgradeKeySelection(_Base):
    """issue #86 按 Key 勾选：归属/启用校验（一次查询完成）、子集方向守卫、名称快照、
    详情行显示、执行子集化、存量无字段申请回退全量语义。"""

    def _guard_keys(self, *tiers):
        return [{"id": f"gid://axonhub/APIKey/2{i}", "name": f"sel-k{i}", "userID": _ME_LOCAL["id"],
                 "profiles": {"activeProfile": t}} for i, t in enumerate(tiers)]

    def _ids(self, n):
        return [f"gid://axonhub/APIKey/2{i}" for i in range(n)]

    def test_foreign_or_unknown_key_400(self):
        # keyIds 混入他人/不存在/未启用 id（不在本人 enabled 集合）→ 400，不区分以免泄露
        with mock.patch.object(kr.alert_poller, "query_user_enabled_keys",
                               side_effect=lambda ax, uid, project_id=None: self._guard_keys("体验档")):
            req, err = kr.create_request(_ME_LOCAL, "upgrade", "", "标准档",
                                         key_ids=self._ids(1) + ["gid://axonhub/APIKey/999"])
        self.assertIsNone(req)
        self.assertEqual(err[0], 400)
        self.assertIn("不存在或未启用", err[1])

    def test_all_selected_at_or_above_400(self):
        # 所选（标准+高档）全部已 ≥ 目标标准档 → 拒收
        with mock.patch.object(kr.alert_poller, "query_user_enabled_keys",
                               side_effect=lambda ax, uid, project_id=None: self._guard_keys("标准档", "高档")):
            _, err = kr.create_request(_ME_LOCAL, "upgrade", "", "标准档", key_ids=self._ids(2))
        self.assertEqual(err[0], 400)

    def test_partial_below_passes_with_name_snapshot(self):
        # 所选（高档+体验）部分低于目标标准 → 放行；keyIds/keyNames 快照按选择顺序存进 req
        with mock.patch.object(kr.alert_poller, "query_user_enabled_keys",
                               side_effect=lambda ax, uid, project_id=None: self._guard_keys("高档", "体验档")):
            req, err = kr.create_request(_ME_LOCAL, "upgrade", "", "标准档", key_ids=self._ids(2))
        self.assertIsNone(err)
        self.assertEqual(req["keyIds"], self._ids(2))
        self.assertEqual(req["keyNames"], ["sel-k0", "sel-k1"])
        # 详情行（审批卡/回执共用）显示目标 Key 名称 + 目标档
        line = kr._detail_line(req)
        self.assertIn("标准档", line)
        self.assertIn("sel-k0", line)
        self.assertIn("sel-k1", line)

    def test_detail_line_legacy_fallback(self):
        # 存量无 keyNames 字段的申请 → 回退「全部 enabled Key」文案
        line = kr._detail_line({"kind": "upgrade", "tier": "高档"})
        self.assertIn("全部 enabled Key", line)

    def test_detail_line_fail_open_falls_back_to_ids(self):
        # 评审 P1-2：fail-open 路径 keyNames=None 但 keyIds 有值 → 回退列 id 并标注快照缺失，
        # 不得错误宣称「全部 enabled Key」（对子集申请是错误事实）
        line = kr._detail_line({"kind": "upgrade", "tier": "高档",
                                "keyIds": ["gid://axonhub/APIKey/20", "gid://axonhub/APIKey/21"],
                                "keyNames": None})
        self.assertIn("gid://axonhub/APIKey/20", line)
        self.assertIn("gid://axonhub/APIKey/21", line)
        self.assertIn("名称快照缺失", line)
        self.assertNotIn("全部 enabled Key", line)

    def test_execute_subset_passed_to_apply(self):
        req = self._create(_ME_LOCAL, kind="upgrade", tier="高档", key_ids=["gid://axonhub/APIKey/101"])
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_LOCAL), \
             mock.patch.object(kr.alert_poller, "apply_tier_to_user", return_value="ok") as at, \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            out, err = kr.resolve_request(req["id"], "approve")
        self.assertIsNone(err)
        self.assertEqual(at.call_args.kwargs["key_ids"], ["gid://axonhub/APIKey/101"])

    def test_legacy_request_without_keyids_falls_back_to_all(self):
        # 存量申请（#86 前创建，无 keyIds/keyNames 字段）→ 执行回退「全部 enabled Key」（key_ids=None）
        req = self._create(_ME_LOCAL, kind="upgrade", tier="高档")
        with kr._lock:
            reqs = kr._load()
            stored = next(r for r in reqs if r["id"] == req["id"])
            del stored["keyIds"], stored["keyNames"]  # 模拟存量记录形状
            kr._save(reqs)
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_LOCAL), \
             mock.patch.object(kr.alert_poller, "apply_tier_to_user", return_value="ok") as at, \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            out, err = kr.resolve_request(req["id"], "approve")
        self.assertIsNone(err)
        self.assertIsNone(at.call_args.kwargs["key_ids"])


class TestResolveTierNarrowing(_Base):
    """issue #85 审批侧收窄：kind=upgrade 的 tier_override 白名单=TIERS（标准/高档），
    体验档 400；kind=new 仍 ALLOWED_TIERS 全集（#81 语义不变）。"""

    def test_upgrade_override_trial_400(self):
        req = self._create(_ME_LOCAL, kind="upgrade", tier="标准档")
        with mock.patch.object(kr.alert_poller, "apply_tier_to_user") as at:
            out, err = kr.resolve_request(req["id"], "approve", tier_override="体验档")
        self.assertEqual(err[0], 400)
        self.assertIn("标准档", err[1])  # 文案列出收窄后的白名单
        self.assertEqual(out["status"], "pending")  # 不执行、保持 pending
        at.assert_not_called()

    def test_upgrade_override_standard_premium_ok(self):
        for target in ("标准档", "高档"):
            req = self._create(_ME_LOCAL, kind="upgrade", tier="标准档")
            with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_LOCAL), \
                 mock.patch.object(kr.alert_poller, "apply_tier_to_user", return_value="ok") as at, \
                 mock.patch.object(kr, "_get_ax", return_value=object()):
                out, err = kr.resolve_request(req["id"], "approve", tier_override=target)
            self.assertIsNone(err, target)
            self.assertEqual(at.call_args[0][2], target)

    def test_new_override_full_set_ok(self):
        # 新建三档全集仍可用（含体验档）
        for target in ("体验档", "标准档", "高档"):
            req = self._create(_ME_LOCAL)
            with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_LOCAL), \
                 mock.patch.object(kr.alert_poller, "ensure_emp_key", return_value=("emp-x", "ah-x", "")), \
                 mock.patch.object(kr, "_get_ax", return_value=object()):
                _, err = kr.resolve_request(req["id"], "approve", tier_override=target)
            self.assertIsNone(err, target)


class TestReapplyAfterTerminal(_Base):
    """issue #85 边界：dup 只挡 pending——canceled/rejected/expired/approved 后同 kind 可再申请。"""

    def test_reapply_after_each_terminal_status(self):
        # canceled：撤回后再申
        req = self._create(_ME_FEISHU)
        kr.cancel_request(req["id"], _ME_FEISHU["email"])
        _, err = kr.create_request(_ME_FEISHU, "new", "再来", "")
        self.assertIsNone(err)
        # rejected：拒绝后再申
        req2 = next(r for r in kr.list_requests(_ME_FEISHU["email"]) if r["status"] == "pending")
        kr.resolve_request(req2["id"], "reject", "预算")
        _, err = kr.create_request(_ME_FEISHU, "new", "三来", "")
        self.assertIsNone(err)
        # expired：超时后再申
        req3 = next(r for r in kr.list_requests(_ME_FEISHU["email"]) if r["status"] == "pending")
        with kr._lock:
            reqs = kr._load()
            for r in reqs:
                if r["id"] == req3["id"]:
                    r["ts"] = time.time() - kr.REQUEST_TTL - 5
            kr._save(reqs)
        kr.sweep_expired()
        _, err = kr.create_request(_ME_FEISHU, "new", "四来", "")
        self.assertIsNone(err)
        # approved：批完再申
        req4 = next(r for r in kr.list_requests(_ME_FEISHU["email"]) if r["status"] == "pending")
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_FEISHU), \
             mock.patch.object(kr.alert_poller, "ensure_emp_key", return_value=("emp-x", "ah-x", "")), \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            kr.resolve_request(req4["id"], "approve")
        _, err = kr.create_request(_ME_FEISHU, "new", "五来", "")
        self.assertIsNone(err)


_P2 = "gid://axonhub/Project/2"


class TestProjectScope(_Base):
    """issue #89 多项目隔离：create 成员校验 fail-closed（非成员 403/查询异常 502）、req 存
    projectId+projectName 快照、upgrade 守卫按项目过滤、执行落申请单项目（与管理员当前项目
    解耦）、执行时复查成员（被移出不建 Key 但照常 approved）、存量无字段申请视为 Default、
    list_requests 项目过滤、详情行项目显示。"""

    def _member_of_p2(self):
        """重挂成员 mock：caller 同时是 Default 与 P2 成员。"""
        return mock.patch.object(kr.alert_poller, "query_user_projects",
                                 side_effect=lambda ax, uid: [
                                     {"id": kr.alert_poller.KEY_PROJECT_ID, "name": "Default"},
                                     {"id": _P2, "name": "P-Test2"}])

    def _create_p2(self, me=_ME_FEISHU, kind="new", tier="", key_ids=None):
        with self._member_of_p2():
            req, err = kr.create_request(me, kind, "项目联调", tier, key_ids=key_ids, project_id=_P2)
        self.assertIsNone(err)
        return req

    def test_create_non_member_403(self):
        # _Base 默认 mock 只有 Default 成员资格 → 对 P2 发申请 403（成员关系是安全闸门，fail-closed）
        req, err = kr.create_request(_ME_FEISHU, "new", "联调", "", project_id=_P2)
        self.assertIsNone(req)
        self.assertEqual(err[0], 403)
        self.assertIn("不是该项目成员", err[1])

    def test_create_membership_query_error_502(self):
        with mock.patch.object(kr.alert_poller, "query_user_projects",
                               side_effect=RuntimeError("gql down")):
            req, err = kr.create_request(_ME_FEISHU, "new", "联调", "", project_id=_P2)
        self.assertIsNone(req)
        self.assertEqual(err[0], 502)
        self.assertIn("成员校验", err[1])

    def test_create_no_uid_502(self):
        req, err = kr.create_request({"email": "nouid@x"}, "new", "联调", "", project_id=_P2)
        self.assertIsNone(req)
        self.assertEqual(err[0], 502)

    def test_create_stores_project_snapshot(self):
        req = self._create_p2()
        self.assertEqual(req["projectId"], _P2)
        self.assertEqual(req["projectName"], "P-Test2")
        # 缺省 project_id = Default 兜底（存量/直调语义）
        req2 = self._create(_ME_LOCAL)
        self.assertEqual(req2["projectId"], kr.alert_poller.KEY_PROJECT_ID)
        self.assertEqual(req2["projectName"], "Default")

    def test_guard_scoped_to_project(self):
        # upgrade 守卫的当前档查询带申请项目——归属校验不再跨项目放行别项目同名 key
        captured = []

        def rec(ax, uid, project_id=None):
            captured.append(project_id)
            return [{"id": "gid://axonhub/APIKey/101", "name": "emp-k", "userID": uid,
                     "profiles": {"activeProfile": "体验档"}}]

        with self._member_of_p2(), \
             mock.patch.object(kr.alert_poller, "query_user_enabled_keys", side_effect=rec):
            req, err = kr.create_request(_ME_FEISHU, "upgrade", "", "标准档",
                                         key_ids=["gid://axonhub/APIKey/101"], project_id=_P2)
        self.assertIsNone(err)
        self.assertEqual(captured, [_P2])
        self.assertEqual(req["projectId"], _P2)

    def test_execute_new_lands_in_request_project(self):
        # 执行落申请单记录的项目（与管理员当前所在项目解耦）
        req = self._create_p2()
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_FEISHU), \
             self._member_of_p2(), \
             mock.patch.object(kr.alert_poller, "ensure_emp_key",
                               return_value=("emp-x", "ah-x", "")) as ek, \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            out, err = kr.resolve_request(req["id"], "approve")
        self.assertIsNone(err)
        self.assertEqual(out["status"], "approved")
        self.assertEqual(ek.call_args.kwargs["project_id"], _P2)

    def test_execute_new_member_removed_no_key(self):
        # 审批期间被移出项目 → 不建 Key、结果文本说明，申请照常 approved
        req = self._create_p2()
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_FEISHU), \
             mock.patch.object(kr.alert_poller, "ensure_emp_key") as ek, \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            # _Base 默认成员 mock 只剩 Default → 执行复查不命中 P2
            out, err = kr.resolve_request(req["id"], "approve")
        self.assertIsNone(err)
        self.assertEqual(out["status"], "approved")
        self.assertIn("已不在项目", out["result"])
        self.assertIn("P-Test2", out["result"])
        ek.assert_not_called()

    def test_execute_legacy_request_defaults_project(self):
        # 存量申请（无 projectId 字段）执行落 Default——#89 前数据零迁移
        req = self._create(_ME_FEISHU)
        with kr._lock:
            reqs = kr._load()
            del reqs[0]["projectId"]
            del reqs[0]["projectName"]
            kr._save(reqs)
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_FEISHU), \
             mock.patch.object(kr.alert_poller, "ensure_emp_key",
                               return_value=("emp-x", "ah-x", "")) as ek, \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            out, err = kr.resolve_request(req["id"], "approve")
        self.assertIsNone(err)
        self.assertEqual(out["status"], "approved")
        self.assertEqual(ek.call_args.kwargs["project_id"], kr.alert_poller.KEY_PROJECT_ID)

    def test_execute_upgrade_passes_project_id(self):
        req = self._create_p2(kind="upgrade", tier="标准档", key_ids=["gid://axonhub/APIKey/101"])
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_FEISHU), \
             mock.patch.object(kr.alert_poller, "apply_tier_to_user",
                               return_value="已将 1 个 Key 换挂 标准档") as at, \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            out, err = kr.resolve_request(req["id"], "approve")
        self.assertIsNone(err)
        self.assertEqual(at.call_args.kwargs["project_id"], _P2)
        self.assertEqual(at.call_args.kwargs["key_ids"], ["gid://axonhub/APIKey/101"])

    def test_list_requests_project_filter(self):
        req_default = self._create(_ME_LOCAL)          # Default 项目
        req_p2 = self._create_p2(_ME_FEISHU)           # P2 项目（不同人避开 dup 409）
        # 存量无字段申请视为 Default
        req_legacy = self._create({"id": "gid://axonhub/User/5", "email": "legacy@x",
                                   "isOwner": False, "scopes": []})
        with kr._lock:
            reqs = kr._load()
            for r in reqs:
                if r["id"] == req_legacy["id"]:
                    del r["projectId"]
                    del r["projectName"]
            kr._save(reqs)
        p2_list = kr.list_requests(project_id=_P2)
        self.assertEqual([r["id"] for r in p2_list], [req_p2["id"]])
        default_list = kr.list_requests(project_id=kr.alert_poller.KEY_PROJECT_ID)
        self.assertEqual({r["id"] for r in default_list}, {req_default["id"], req_legacy["id"]})
        self.assertEqual(len(kr.list_requests()), 3)  # 不过滤=全量

    def test_detail_line_shows_project(self):
        req = self._create_p2()
        self.assertIn("**项目**: P-Test2", kr._detail_line(req))
        # 存量申请无快照 → Default（与过滤/执行同口径）
        legacy = {"kind": "new", "purpose": "x"}
        self.assertIn("**项目**: Default", kr._detail_line(legacy))

    def test_card_url_carries_project_param(self):
        # issue #91 P2-1：审批卡按钮 URL 带 ?project=<urlencoded gid>，直达申请所属项目
        req = self._create_p2()
        url = kr._request_card(req)["elements"][1]["actions"][0]["url"]
        self.assertTrue(url.endswith("?project=" + urllib.parse.quote(_P2, safe="")))
        # 存量无字段申请 → Default gid（_req_project_id 口径）
        legacy = {"kind": "new", "purpose": "x"}
        legacy_url = kr._console_url(legacy)
        self.assertTrue(legacy_url.endswith(
            "?project=" + urllib.parse.quote(kr.alert_poller.KEY_PROJECT_ID, safe="")))

    def test_fallback_group_text_url_carries_project_param(self):
        # issue #91 P2-1：降级群文本（_Base 默认无管理员 open_id → 群 webhook）URL 同步带参
        self._create_p2()
        self.assertIn("?project=" + urllib.parse.quote(_P2, safe=""), self.group[0])

    def test_card_time_shown_as_cst(self):
        # 飞卡卡面时间统一东八：存储仍是 ISO Z，显示转 UTC+8
        req = {"id": "kr-20260902-abcdef", "kind": "new", "purpose": "联调", "createdAt": "2026-09-02T13:36:45Z"}
        content = kr._request_card(req)["elements"][0]["text"]["content"]
        self.assertIn("2026-09-02 21:36:45 UTC+8", content)
        self.assertNotIn("2026-09-02T13:36:45", content)

    def test_dup_same_project_409_with_project_name(self):
        # issue #90：同项目同 kind pending 仍 409（防 spam 不变），文案带冲突项目名
        req = self._create_p2()
        with self._member_of_p2():
            dup, err = kr.create_request(_ME_FEISHU, "new", "再来一个", "", project_id=_P2)
        self.assertIsNone(dup)
        self.assertEqual(err[0], 409)
        self.assertIn("P-Test2", err[1])
        self.assertIn(req["id"], err[1])

    def test_dup_cross_project_allowed(self):
        # issue #90：Default 挂 pending → P2 同 kind 放行（跨项目互不阻塞）
        self._create(_ME_FEISHU)  # Default pending
        req_p2, err = None, None
        with self._member_of_p2():
            req_p2, err = kr.create_request(_ME_FEISHU, "new", "P2 联调", "", project_id=_P2)
        self.assertIsNone(err)
        self.assertEqual(req_p2["projectId"], _P2)

    def test_dup_legacy_request_counts_as_default(self):
        # issue #90：存量无字段申请参与 Default 维度去重（_req_project_id 口径），
        # 文案回退项目名 Default；但不阻塞 P2 申请
        req = self._create(_ME_FEISHU)
        with kr._lock:
            reqs = kr._load()
            del reqs[0]["projectId"]
            del reqs[0]["projectName"]
            kr._save(reqs)
        _, err = kr.create_request(_ME_FEISHU, "new", "再来", "")
        self.assertEqual(err[0], 409)
        self.assertIn("Default", err[1])
        with self._member_of_p2():
            _, err2 = kr.create_request(_ME_FEISHU, "new", "P2 联调", "", project_id=_P2)
        self.assertIsNone(err2)


_P3 = "gid://axonhub/Project/3"


class TestProjectOverride(_Base):
    """issue #128：管理员批准新建申请时指定正式项目（project_override）。
    锁外 myProjects 存在性校验（不存在 400 / 查询异常 502，均保持 pending 可重试）→ 落
    projectOverride/projectNameOverride 快照 → 执行落 override 项目：非成员先 scopes=[]
    零能力入项再建 Key，已是成员直接建；upgrade 带 override 400（锁内廉价校验，零 gql）；
    无 override 存量 #89 fail-closed 成员复查原样。
    mock 边界与 _Base 同款线路边界：ax.gql 按查询串分流 myProjects/addUserToProject，
    成员资格仍走 query_user_projects mock。"""

    def _ax_mock(self, projects, events):
        """假 Axonhub：myProjects 返回给定项目列表；addUserToProject 记录入项 input 到 events。"""
        ax = mock.Mock()

        def _gql(query, variables=None):
            if "myProjects" in query:
                return {"myProjects": projects}
            if "addUserToProject" in query:
                events.append(("add", variables["input"]))
                return {"addUserToProject": {"id": "m-1"}}
            raise AssertionError(f"unexpected gql: {query}")

        ax.gql.side_effect = _gql
        return ax

    def _ensure_mock(self, events):
        """ensure_emp_key side_effect：记录建 Key 事件（顺序断言用），返回标准三元组。"""
        def _ensure(*_a, **kw):
            events.append(("key", kw.get("project_id")))
            return ("emp-x", "ah-x", "")
        return _ensure

    def test_override_non_member_zero_scope_add_then_key(self):
        # ①override 到非成员项目：先零能力入项（scopes==[]）再建 Key 于 override 项目
        req = self._create(_ME_LOCAL)
        events = []
        ax = self._ax_mock([{"id": kr.alert_poller.KEY_PROJECT_ID, "name": "Default"},
                            {"id": _P3, "name": "P-Formal"}], events)
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_LOCAL), \
             mock.patch.object(kr.alert_poller, "ensure_emp_key",
                               side_effect=self._ensure_mock(events)), \
             mock.patch.object(kr, "_get_ax", return_value=ax):
            out, err = kr.resolve_request(req["id"], "approve", project_override=_P3)
        self.assertIsNone(err)
        self.assertEqual(out["status"], "approved")
        # 顺序锁定：先入项后建 Key；入项 input 逐项断言（零能力 scopes==[] 是 #128 核心语义）
        self.assertEqual([e[0] for e in events], ["add", "key"])
        add_input = events[0][1]
        self.assertEqual(add_input["projectId"], _P3)
        self.assertEqual(add_input["userId"], _USER_LOCAL["id"])
        self.assertEqual(add_input["isOwner"], False)
        self.assertEqual(add_input["scopes"], [])
        self.assertEqual(events[1][1], _P3)  # ensure_emp_key 落 override 项目
        # 快照落申请单并经 shape_public 透出；结果摘要/详情行体现落点项目名
        self.assertEqual(out["projectOverride"], _P3)
        self.assertEqual(out["projectNameOverride"], "P-Formal")
        self.assertIn("项目: P-Formal", out["result"])
        stored = [r for r in kr._load() if r["id"] == req["id"]][0]
        self.assertEqual(stored["projectOverride"], _P3)
        self.assertEqual(stored["projectNameOverride"], "P-Formal")
        self.assertIn("**项目**: P-Formal", kr._detail_line(stored))  # 回执卡项目行覆盖显示

    def test_override_member_direct_key_no_add(self):
        # ②override 到已是成员项目：不重复入项，直接建 Key
        req = self._create(_ME_LOCAL)
        events = []
        ax = self._ax_mock([{"id": _P3, "name": "P-Formal"}], events)
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_LOCAL), \
             mock.patch.object(kr.alert_poller, "query_user_projects",
                               side_effect=lambda ax_, uid: [
                                   {"id": kr.alert_poller.KEY_PROJECT_ID, "name": "Default"},
                                   {"id": _P3, "name": "P-Formal"}]), \
             mock.patch.object(kr.alert_poller, "ensure_emp_key",
                               side_effect=self._ensure_mock(events)), \
             mock.patch.object(kr, "_get_ax", return_value=ax):
            out, err = kr.resolve_request(req["id"], "approve", project_override=_P3)
        self.assertIsNone(err)
        self.assertEqual(out["status"], "approved")
        self.assertEqual(events, [("key", _P3)])  # 无入项事件

    def test_override_unknown_project_400(self):
        # ③override gid 不在 myProjects → 400「项目不存在」；不执行、保持 pending、不落快照
        req = self._create(_ME_LOCAL)
        ax = self._ax_mock([{"id": kr.alert_poller.KEY_PROJECT_ID, "name": "Default"}], [])
        with mock.patch.object(kr.alert_poller, "ensure_emp_key") as ek, \
             mock.patch.object(kr, "_get_ax", return_value=ax):
            out, err = kr.resolve_request(req["id"], "approve", project_override=_P3)
        self.assertEqual(err[0], 400)
        self.assertIn("项目不存在", err[1])
        self.assertEqual(out["status"], "pending")
        ek.assert_not_called()
        stored = [r for r in kr._load() if r["id"] == req["id"]][0]
        self.assertIsNone(stored["projectOverride"])

    def test_override_query_error_502_retryable(self):
        # ③b myProjects 查询异常 → 502 保持 pending 可重试（fail-closed 不猜项目）
        req = self._create(_ME_LOCAL)
        ax = mock.Mock()
        ax.gql.side_effect = RuntimeError("gql down")
        with mock.patch.object(kr.alert_poller, "ensure_emp_key") as ek, \
             mock.patch.object(kr, "_get_ax", return_value=ax):
            out, err = kr.resolve_request(req["id"], "approve", project_override=_P3)
        self.assertEqual(err[0], 502)
        self.assertIn("项目校验", err[1])
        self.assertEqual(out["status"], "pending")
        ek.assert_not_called()
        stored = [r for r in kr._load() if r["id"] == req["id"]][0]
        self.assertIsNone(stored["projectOverride"])

    def test_upgrade_with_override_400(self):
        # ④upgrade 带 override → 400（提额不涉及项目变更）；锁内廉价校验——不触发任何 gql
        req = self._create(_ME_LOCAL, kind="upgrade", tier="标准档")
        with mock.patch.object(kr.alert_poller, "apply_tier_to_user") as at, \
             mock.patch.object(kr, "_get_ax") as get_ax:
            out, err = kr.resolve_request(req["id"], "approve", project_override=_P3)
        self.assertEqual(err[0], 400)
        self.assertIn("project_override", err[1])
        self.assertEqual(out["status"], "pending")
        at.assert_not_called()
        get_ax.assert_not_called()

    def test_no_override_legacy_member_recheck_unchanged(self):
        # ⑤无 override：#89 存量 fail-closed 成员复查原样——审批期间被移出项目不建 Key、
        # 照常 approved 落结果；override 字段保持 None
        req = self._create(_ME_LOCAL)
        with mock.patch.object(kr.alert_poller, "find_user_by_email", return_value=_USER_LOCAL), \
             mock.patch.object(kr.alert_poller, "query_user_projects",
                               side_effect=lambda ax_, uid: []), \
             mock.patch.object(kr.alert_poller, "ensure_emp_key") as ek, \
             mock.patch.object(kr, "_get_ax", return_value=object()):
            out, err = kr.resolve_request(req["id"], "approve")
        self.assertIsNone(err)
        self.assertEqual(out["status"], "approved")
        self.assertIn("已不在项目", out["result"])
        ek.assert_not_called()
        self.assertIsNone(out["projectOverride"])


if __name__ == "__main__":
    unittest.main()
