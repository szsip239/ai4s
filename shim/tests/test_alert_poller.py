#!/usr/bin/env python3
"""alert_poller 巡检判定核心分支测试（issue #56：随 alert-poller 并入 shim 补齐——原独立容器无测试）。

seam 纪律：判定逻辑抽纯函数（quota_dims/classify_quota/flip_actions/parse_tier/approval_action/
parse_purpose/make_key_name）直接测；check_cycle/approval_sync/create_emp_key 只在线路边界
mock（http_get / Axonhub.gql / feishu_get / send_feishu / apply_tier / create_emp_key /
assign_key_owner / feishu_dm），不 mock 模块内部判定函数。
issue #57 增补：POLL_INTERVAL 非法值 import 安全（P1）、save_state 原子写与纯文件名边角（P2-2）。
issue #72 增补：审批同步泛化（提额/新建并存）、新建执行体各分支、归属 SQL 形状（psycopg 假模块注入）。
issue #73 增补：新用户自动入 Default 项目（pending_default_project_users 纯函数筛选、
auto_assign_project 幂等/单用户失败不阻塞/入项群通知）。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

# 让测试可 import shim 目录下的 alert_poller（discover 从 shim/tests 启动）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alert_poller as ap


class TestQuotaDims(unittest.TestCase):
    """额度维度比率：维度缺失/配额 0 不参与；usage 缺键按 0。"""

    def test_all_dims(self):
        dims = ap.quota_dims(
            {"requests": 100, "totalTokens": 1000, "cost": "10"},
            {"requestCount": 50, "totalTokens": 800, "totalCost": "9.5"},
        )
        self.assertEqual([d[0] for d in dims], ["请求数", "token", "credit"])
        self.assertAlmostEqual(dims[0][1], 0.5)
        self.assertAlmostEqual(dims[1][1], 0.8)
        self.assertAlmostEqual(dims[2][1], 0.95)
        self.assertEqual(dims[2][2], "9.5/10")

    def test_missing_usage_defaults_zero(self):
        dims = ap.quota_dims({"requests": 100}, {})
        self.assertEqual(len(dims), 1)
        self.assertAlmostEqual(dims[0][1], 0.0)

    def test_absent_and_zero_quota_skipped(self):
        # requests/totalTokens 缺失、cost None 或 "0" → 空 dims（无判定维度）
        self.assertEqual(ap.quota_dims({}, {}), [])
        self.assertEqual(ap.quota_dims({"requests": 0, "totalTokens": None, "cost": None}, {}), [])
        self.assertEqual(ap.quota_dims({"cost": "0"}, {"totalCost": "1"}), [])


class TestClassifyQuota(unittest.TestCase):
    """warning/exhausted 判定：over ≥100%；near ∈[80%,100%)。"""

    def test_under_80_ok(self):
        over, near = ap.classify_quota([("请求数", 0.79, "79/100")])
        self.assertEqual(over, [])
        self.assertEqual(near, [])

    def test_near_at_80(self):
        over, near = ap.classify_quota([("token", 0.8, "80/100")])
        self.assertEqual(over, [])
        self.assertEqual(len(near), 1)

    def test_over_at_100(self):
        over, near = ap.classify_quota([("请求数", 1.0, "100/100")])
        self.assertEqual(len(over), 1)
        self.assertEqual(near, [])  # ≥1.0 不算 near

    def test_mixed_dims(self):
        dims = [("请求数", 1.2, "120/100"), ("token", 0.85, "85/100"), ("credit", 0.5, "5/10")]
        over, near = ap.classify_quota(dims)
        self.assertEqual([d[0] for d in over], ["请求数"])
        self.assertEqual([d[0] for d in near], ["token"])
        # 耗尽优先语义（调用方）：bool(near) and not over 为 False——已耗尽不再发 80% 预警
        self.assertFalse(bool(near) and not over)


class TestFlipActions(unittest.TestCase):
    """防抖翻转：只在 bad 位变化时产出动作。"""

    F = {
        "k1": (True, "ALERT-1", "RECOVER-1"),
        "k2": (False, "ALERT-2", "RECOVER-2"),
    }

    def test_new_bad_alerts(self):
        self.assertEqual(ap.flip_actions(self.F, {}), [("k1", "alert", "ALERT-1")])

    def test_steady_bad_no_repeat(self):
        self.assertEqual(ap.flip_actions(self.F, {"k1": True}), [])

    def test_recover_after_bad(self):
        self.assertEqual(ap.flip_actions(self.F, {"k1": True, "k2": True}),
                         [("k2", "recover", "RECOVER-2")])

    def test_good_stays_silent(self):
        self.assertEqual(ap.flip_actions({"k": (False, "a", "r")}, {}), [])


class TestParseTier(unittest.TestCase):
    def test_premium(self):
        for text in ("高档", "申请高档额度", "premium", "Premium 档", "pro", "PRO"):
            self.assertEqual(ap.parse_tier(text), "高档", text)

    def test_standard(self):
        for text in ("标准档", "标准", "standard", "Standard", "std", "STD 档"):
            self.assertEqual(ap.parse_tier(text), "标准档", text)

    def test_unrecognized(self):
        for text in ("", "随便加点", "abc"):
            self.assertIsNone(ap.parse_tier(text), text)


class TestApprovalAction(unittest.TestCase):
    def test_branches(self):
        self.assertEqual(ap.approval_action("PENDING"), "skip")
        self.assertEqual(ap.approval_action("APPROVED"), "process")
        self.assertEqual(ap.approval_action("REJECTED"), "receipt")
        self.assertEqual(ap.approval_action("CANCELED"), "mark")
        self.assertEqual(ap.approval_action("DELETED"), "mark")
        self.assertEqual(ap.approval_action("SOME_UNKNOWN"), "mark")  # 未知终态只标记（同原实现）


class TestApprovalSync(unittest.TestCase):
    """审批处理分支：凭据缺失整体跳过；各状态分支的标记/重试语义。"""

    def _run(self, instances_by_id, state=None, apply_side_effect=None):
        """mock 线路边界跑一轮 approval_sync；返回 (state, send_calls, apply_calls)。"""
        state = state if state is not None else {}
        sends, applies = [], []

        def fake_feishu_get(path):
            if path.startswith("/approval/v4/instances/"):
                ic = path.rsplit("/", 1)[1]
                return instances_by_id[ic]
            return {"instance_code_list": list(instances_by_id)}

        def fake_send(text):
            sends.append(text)
            return True

        def fake_apply(ax, open_id, tier_name):
            applies.append((open_id, tier_name))
            if apply_side_effect:
                raise apply_side_effect
            return "已将 1 个 Key 换挂"

        with mock.patch.multiple(
            ap, feishu_get=fake_feishu_get, send_feishu=fake_send, apply_tier=fake_apply,
            APPROVAL_QUOTA_CODE="CODE", FEISHU_APP_ID="id", FEISHU_APP_SECRET="secret",
        ):
            ap.approval_sync(mock.Mock(), state)
        return state, sends, applies

    def test_no_credentials_skips_entirely(self):
        # 凭据缺失：不调飞书接口直接返回（只巡检不审批，与独立容器时代一致）
        state = {}
        with mock.patch.multiple(ap, APPROVAL_QUOTA_CODE="", FEISHU_APP_ID="", FEISHU_APP_SECRET=""), \
             mock.patch.object(ap, "feishu_get", side_effect=AssertionError("不应被调用")):
            ap.approval_sync(mock.Mock(), state)
        self.assertEqual(state, {})

    def test_pending_not_marked(self):
        state, sends, _ = self._run({"ic1": {"status": "PENDING"}})
        self.assertEqual(state.get("approval_done"), [])
        self.assertEqual(sends, [])

    def test_approved_processed_and_marked(self):
        inst = {"status": "APPROVED", "open_id": "ou_abc",
                "form": '[{"custom_id": "widget_tier", "value": "高档"}]'}
        state, sends, applies = self._run({"ic1": inst})
        self.assertEqual(applies, [("ou_abc", "高档")])
        self.assertEqual(state["approval_done"], ["ic1"])
        self.assertEqual(len(sends), 1)
        self.assertIn("审批通过", sends[0])

    def test_approved_unrecognized_tier_receipt_no_apply(self):
        inst = {"status": "APPROVED", "open_id": "ou_abc",
                "form": '[{"custom_id": "widget_tier", "value": "随便"}]'}
        state, sends, applies = self._run({"ic1": inst})
        self.assertEqual(applies, [])  # 档位识别失败不执行，但仍回执并标记（同原实现）
        self.assertEqual(state["approval_done"], ["ic1"])
        self.assertIn("无法识别目标档位", sends[0])

    def test_apply_failure_not_marked_for_retry(self):
        inst = {"status": "APPROVED", "open_id": "ou_abc",
                "form": '[{"custom_id": "widget_tier", "value": "高档"}]'}
        state, sends, _ = self._run({"ic1": inst}, apply_side_effect=RuntimeError("gql down"))
        self.assertEqual(state.get("approval_done"), [])  # 执行失败不标记，下轮重试
        self.assertEqual(sends, [])

    def test_rejected_receipt_and_marked(self):
        state, sends, _ = self._run({"ic1": {"status": "REJECTED", "open_id": "ou_x"}})
        self.assertEqual(state["approval_done"], ["ic1"])
        self.assertIn("审批未通过", sends[0])

    def test_canceled_marked_silently(self):
        state, sends, _ = self._run({"ic1": {"status": "CANCELED"}})
        self.assertEqual(state["approval_done"], ["ic1"])
        self.assertEqual(sends, [])

    def test_done_instances_skipped(self):
        state, sends, applies = self._run(
            {"ic1": {"status": "APPROVED", "open_id": "ou_abc", "form": "[]"}},
            state={"approval_done": ["ic1"]},
        )
        self.assertEqual(applies, [])
        self.assertEqual(sends, [])

    def test_done_list_trimmed_to_200(self):
        state = {"approval_done": [f"old{i}" for i in range(200)]}
        state, _, _ = self._run({"new1": {"status": "CANCELED"}}, state=state)
        self.assertEqual(len(state["approval_done"]), 200)
        self.assertEqual(state["approval_done"][-1], "new1")
        self.assertNotIn("old0", state["approval_done"])


class TestParsePurpose(unittest.TestCase):
    """issue #72：新建审批表单 widget_purpose 解析。"""

    def test_by_custom_id(self):
        form = [{"custom_id": "widget_guide", "value": "说明"},
                {"custom_id": "widget_purpose", "value": "  项目联调用  "}]
        self.assertEqual(ap.parse_purpose(form), "项目联调用")

    def test_by_id_fallback_and_str_form(self):
        form = json.dumps([{"id": "widget_purpose", "value": "写报告"}])
        self.assertEqual(ap.parse_purpose(form), "写报告")

    def test_missing_or_empty(self):
        self.assertEqual(ap.parse_purpose([]), "")
        self.assertEqual(ap.parse_purpose("not-json"), "")
        self.assertEqual(ap.parse_purpose([{"custom_id": "widget_purpose"}]), "")


class TestMakeKeyName(unittest.TestCase):
    """issue #72 命名规范：emp-<oid8>-<yyyymmdd>-<用途摘要≤12>-<实例码尾4>。"""

    def test_format_and_charset(self):
        name = ap.make_key_name("ou_bc5333f08f1823b88d8bcf0511a2f409", "项目联调!", "20260820",
                                "C3DC8B3B-335F-4059-BC50-01F601D0F18C")
        self.assertEqual(name, "emp-ou_bc533-20260820-项目联调-f18c")

    def test_slug_length_cap_and_empty_purpose(self):
        name = ap.make_key_name("ou_x", "这是一个非常非常非常长的用途说明超过十二字", "20260820", "ic-abcd")
        self.assertEqual(name, "emp-ou_x-20260820-这是一个非常非常非常长的-abcd")  # 摘要截 12 字
        self.assertEqual(ap.make_key_name("ou_x", "", "20260820", "ic1"), "emp-ou_x-20260820-key-ic1")

    def test_deterministic(self):
        a = ap.make_key_name("ou_abc", "用途", "20260820", "ic-99")
        b = ap.make_key_name("ou_abc", "用途", "20260820", "ic-99")
        self.assertEqual(a, b)


class TestAssignKeyOwner(unittest.TestCase):
    """issue #72 归属步骤：GID 解析 + SQL 形状（psycopg 以假模块注入，不落真库）。"""

    def test_dsn_missing_raises(self):
        with mock.patch.object(ap, "AXONHUB_DB_DSN", ""):
            with self.assertRaises(RuntimeError):
                ap.assign_key_owner("gid://axonhub/APIKey/9", "gid://axonhub/User/7")

    def test_sql_params(self):
        executed = []

        class FakeCursor:
            rowcount = 1

        class FakeConn:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql, params):
                executed.append((sql, params))
                return FakeCursor()
            def commit(self): pass
            def rollback(self): pass

        fake_connect = mock.Mock(return_value=FakeConn())
        fake_psycopg = mock.Mock(connect=fake_connect)
        with mock.patch.object(ap, "AXONHUB_DB_DSN", "postgres://x"), \
             mock.patch.dict("sys.modules", {"psycopg": fake_psycopg}):
            ap.assign_key_owner("gid://axonhub/APIKey/9", "gid://axonhub/User/7")
        self.assertEqual(len(executed), 1)
        sql, params = executed[0]
        self.assertIn("UPDATE api_keys SET user_id", sql)
        self.assertEqual(params, (7, 9))
        fake_connect.assert_called_once()

    def test_rowcount_zero_raises(self):
        # UPDATE 命中 0 行（key 不存在/已软删）必须抛错让上层重试，不静默成功（评审 P2）
        class FakeCursor:
            rowcount = 0

        class FakeConn:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql, params): return FakeCursor()
            def commit(self): pass
            def rollback(self): pass

        fake_psycopg = mock.Mock(connect=lambda *a, **k: FakeConn())
        with mock.patch.object(ap, "AXONHUB_DB_DSN", "postgres://x"), \
             mock.patch.dict("sys.modules", {"psycopg": fake_psycopg}):
            with self.assertRaises(RuntimeError):
                ap.assign_key_owner("gid://axonhub/APIKey/9", "gid://axonhub/User/7")


class TestCreateEmpKey(unittest.TestCase):
    """issue #72 新建执行体：用户检查/建 key/归属/挂体验档/私信交付/幂等恢复（线路边界全 mock）。"""

    def _run(self, gql_handler, assign_side_effect=None, dm_ok=True):
        ax = mock.Mock()
        gql_calls = []

        def fake_gql(query, variables=None):
            gql_calls.append((query, variables or {}))
            return gql_handler(query, variables or {})

        ax.gql = fake_gql
        with mock.patch.object(ap, "assign_key_owner",
                               side_effect=assign_side_effect or (lambda k, u: None)) as assign, \
             mock.patch.object(ap, "feishu_dm", return_value=dm_ok) as dm:
            result = ap.create_emp_key(ax, "ou_abc12345", "项目联调", "20260820", "ic-0000abcd")
        return result, gql_calls, assign, dm

    def _handler(self, user=True, existing_key=None):
        tpl = {"name": "体验档", "profile": {"quota": {"requests": None, "totalTokens": 4300000,
               "cost": 3, "period": {"type": "calendar_duration", "calendarDuration": {"unit": "month"}}}}}

        def handler(query, variables):
            if "users(" in query:
                node = {"id": "gid://axonhub/User/7", "email": variables["email"], "status": "activated"}
                return {"users": {"edges": [{"node": node}] if user else []}}
            if "apiKeys(" in query:
                node = existing_key
                return {"apiKeys": {"edges": [{"node": node}] if node else []}}
            if "createAPIKey" in query:
                return {"createAPIKey": {"id": "gid://axonhub/APIKey/9", "name": variables["input"]["name"],
                                         "key": "ah-plaintext-probe"}}
            if "apiKeyProfileTemplates" in query:
                return {"apiKeyProfileTemplates": {"edges": [{"node": tpl}]}}
            if "updateAPIKeyProfiles" in query:
                return {"updateAPIKeyProfiles": {"id": variables["id"]}}
            raise AssertionError(f"unexpected gql: {query[:60]}")

        return handler

    def test_happy_path(self):
        result, calls, assign, dm = self._run(self._handler())
        self.assertIn("emp-ou_abc12-20260820-项目联调-abcd", result)
        self.assertIn("明文已私信", result)
        self.assertNotIn("ah-plaintext-probe", result)  # 群回执摘要绝不含明文
        assign.assert_called_once_with("gid://axonhub/APIKey/9", "gid://axonhub/User/7")
        dm_text = dm.call_args[0][1]
        self.assertEqual(dm.call_args[0][0], "ou_abc12345")
        self.assertIn("ah-plaintext-probe", dm_text)  # 私信含明文
        # 建 key 入参：默认项目 + type 不传（user 默认，scopes 由 schema 给安全默认）
        create_vars = next(v for q, v in calls if "createAPIKey" in q)
        self.assertEqual(create_vars["input"]["projectID"], ap.KEY_PROJECT_ID)
        self.assertNotIn("scopes", create_vars["input"])
        # 挂体验档
        prof_vars = next(v for q, v in calls if "updateAPIKeyProfiles" in q)
        self.assertEqual(prof_vars["input"]["activeProfile"], "体验档")

    def test_user_missing_no_create(self):
        result, calls, assign, dm = self._run(self._handler(user=False))
        self.assertIn("未首登", result)
        self.assertFalse(any("createAPIKey" in q for q, _ in calls))
        assign.assert_not_called()
        dm.assert_not_called()

    def test_dm_failure_fallback_tail_only(self):
        result, _, _, _ = self._run(self._handler(), dm_ok=False)
        self.assertIn("私信未送达", result)
        self.assertIn("…robe", result)  # 尾号 4 位
        self.assertNotIn("ah-plaintext-probe", result)

    def test_assign_failure_still_delivers(self):
        result, _, _, dm = self._run(self._handler(), assign_side_effect=RuntimeError("db down"))
        self.assertIn("归属调整失败", result)
        dm.assert_called_once()

    def test_idempotent_recover_existing_key(self):
        existing = {"id": "gid://axonhub/APIKey/9", "name": "emp-ou_abc12-20260820-项目联调-abcd",
                    "key": "ah-recovered"}
        result, calls, _, dm = self._run(self._handler(existing_key=existing))
        self.assertFalse(any("createAPIKey" in q for q, _ in calls))  # 不重复建
        self.assertIn("ah-recovered", dm.call_args[0][1])  # 明文按名找回续交付
        self.assertTrue(any("updateAPIKeyProfiles" in q for q, _ in calls))

    def test_key_lookup_scoped_to_project(self):
        # 评审 P2-1：找回查询必须带 projectID 过滤——全局按名查跨项目同名会取回别人 key 明文
        _, calls, _, _ = self._run(self._handler())
        lookup_vars = next(v for q, v in calls if "apiKeys(" in q)
        self.assertEqual(lookup_vars.get("projectID"), ap.KEY_PROJECT_ID)

    def test_template_missing_raises_for_retry(self):
        def handler(query, variables):
            h = self._handler()
            if "apiKeyProfileTemplates" in query:
                return {"apiKeyProfileTemplates": {"edges": []}}
            return h(query, variables)
        with self.assertRaises(RuntimeError):
            self._run(handler)


class TestApprovalSyncKeyKind(unittest.TestCase):
    """issue #72：approval_sync 泛化——新建审批定义与提额并存，分支/标记语义对齐。"""

    def _run(self, instances_by_code, state=None, create_side_effect=None,
             quota_code="", key_code="KEYCODE"):
        state = state if state is not None else {}
        sends, creates = [], []

        def fake_feishu_get(path):
            if path.startswith("/approval/v4/instances/"):
                ic = path.rsplit("/", 1)[1]
                for insts in instances_by_code.values():
                    if ic in insts:
                        return insts[ic]
                raise AssertionError(f"unknown instance {ic}")
            code = next(p.split("&")[0] for p in [path.split("approval_code=")[1]])
            return {"instance_code_list": list(instances_by_code.get(code, {}))}

        def fake_send(text):
            sends.append(text)
            return True

        def fake_create(ax, open_id, purpose, day, ic):
            creates.append((open_id, purpose, ic))
            if create_side_effect:
                raise create_side_effect
            return "已建 Key emp-x（体验档），明文已私信申请人"

        with mock.patch.multiple(
            ap, feishu_get=fake_feishu_get, send_feishu=fake_send, create_emp_key=fake_create,
            APPROVAL_QUOTA_CODE=quota_code, APPROVAL_KEY_CODE=key_code,
            FEISHU_APP_ID="id", FEISHU_APP_SECRET="secret",
        ):
            ap.approval_sync(mock.Mock(), state)
        return state, sends, creates

    def test_approved_creates_and_marks(self):
        inst = {"status": "APPROVED", "open_id": "ou_abc", "start_time": "1787184000000",
                "form": '[{"custom_id": "widget_purpose", "value": "项目联调"}]'}
        state, sends, creates = self._run({"KEYCODE": {"ic1": inst}})
        self.assertEqual(creates, [("ou_abc", "项目联调", "ic1")])
        self.assertEqual(state["approval_done"], ["ic1"])
        self.assertEqual(len(sends), 1)
        self.assertIn("新建 Key", sends[0])
        self.assertIn("审批通过", sends[0])

    def test_create_failure_not_marked(self):
        inst = {"status": "APPROVED", "open_id": "ou_abc", "form": "[]"}
        state, sends, _ = self._run({"KEYCODE": {"ic1": inst}},
                                    create_side_effect=RuntimeError("gql down"))
        self.assertEqual(state.get("approval_done"), [])
        self.assertEqual(sends, [])

    def test_rejected_receipt(self):
        state, sends, _ = self._run({"KEYCODE": {"ic1": {"status": "REJECTED", "open_id": "ou_x"}}})
        self.assertEqual(state["approval_done"], ["ic1"])
        self.assertIn("新建 Key", sends[0])
        self.assertIn("未创建任何 Key", sends[0])

    def test_pending_skipped(self):
        state, sends, creates = self._run({"KEYCODE": {"ic1": {"status": "PENDING"}}})
        self.assertEqual(state.get("approval_done"), [])
        self.assertEqual(sends, [])

    def test_both_kinds_coexist(self):
        quota_inst = {"status": "APPROVED", "open_id": "ou_q",
                      "form": '[{"custom_id": "widget_tier", "value": "高档"}]'}
        key_inst = {"status": "APPROVED", "open_id": "ou_k",
                    "form": '[{"custom_id": "widget_purpose", "value": "联调"}]'}
        with mock.patch.object(ap, "apply_tier", return_value="已将 1 个 Key 换挂") as at:
            state, sends, creates = self._run(
                {"QCODE": {"icq": quota_inst}, "KEYCODE": {"ick": key_inst}},
                quota_code="QCODE", key_code="KEYCODE")
        at.assert_called_once()
        self.assertEqual(len(creates), 1)
        self.assertEqual(sorted(state["approval_done"]), ["ick", "icq"])
        self.assertEqual(len(sends), 2)

    def test_quota_kind_failure_isolates_key_kind(self):
        # 评审 P2-2 跨类异常隔离：quota 类拉取抛异常，key 类实例仍被正常处理标记
        key_inst = {"status": "APPROVED", "open_id": "ou_k",
                    "form": '[{"custom_id": "widget_purpose", "value": "联调"}]'}
        state, sends, creates = {}, [], []

        def fake_feishu_get(path):
            if path.startswith("/approval/v4/instances/"):
                return key_inst
            if "approval_code=QCODE" in path:
                raise RuntimeError("quota 列表接口挂了")
            return {"instance_code_list": ["ick"]}

        with mock.patch.multiple(
            ap, feishu_get=fake_feishu_get,
            send_feishu=lambda t: sends.append(t) or True,
            create_emp_key=lambda ax, o, p, d, ic: creates.append(ic) or "已建 Key",
            APPROVAL_QUOTA_CODE="QCODE", APPROVAL_KEY_CODE="KEYCODE",
            FEISHU_APP_ID="id", FEISHU_APP_SECRET="secret",
        ):
            ap.approval_sync(mock.Mock(), state)
        self.assertEqual(creates, ["ick"])
        self.assertEqual(state["approval_done"], ["ick"])
        self.assertEqual(len(sends), 1)


class TestPendingDefaultProjectUsers(unittest.TestCase):
    """issue #73 筛选纯函数：activated、非 owner、不在 Default 项目。"""

    PID = "gid://axonhub/Project/1"

    def _node(self, uid, email, owner=False, status="activated", projects=("gid://axonhub/Project/1",)):
        return {"node": {"id": f"gid://axonhub/User/{uid}", "email": email, "isOwner": owner,
                         "status": status,
                         "projects": {"edges": [{"node": {"id": p}} for p in projects]}}}

    def test_filters(self):
        edges = [
            self._node(1, "admin@x", owner=True, projects=()),          # owner 跳过
            self._node(2, "emp@x"),                                      # 已成员跳过
            self._node(3, "new@x", projects=()),                         # 待入项
            self._node(4, "disabled@x", status="disabled", projects=()), # 非 activated 跳过
            self._node(5, "other@x", projects=("gid://axonhub/Project/9",)),  # 在别的项目 → 待入项
        ]
        out = ap.pending_default_project_users(edges, self.PID)
        self.assertEqual([n["email"] for n in out], ["new@x", "other@x"])

    def test_empty_and_no_projects_edge(self):
        self.assertEqual(ap.pending_default_project_users([], self.PID), [])
        n = {"node": {"id": "u1", "email": "a@x", "isOwner": False, "status": "activated", "projects": None}}
        self.assertEqual([x["email"] for x in ap.pending_default_project_users([n], self.PID)], ["a@x"])


class TestAutoAssignProject(unittest.TestCase):
    """issue #73 执行体：入项 mutation 入参/幂等跳过/单用户失败不阻塞/入项后群通知（边界全 mock）。"""

    def _run(self, users_edges, my_projects=None, fail_on_uid=None):
        ax = mock.Mock()
        sends, adds = [], []

        def fake_gql(query, variables=None):
            if "myProjects" in query:
                return {"myProjects": my_projects if my_projects is not None
                        else [{"id": "gid://axonhub/Project/1", "name": "Default"}]}
            if "users(" in query:
                return {"users": {"edges": users_edges}}
            if "addUserToProject" in query:
                uid = variables["input"]["userId"]
                if fail_on_uid and uid == fail_on_uid:
                    raise RuntimeError("gql down")
                adds.append(variables["input"])
                return {"addUserToProject": {"id": "x"}}
            raise AssertionError(f"unexpected gql: {query[:50]}")

        ax.gql = fake_gql
        with mock.patch.object(ap, "send_feishu", side_effect=lambda t: sends.append(t) or True):
            ap.auto_assign_project(ax)
        return sends, adds

    def _user(self, uid, email, projects=()):
        return {"node": {"id": f"gid://axonhub/User/{uid}", "email": email, "isOwner": False,
                         "status": "activated",
                         "projects": {"edges": [{"node": {"id": p}} for p in projects]}}}

    def test_pending_assigned_and_notified(self):
        sends, adds = self._run([self._user(9, "new@x")])
        self.assertEqual(len(adds), 1)
        self.assertEqual(adds[0], {"projectId": "gid://axonhub/Project/1", "userId": "gid://axonhub/User/9",
                                   "isOwner": False, "scopes": ["read_requests", "write_requests"]})
        self.assertEqual(len(sends), 1)
        self.assertIn("new@x", sends[0])
        self.assertIn("自动加入 Default 项目", sends[0])

    def test_member_skipped_idempotent(self):
        # 已入项成员不重复处理（幂等验证点：零 mutation 零通知）
        sends, adds = self._run([self._user(7, "existing-member@x", projects=("gid://axonhub/Project/1",))])
        self.assertEqual(adds, [])
        self.assertEqual(sends, [])

    def test_single_failure_not_blocking(self):
        # 用户 A 入项失败记日志下轮重试（天然幂等，无需状态位）；用户 B 不受影响照常入项+通知
        sends, adds = self._run(
            [self._user(9, "a@x"), self._user(10, "b@x")], fail_on_uid="gid://axonhub/User/9")
        self.assertEqual(len(adds), 1)
        self.assertEqual(adds[0]["userId"], "gid://axonhub/User/10")
        self.assertEqual(len(sends), 1)
        self.assertIn("b@x", sends[0])

    def test_no_default_project_noop(self):
        sends, adds = self._run([self._user(9, "new@x")], my_projects=[])
        self.assertEqual(adds, [])
        self.assertEqual(sends, [])


class TestCheckCycleDebounce(unittest.TestCase):
    """check_cycle 防抖与恢复翻转（mock 探活 + gql + 发送）。"""

    def _run_cycle(self, state, shim_ok, presidio_ok, send_ok=True):
        sends = []

        def fake_send(text):
            sends.append(text)
            return send_ok

        with mock.patch.object(ap, "http_get", side_effect=[shim_ok, presidio_ok]), \
             mock.patch.object(ap, "send_feishu", side_effect=fake_send):
            new_state = ap.check_cycle(mock.Mock(), state)
        return new_state, sends

    def test_alert_then_steady_then_recover(self):
        # 轮 1：presidio 挂 → 告警且状态翻转
        state, sends = self._run_cycle({}, shim_ok=True, presidio_ok=False)
        self.assertEqual(len(sends), 1)
        self.assertIn("Presidio 不可达", sends[0])
        self.assertTrue(state["dlp:presidio"])
        self.assertNotIn("dlp:shim", state)  # 正常项不落状态
        # 轮 2：仍挂 → 不重复发
        state, sends = self._run_cycle(state, shim_ok=True, presidio_ok=False)
        self.assertEqual(sends, [])
        self.assertTrue(state["dlp:presidio"])
        # 轮 3：恢复 → 发恢复通知且状态清除
        state, sends = self._run_cycle(state, shim_ok=True, presidio_ok=True)
        self.assertEqual(len(sends), 1)
        self.assertIn("已恢复", sends[0])
        self.assertFalse(state["dlp:presidio"])

    def test_send_failure_keeps_state_for_retry(self):
        # 发送失败不更新状态——下轮仍会尝试告警
        state, sends = self._run_cycle({}, shim_ok=True, presidio_ok=False, send_ok=False)
        self.assertEqual(len(sends), 1)
        self.assertNotIn("dlp:presidio", state)
        state, sends = self._run_cycle(state, shim_ok=True, presidio_ok=False)
        self.assertEqual(len(sends), 1)  # 重试发出

    def test_gql_failure_does_not_break_cycle(self):
        # gql 全挂（axonhub 不可达）：探活 finding 仍产出，循环不抛异常
        ax = mock.Mock()
        ax.gql.side_effect = RuntimeError("axonhub down")
        with mock.patch.object(ap, "http_get", return_value=True), \
             mock.patch.object(ap, "send_feishu", return_value=True):
            state = ap.check_cycle(ax, {})
        self.assertEqual(state, {})


class TestImportDoesNotStartThread(unittest.TestCase):
    """隔离纪律（issue #56）：import alert_poller 不起巡检线程。"""

    def test_no_poller_thread_on_import(self):
        import threading
        self.assertNotIn("alert-poller", [t.name for t in threading.enumerate()])


_SHIM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestPollIntervalImportSafety(unittest.TestCase):
    """issue #57 P1：POLL_INTERVAL 非法值 import 不抛、生效默认 30；合法值正常生效。
    用子进程隔离——模块级 env 解析只发生在 import 时，主测试进程已 import 过。"""

    def _import_with(self, interval: str):
        code = (
            "import sys; sys.path.insert(0, " + repr(_SHIM_DIR) + ");"
            "import alert_poller; print('INTERVAL=' + str(alert_poller.POLL_INTERVAL))"
        )
        return subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "POLL_INTERVAL": interval},
            capture_output=True, text=True, timeout=30,
        )

    def test_invalid_falls_back_30_with_warning(self):
        r = self._import_with("abc")
        self.assertEqual(r.returncode, 0, r.stderr)  # import 不抛
        self.assertIn("INTERVAL=30", r.stdout)
        self.assertIn("POLL_INTERVAL", r.stdout)  # warning 打出（不含非法值本身之外的信息）

    def test_valid_value_honored(self):
        r = self._import_with("7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("INTERVAL=7", r.stdout)

    def test_empty_default_30(self):
        r = self._import_with("")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("INTERVAL=30", r.stdout)


class TestSaveState(unittest.TestCase):
    """issue #57 P2-2：save_state 对齐原子写惯例（唯一 tmp + .bak 滚动 + finally）。"""

    def test_atomic_write_with_bak_rollover(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "alert-state.json")
            with mock.patch.object(ap, "STATE_PATH", path):
                ap.save_state({"v": 1})
                with open(path, encoding="utf-8") as f:
                    self.assertEqual(json.load(f), {"v": 1})
                ap.save_state({"v": 2})
                with open(path, encoding="utf-8") as f:
                    self.assertEqual(json.load(f), {"v": 2})
                # .bak 保留上一版（写前备份）
                with open(path + ".bak", encoding="utf-8") as f:
                    self.assertEqual(json.load(f), {"v": 1})
                # 无 tmp 残留（finally 清理）
                self.assertEqual([f for f in os.listdir(td) if ".tmp" in f], [])

    def test_plain_filename_no_dirname(self):
        # STATE_PATH 被 env 覆写为纯文件名（dirname 为空）：makedirs 容错不抛，状态正常落盘
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            os.chdir(td)
            try:
                with mock.patch.object(ap, "STATE_PATH", "alert-state.json"):
                    ap.save_state({"ok": True})
                with open("alert-state.json", encoding="utf-8") as f:
                    self.assertEqual(json.load(f), {"ok": True})
            finally:
                os.chdir(cwd)

    def test_nested_dir_created(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "sub", "dir", "alert-state.json")
            with mock.patch.object(ap, "STATE_PATH", path):
                ap.save_state({"ok": True})
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"ok": True})


if __name__ == "__main__":
    unittest.main()
