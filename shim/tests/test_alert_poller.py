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
issue #87 增补：Default 解析 fail-closed（按名命中不依赖顺序、缺失时不加人不发通知只记日志）。
issue #89 增补：项目参数化 helper（query_user_projects 形状、ensure_emp_key/apply_tier 的
projectID 透传与 Default 兜底）；apply_tier 的 USER_ENABLED_KEYS_QUERY 断言随项目过滤更新。
issue #91 增补：P2-2 飞书新建成员校验（非成员不建 Key+回执文本）；P2-3 额度告警文本带
项目名（含 myProjects 失败回退 gid）；P2-4 自动入项按 gid 匹配（改名仍命中/缺失跳过）。
"""
import json
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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


class TestLoadTierProfile(unittest.TestCase):
    """issue #83：load_tier_profile 全字段透传锁死——模板 profile 的 channelIDs/
    channelTags/channelTagsMatchMode/modelIDs/loadBalanceStrategy 一个不落（#81 丢
    modelMappings 落库 null 的同款教训；档位渠道允许列表经模板下发，丢字段=换档
    静默丢限制）。"""

    def _load(self, tpl_profile):
        class FakeAx:
            def gql(self, query, variables=None):
                tpl = {"name": "体验档", "profile": tpl_profile}
                return {"apiKeyProfileTemplates": {"edges": [{"node": tpl}]}}

        return ap.load_tier_profile(FakeAx(), "体验档")

    def test_full_field_passthrough(self):
        # 模板带全量实值时逐字段原样透传（quota.cost 转 str 为既有约定，前端 zod coerce）
        tpl_profile = {
            "modelMappings": [{"from": "gpt-4o", "to": "deepseek-v4"}],
            "channelIDs": [2, 4, 7],
            "channelTags": ["sub"],
            "channelTagsMatchMode": "all",
            "modelIDs": ["k3"],
            "loadBalanceStrategy": "round_robin",
            "quota": {"requests": 7, "totalTokens": 150000000, "cost": 100,
                      "period": {"type": "calendar_duration", "pastDuration": None,
                                 "calendarDuration": {"unit": "month"}}},
        }
        prof = self._load(tpl_profile)
        for field in ("channelIDs", "channelTags", "channelTagsMatchMode", "modelIDs",
                      "loadBalanceStrategy", "modelMappings"):
            self.assertEqual(prof[field], tpl_profile[field], field)
        self.assertEqual(prof["quota"]["requests"], 7)
        self.assertEqual(prof["quota"]["totalTokens"], 150000000)
        self.assertEqual(prof["quota"]["cost"], "100")
        self.assertEqual(prof["quota"]["period"]["calendarDuration"], {"unit": "month"})
        self.assertIsNone(prof["quota"]["period"]["pastDuration"])  # 键存在且原样为 None

    def test_past_duration_passthrough(self):
        # 评审 P2：period.pastDuration 为本次新拉字段，实值必须透传（非 calendar 模板直挂）
        tpl_profile = {"modelMappings": [],
                       "quota": {"requests": 500, "totalTokens": None, "cost": None,
                                 "period": {"type": "past_duration",
                                            "pastDuration": {"value": 12, "unit": "hour"},
                                            "calendarDuration": None}}}
        prof = self._load(tpl_profile)
        self.assertEqual(prof["quota"]["period"]["type"], "past_duration")
        self.assertEqual(prof["quota"]["period"]["pastDuration"], {"value": 12, "unit": "hour"})
        self.assertIsNone(prof["quota"]["period"]["calendarDuration"])

    def test_empty_optional_fields_pass_as_null(self):
        # 模板限制字段为空（标准/高档有意全开）：null 原样透传，不臆造空数组/默认值——
        # 落库与模板语义一致（null=不限制）；channelTagsMatchMode 唯一例外落 'any'
        # （对齐前端 zod 缺省，且 'any' 搭配空 channelTags 本就不限制任何渠道）
        tpl_profile = {"modelMappings": [], "channelIDs": None, "channelTags": None,
                       "channelTagsMatchMode": None, "modelIDs": None, "loadBalanceStrategy": None,
                       "quota": {"requests": None, "totalTokens": 750000000, "cost": 500,
                                 "period": {"type": "calendar_duration", "calendarDuration": {"unit": "month"}}}}
        prof = self._load(tpl_profile)
        self.assertIsNone(prof["channelIDs"])
        self.assertIsNone(prof["channelTags"])
        self.assertIsNone(prof["modelIDs"])
        self.assertIsNone(prof["loadBalanceStrategy"])
        self.assertEqual(prof["channelTagsMatchMode"], "any")
        self.assertEqual(prof["modelMappings"], [])

    def test_template_missing_returns_none(self):
        class FakeAx:
            def gql(self, query, variables=None):
                return {"apiKeyProfileTemplates": {"edges": []}}

        self.assertIsNone(ap.load_tier_profile(FakeAx(), "不存在的档"))


class TestTierRank(unittest.TestCase):
    """issue #85：档位秩次单一定义点（体验档<标准档<高档）+ key 档名纯函数。"""

    def test_rank_order(self):
        self.assertLess(ap.TIER_RANK["体验档"], ap.TIER_RANK["标准档"])
        self.assertLess(ap.TIER_RANK["标准档"], ap.TIER_RANK["高档"])

    def test_key_tier_name(self):
        self.assertEqual(ap.key_tier_name({"profiles": {"activeProfile": "高档"}}), "高档")
        self.assertIsNone(ap.key_tier_name({"profiles": {"activeProfile": None}}))
        self.assertIsNone(ap.key_tier_name({"profiles": None}))
        self.assertIsNone(ap.key_tier_name({}))


class TestApplyTierToUserGuard(unittest.TestCase):
    """issue #85 执行侧 never-downgrade（控制台/飞书两通道同享）：目标秩 > 当前秩换挂；
    == 同档重挂仍执行（模板数值调整后刷新存量快照的运维路径）；< 跳过并在结果文本如实列出；
    全部跳过返回「未变更」文本。查询换按 userID 精确过滤的新查询（不再复用巡检共用查询）。"""

    def _run(self, own_keys, tier_name="标准档", key_ids=None):
        mutations = []
        key_query_vars = []

        class FakeAx:
            def gql(self, query, variables=None):
                if query.startswith("mutation"):
                    mutations.append(variables)
                    return {"updateAPIKeyProfiles": {"id": variables["id"]}}
                if "apiKeyProfileTemplates" in query:
                    tpl = {"name": tier_name, "profile": {"modelMappings": [], "quota": {}}}
                    return {"apiKeyProfileTemplates": {"edges": [{"node": tpl}]}}
                key_query_vars.append(variables)  # USER_ENABLED_KEYS_QUERY
                return {"apiKeys": {"edges": [{"node": k} for k in own_keys]}}

        user = {"id": "gid://axonhub/User/9", "email": "u9@x.com"}
        text = ap.apply_tier_to_user(FakeAx(), user, tier_name, key_ids=key_ids)
        return text, mutations, key_query_vars

    @staticmethod
    def _key(kid, name, tier):
        return {"id": kid, "name": name, "userID": "gid://axonhub/User/9",
                "profiles": {"activeProfile": tier}}

    def test_mixed_tiers_skip_downgrade(self):
        # 混档（高档 + 体验）目标标准 → 高档 key 跳过、体验档 key 升，结果文本如实列出两侧
        keys = [self._key("k1", "k-prem", "高档"), self._key("k2", "k-trial", "体验档")]
        text, mutations, qvars = self._run(keys)
        self.assertEqual([m["id"] for m in mutations], ["k2"])  # 只有体验档 key 收到换挂 mutation
        self.assertEqual(mutations[0]["input"]["activeProfile"], "标准档")
        self.assertIn("已将 1 个 Key 换挂 标准档（k-trial）", text)
        self.assertIn("跳过 1 个", text)
        self.assertIn("k-prem（当前高档）", text)
        # #89 按 userID+projectID 过滤（uid 变量下发），不再拉全量 enabled 列表
        self.assertEqual(qvars, [{"uid": "gid://axonhub/User/9", "projectID": ap.KEY_PROJECT_ID}])

    def test_same_tier_reapply_still_runs(self):
        # 同档重挂仍执行（保留运维刷新路径）
        text, mutations, _ = self._run([self._key("k1", "k-std", "标准档")])
        self.assertEqual(len(mutations), 1)
        self.assertIn("已将 1 个 Key 换挂 标准档（k-std）", text)

    def test_all_skipped_returns_no_change_text(self):
        text, mutations, _ = self._run([self._key("k1", "k-prem", "高档")])
        self.assertEqual(mutations, [])
        self.assertIn("未变更", text)
        self.assertIn("k-prem（当前高档）", text)

    def test_unprofiled_key_attaches_directly(self):
        # 未挂档 key 无秩次可比，直挂目标档
        text, mutations, _ = self._run([self._key("k1", "k-none", None)])
        self.assertEqual(len(mutations), 1)
        self.assertIn("换挂", text)

    def test_no_enabled_key_text_unchanged(self):
        text, mutations, _ = self._run([])
        self.assertIn("名下无 enabled Key", text)
        self.assertEqual(mutations, [])

    # ---- issue #86：key_ids 子集化（控制台按 Key 勾选；None=全部 enabled 原语义）----

    def test_subset_only_selected_mutated(self):
        # 两把体验档 key 只勾选 k2 → 仅 k2 收到换挂 mutation，k1 不动
        keys = [self._key("k1", "k-a", "体验档"), self._key("k2", "k-b", "体验档")]
        text, mutations, _ = self._run(keys, key_ids=["k2"])
        self.assertEqual([m["id"] for m in mutations], ["k2"])
        self.assertIn("已将 1 个 Key 换挂 标准档（k-b）", text)

    def test_subset_missing_key_listed_and_skipped(self):
        # 审批期间被归档/删除的所选 Key（不在 enabled 集）→ 跳过并在结果文本按 id 列明
        keys = [self._key("k1", "k-a", "体验档")]
        text, mutations, _ = self._run(keys, key_ids=["k1", "k-gone"])
        self.assertEqual([m["id"] for m in mutations], ["k1"])
        self.assertIn("已不可用", text)
        self.assertIn("k-gone", text)

    def test_subset_all_missing_no_change(self):
        # 所选全部已不可用 → 未变更、零 mutation
        text, mutations, _ = self._run([self._key("k1", "k-a", "体验档")], key_ids=["k-gone"])
        self.assertEqual(mutations, [])
        self.assertIn("未变更", text)
        self.assertIn("k-gone", text)

    def test_subset_mixed_three_states(self):
        # 子集混档（高档+体验）目标标准 + 未勾选同档 key k3 → k2 升、k1 跳过、k3 不动
        keys = [self._key("k1", "k-prem", "高档"), self._key("k2", "k-trial", "体验档"),
                self._key("k3", "k-untouched", "体验档")]
        text, mutations, _ = self._run(keys, key_ids=["k1", "k2"])
        self.assertEqual([m["id"] for m in mutations], ["k2"])
        self.assertIn("k-trial", text)
        self.assertIn("k-prem（当前高档）", text)
        self.assertNotIn("k-untouched", text)

    def test_template_missing_text_unchanged(self):
        class FakeAx:
            def gql(self, query, variables=None):
                return {"apiKeyProfileTemplates": {"edges": []}}

        text = ap.apply_tier_to_user(FakeAx(), {"id": "u", "email": "u@x.com"}, "不存在的档")
        self.assertEqual(text, "找不到 不存在的档 Profile 模板")


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

    def _handler(self, user=True, existing_key=None, member=True):
        tpl = {"name": "体验档", "profile": {"modelMappings": [], "quota": {"requests": None, "totalTokens": 150000000,
               "cost": 100, "period": {"type": "calendar_duration", "calendarDuration": {"unit": "month"}}}}}

        def handler(query, variables):
            if "projects(" in query:
                # issue #91 P2-2 成员校验（USER_PROJECTS_QUERY，users 子查 projects）：
                # member=False 模拟申请人已被移出 Default 项目
                projs = [{"node": {"id": ap.KEY_PROJECT_ID, "name": "Default"}}] if member else []
                return {"users": {"edges": [{"node": {"id": "gid://axonhub/User/7",
                                                     "projects": {"edges": projs}}}]}}
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
        # issue #81：modelMappings 必须落库为 []（此前丢字段落 null，前端 zod 解析崩）
        self.assertEqual(prof_vars["input"]["profiles"][0]["modelMappings"], [])

    def test_model_mappings_passthrough(self):
        # issue #81：模板带实值 modelMappings 时原样透传（不丢字段、不改写）
        mappings = [{"from": "gpt-4o", "to": "deepseek-v4"}]

        def handler(query, variables):
            h = self._handler()
            if "apiKeyProfileTemplates" in query:
                t = {"name": "体验档", "profile": {"modelMappings": mappings, "quota": {}}}
                return {"apiKeyProfileTemplates": {"edges": [{"node": t}]}}
            return h(query, variables)

        _, calls, _, _ = self._run(handler)
        prof_vars = next(v for q, v in calls if "updateAPIKeyProfiles" in q)
        self.assertEqual(prof_vars["input"]["profiles"][0]["modelMappings"], mappings)

    def test_user_missing_no_create(self):
        result, calls, assign, dm = self._run(self._handler(user=False))
        self.assertIn("未首登", result)
        self.assertFalse(any("createAPIKey" in q for q, _ in calls))
        assign.assert_not_called()
        dm.assert_not_called()

    def test_non_member_no_create(self):
        # issue #91 P2-2：申请人已被移出 Default 项目 → 不建 Key，回执说明（正常终态文本，
        # 不抛异常 → approval_sync 标记已处理不重试），与「无用户」先例同语义
        result, calls, assign, dm = self._run(self._handler(member=False))
        self.assertIn("已不在 Default 项目", result)
        self.assertIn("未建 Key", result)
        self.assertFalse(any("createAPIKey" in q for q, _ in calls))
        assign.assert_not_called()
        dm.assert_not_called()

    def test_dm_failure_fallback_self_keys_page(self):
        # issue #81：私信未送达的兜底从「尾号+联系管理员」改为「我的 Key 页查看明文」——
        # 结果摘要仍绝不含明文
        result, _, _, _ = self._run(self._handler(), dm_ok=False)
        self.assertIn("私信未送达", result)
        self.assertIn("我的 Key", result)
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


class TestProjectScopedHelpers(unittest.TestCase):
    """issue #89 增补：项目参数化 helper——query_user_projects 形状（含用户不存在）、
    ensure_emp_key 的 projectID 透传（同名查找/创建同项目）与 Default 兜底、
    apply_tier_to_user 把 project_id 传进 enabled key 查询。"""

    _P2 = "gid://axonhub/Project/2"

    def test_query_user_projects_shape(self):
        ax = mock.Mock()
        ax.gql = mock.Mock(return_value={"users": {"edges": [{"node": {
            "id": "gid://axonhub/User/2",
            "projects": {"edges": [{"node": {"id": "gid://axonhub/Project/1", "name": "Default"}},
                                   {"node": {"id": self._P2, "name": "P-Test2"}}]}}}]}})
        projs = ap.query_user_projects(ax, "gid://axonhub/User/2")
        self.assertEqual(projs, [{"id": "gid://axonhub/Project/1", "name": "Default"},
                                 {"id": self._P2, "name": "P-Test2"}])
        self.assertEqual(ax.gql.call_args[0][1], {"uid": "gid://axonhub/User/2"})

    def test_query_user_projects_user_missing(self):
        ax = mock.Mock()
        ax.gql = mock.Mock(return_value={"users": {"edges": []}})
        self.assertEqual(ap.query_user_projects(ax, "gid://axonhub/User/404"), [])

    def _ensure_ax(self, seen):
        """ensure_emp_key 线路边界假 gql：记录同名查找与创建的变量。"""
        tpl = {"name": "体验档", "profile": {"modelMappings": [], "quota": {}}}

        def fake_gql(query, variables=None):
            if "apiKeys(" in query:
                seen["lookup"] = variables
                return {"apiKeys": {"edges": []}}
            if "createAPIKey" in query:
                seen["create"] = variables
                return {"createAPIKey": {"id": "gid://axonhub/APIKey/9", "name": "n", "key": "ah-x"}}
            if "apiKeyProfileTemplates" in query:
                return {"apiKeyProfileTemplates": {"edges": [{"node": tpl}]}}
            if "updateAPIKeyProfiles" in query:
                return {"updateAPIKeyProfiles": {"id": "x"}}
            raise AssertionError(f"unexpected gql: {query[:60]}")

        ax = mock.Mock()
        ax.gql = fake_gql
        return ax

    def test_ensure_emp_key_project_id_passthrough(self):
        # 指定项目：同名查找与 createAPIKey 同落该项目（跨项目同名不串）
        seen = {}
        with mock.patch.object(ap, "assign_key_owner"):
            ap.ensure_emp_key(self._ensure_ax(seen), {"id": "gid://axonhub/User/7"},
                              "seed", "联调", "20260822", "tail-1", project_id=self._P2)
        self.assertEqual(seen["lookup"]["projectID"], self._P2)
        self.assertEqual(seen["create"]["input"]["projectID"], self._P2)

    def test_ensure_emp_key_default_project_fallback(self):
        # 不传 project_id = Default 常量（飞书通道/存量申请零变化）
        seen = {}
        with mock.patch.object(ap, "assign_key_owner"):
            ap.ensure_emp_key(self._ensure_ax(seen), {"id": "gid://axonhub/User/7"},
                              "seed", "", "20260822", "t")
        self.assertEqual(seen["lookup"]["projectID"], ap.KEY_PROJECT_ID)
        self.assertEqual(seen["create"]["input"]["projectID"], ap.KEY_PROJECT_ID)

    def test_apply_tier_passes_project_id(self):
        # 执行侧换挂的 enabled key 评估集限定（user, project_id）
        with mock.patch.object(ap, "load_tier_profile", return_value={"modelMappings": [], "quota": {}}), \
             mock.patch.object(ap, "query_user_enabled_keys", return_value=[]) as q:
            text = ap.apply_tier_to_user(mock.Mock(), {"id": "gid://axonhub/User/7", "email": "e@x"},
                                         "标准档", project_id=self._P2)
        self.assertEqual(q.call_args[0][2], self._P2)
        self.assertIn("无 enabled Key", text)


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

    def test_default_not_first_still_picked(self):
        # issue #87 回归：Default 不在 myProjects 首位也按名命中（不依赖列表顺序）
        sends, adds = self._run([self._user(9, "new@x")], my_projects=[
            {"id": "gid://axonhub/Project/2", "name": "P-Test"},
            {"id": "gid://axonhub/Project/1", "name": "Default"}])
        self.assertEqual([a["projectId"] for a in adds], ["gid://axonhub/Project/1"])
        self.assertEqual(len(sends), 1)
        self.assertIn("自动加入 Default 项目", sends[0])

    def test_default_missing_fail_closed(self):
        # issue #87：Default 被删除 → 不加任何项目、不发通知、只记日志（不回退 projs[0]）
        # issue #91 P2-4：日志按 gid 报（匹配键从项目名改为 KEY_PROJECT_ID）
        with redirect_stdout(io.StringIO()) as buf:
            sends, adds = self._run([self._user(9, "new@x")], my_projects=[
                {"id": "gid://axonhub/Project/2", "name": "P-Test"}])
        self.assertEqual(adds, [])
        self.assertEqual(sends, [])
        self.assertIn(f"未找到 {ap.KEY_PROJECT_ID}", buf.getvalue())

    def test_renamed_default_still_matched_by_gid(self):
        # issue #91 P2-4：Default 改名后按 gid 仍命中（按名匹配时此处会静默停摆），
        # 通知文本按匹配到的项目实名发出
        sends, adds = self._run([self._user(9, "new@x")], my_projects=[
            {"id": "gid://axonhub/Project/1", "name": "主项目（已改名）"}])
        self.assertEqual([a["projectId"] for a in adds], ["gid://axonhub/Project/1"])
        self.assertEqual(len(sends), 1)
        self.assertIn("主项目（已改名）", sends[0])


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


class TestCheckCycleProjectLabel(unittest.TestCase):
    """issue #91 P2-3：apikey 额度告警/预警/恢复文本带项目名（一轮一次 myProjects 建
    gid→名映射）；名单查询失败回退裸 gid，不阻塞告警主流程。"""

    P2 = "gid://axonhub/Project/2"

    def _cycle(self, state, cost_used, proj_names_ok=True):
        sends = []
        usage = {"profileName": "体验档",
                 "quota": {"requests": None, "totalTokens": None, "cost": 100},
                 "usage": {"requestCount": 0, "totalTokens": 0, "totalCost": cost_used}}

        def fake_gql(query, variables=None):
            if "myProjects" in query:
                if not proj_names_ok:
                    raise RuntimeError("gql down")
                return {"myProjects": [{"id": "gid://axonhub/Project/1", "name": "Default"},
                                       {"id": self.P2, "name": "P-Test2"}]}
            if "queryChannels" in query:
                return {"queryChannels": {"edges": []}}
            if "apiKeyQuotaUsages" in query:
                return {"apiKeyQuotaUsages": [usage]}
            if "apiKeys(" in query:
                return {"apiKeys": {"edges": [{"node": {"id": "gid://axonhub/APIKey/9",
                        "name": "emp-k", "userID": "gid://axonhub/User/2", "projectID": self.P2}}]}}
            raise AssertionError(f"unexpected gql: {query[:50]}")

        ax = mock.Mock()
        ax.gql = fake_gql
        with mock.patch.object(ap, "http_get", return_value=True), \
             mock.patch.object(ap, "send_feishu", side_effect=lambda t: sends.append(t) or True):
            new_state = ap.check_cycle(ax, state)
        return new_state, sends

    def test_alert_and_recover_texts_have_project_name(self):
        # 耗尽告警 + 恢复 两类文本都带项目名
        state, sends = self._cycle({}, 150)
        alert = next(t for t in sends if "额度耗尽" in t)
        self.assertIn("项目: P-Test2", alert)
        state, sends = self._cycle(state, 10)  # 新周期用量回落 → 恢复
        recover = next(t for t in sends if "已重置" in t)
        self.assertIn("项目 P-Test2", recover)

    def test_near_warning_text_has_project_name(self):
        _, sends = self._cycle({}, 85)  # ≥80% 未耗尽 → 预警
        warn = next(t for t in sends if "额度将尽" in t)
        self.assertIn("项目: P-Test2", warn)

    def test_projects_query_failure_fallback_gid(self):
        # myProjects 查询失败：告警照发，项目位回退裸 gid
        _, sends = self._cycle({}, 150, proj_names_ok=False)
        alert = next(t for t in sends if "额度耗尽" in t)
        self.assertIn(f"项目: {self.P2}", alert)


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
