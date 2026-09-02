#!/usr/bin/env python3
"""self_api 员工自助端点测试（issue #74）：GET /self/keys。

seam 纪律与 test_alert_poller 同款：鉴权/查询在线路边界 mock（admin_api._introspect /
self_api.query_own_keys），handler 用最小假对象（headers/send_response/wfile），
不 mock 模块内部塑形函数（_shape_key 白名单直接测）。
issue #89：X-Project-ID 项目上下文——默认带头，缺失/非法 400 用例专项覆盖。
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

# 让测试可 import shim 目录下的 self_api（discover 从 shim/tests 启动）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import self_api


_PID = "gid://axonhub/Project/1"  # 与 KEY_PROJECT_ID 默认一致（issue #89：self 平面项目头）


class _FakeHandler:
    """最小 HTTP handler 假对象：捕获状态码与响应体（admin_api._respond 协议）。
    issue #89：默认带 X-Project-ID（控制台常态）；project=None 模拟头缺失。"""

    def __init__(self, path="/self/keys", auth="", project=_PID):
        self.path = path
        self.headers = {}
        if auth:
            self.headers["Authorization"] = auth
        if project:
            self.headers["X-Project-ID"] = project
        self.status = None
        self.wfile = io.BytesIO()

    def send_response(self, code):
        self.status = code

    def send_header(self, *_a):
        pass

    def end_headers(self):
        pass

    def body_json(self):
        return json.loads(self.wfile.getvalue().decode())


class TestHandleRouting(unittest.TestCase):
    def test_non_self_path_passthrough(self):
        h = _FakeHandler(path="/healthz")
        self.assertFalse(self_api.handle(h, "GET"))
        self.assertIsNone(h.status)

    def test_unknown_self_path_404(self):
        h = _FakeHandler(path="/self/other", auth="Bearer t")
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=({"id": "u"}, None)):
            self.assertTrue(self_api.handle(h, "GET"))
        self.assertEqual(h.status, 404)

    def test_post_not_supported(self):
        h = _FakeHandler(auth="Bearer t")
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=({"id": "u"}, None)):
            self.assertTrue(self_api.handle(h, "POST"))
        self.assertEqual(h.status, 404)

    def test_unknown_path_requires_auth_first(self):
        # 先鉴权再分流（评审 P2 对齐 admin 平面）：未知路径无凭据 → 401 而非 404，
        # 未鉴权探测不得用状态码差异区分路由是否存在
        h = _FakeHandler(path="/self/other")
        with mock.patch.object(self_api.admin_api, "_introspect",
                               side_effect=AssertionError("不应被调用")):
            self.assertTrue(self_api.handle(h, "GET"))
        self.assertEqual(h.status, 401)

    def test_post_requires_auth_first(self):
        # 同上：POST /self/keys 无凭据 → 401（内省不应被调用）
        h = _FakeHandler()
        with mock.patch.object(self_api.admin_api, "_introspect",
                               side_effect=AssertionError("不应被调用")):
            self.assertTrue(self_api.handle(h, "POST"))
        self.assertEqual(h.status, 401)

    def test_put_delete_reach_handler(self):
        # app.py do_PUT/do_DELETE 接线后（评审 P2）：已鉴权写方法显式 404，而非通用 404 兜底
        for method in ("PUT", "DELETE"):
            h = _FakeHandler(auth="Bearer t")
            with mock.patch.object(self_api.admin_api, "_introspect", return_value=({"id": "u"}, None)):
                self.assertTrue(self_api.handle(h, method))
            self.assertEqual(h.status, 404, method)


class TestSelfKeysAuth(unittest.TestCase):
    def test_missing_token_401(self):
        h = _FakeHandler()
        with mock.patch.object(self_api.admin_api, "_introspect",
                               side_effect=AssertionError("不应被调用")):
            self.assertTrue(self_api.handle(h, "GET"))
        self.assertEqual(h.status, 401)
        self.assertIn("bearer", h.body_json()["error"])

    def test_introspect_invalid_token_401(self):
        h = _FakeHandler(auth="Bearer bad")
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=(None, 401)):
            self.assertTrue(self_api.handle(h, "GET"))
        self.assertEqual(h.status, 401)

    def test_introspect_unavailable_503(self):
        # 内省不可达 fail-closed 503，不降级（issue #74 实现约定）
        h = _FakeHandler(auth="Bearer t")
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=(None, 503)):
            self.assertTrue(self_api.handle(h, "GET"))
        self.assertEqual(h.status, 503)


class TestSelfKeysQuery(unittest.TestCase):
    def _run(self, keys, me_id="gid://axonhub/User/2"):
        h = _FakeHandler(auth="Bearer emp-token")
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=({"id": me_id}, None)), \
             mock.patch.object(self_api, "query_own_keys", return_value=keys) as qok:
            self.assertTrue(self_api.handle(h, "GET"))
        return h, qok

    def test_empty_list(self):
        h, qok = self._run([])
        self.assertEqual(h.status, 200)
        self.assertEqual(h.body_json(), {"keys": []})
        qok.assert_called_once_with("gid://axonhub/User/2", _PID)

    def test_multi_keys_bound_to_me(self):
        # 多 key 只含本人：查询以 me.id 绑定（服务端 userID 等值过滤），响应白名单字段
        keys = [
            {"name": "employee-self-key", "status": "enabled", "createdAt": "2026-08-01T00:00:00Z",
             "profiles": {"activeProfile": "体验档", "profiles": [{"name": "体验档"}]}},
            {"name": "employee-self-key-2", "status": "archived", "createdAt": "2026-08-10T00:00:00Z",
             "profiles": {"activeProfile": None, "profiles": []}},
        ]
        h, qok = self._run(keys)
        self.assertEqual(h.status, 200)
        self.assertEqual(h.body_json()["keys"], keys)
        qok.assert_called_once_with("gid://axonhub/User/2", _PID)  # 本人 gid + 项目入查询

    def test_query_failure_503(self):
        h = _FakeHandler(auth="Bearer t")
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=({"id": "u"}, None)), \
             mock.patch.object(self_api, "query_own_keys", side_effect=RuntimeError("gql down")):
            self.assertTrue(self_api.handle(h, "GET"))
        self.assertEqual(h.status, 503)

    def test_missing_project_header_400(self):
        # issue #89：无项目上下文（头缺失/非法）→ 400 兜底，不发起查询
        for bad in (None, "2", "gid://axonhub/User/2"):
            h = _FakeHandler(auth="Bearer t", project=bad)
            with mock.patch.object(self_api.admin_api, "_introspect", return_value=({"id": "u"}, None)), \
                 mock.patch.object(self_api, "query_own_keys") as qok:
                self.assertTrue(self_api.handle(h, "GET"))
            self.assertEqual(h.status, 400, bad)
            qok.assert_not_called()


class TestSelfKeyRequests(unittest.TestCase):
    """issue #79：/self/key-requests 鉴权/分流（store/执行细节见 test_key_requests）。"""

    def _handler(self, path="/self/key-requests", auth="Bearer t", body=None):
        h = _FakeHandler(path=path, auth=auth)
        if body is not None:
            raw = json.dumps(body, ensure_ascii=False).encode()
            h.headers["Content-Length"] = str(len(raw))
            h.rfile = io.BytesIO(raw)
        else:
            h.headers["Content-Length"] = "0"
            h.rfile = io.BytesIO(b"")
        return h

    def test_get_own_requests(self):
        h = self._handler()
        me = {"id": "u2", "email": "e@x.com"}
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=(me, None)), \
             mock.patch.object(self_api.key_requests, "list_requests", return_value=[{"id": "kr-1"}]) as lr:
            self.assertTrue(self_api.handle(h, "GET"))
        self.assertEqual(h.status, 200)
        self.assertEqual(h.body_json(), {"requests": [{"id": "kr-1"}]})
        lr.assert_called_once_with(email="e@x.com", project_id=_PID)  # 本人+项目过滤在服务端

    def test_get_requests_missing_project_400(self):
        # issue #89：申请列表同样要项目头，缺失 400 且不调 store
        h = self._handler()
        h.headers.pop("X-Project-ID")
        with mock.patch.object(self_api.admin_api, "_introspect",
                               return_value=({"id": "u2", "email": "e@x.com"}, None)), \
             mock.patch.object(self_api.key_requests, "list_requests") as lr:
            self.assertTrue(self_api.handle(h, "GET"))
        self.assertEqual(h.status, 400)
        lr.assert_not_called()

    def test_get_no_email_502(self):
        # 评审 P1 fail-closed：caller email 缺失时若照常调 list_requests("") 不过滤=返回
        # 全部申请（属主隔离失效）；必须 502 且 list_requests 不被调用
        h = self._handler()
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=({"id": "u2"}, None)), \
             mock.patch.object(self_api.key_requests, "list_requests") as lr:
            self.assertTrue(self_api.handle(h, "GET"))
        self.assertEqual(h.status, 502)
        lr.assert_not_called()

    def test_post_created_201(self):
        h = self._handler(body={"kind": "new", "purpose": "联调"})
        me = {"id": "u2", "email": "e@x.com"}
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=(me, None)), \
             mock.patch.object(self_api.key_requests, "create_request",
                               return_value=({"id": "kr-1", "status": "pending"}, None)) as cr:
            self.assertTrue(self_api.handle(h, "POST"))
        self.assertEqual(h.status, 201)
        cr.assert_called_once_with(me, "new", "联调", "", key_ids=None, project_id=_PID)

    def test_post_missing_project_400(self):
        # issue #89：发起申请无项目头 → 400，不落申请
        h = self._handler(body={"kind": "new", "purpose": "联调"})
        h.headers.pop("X-Project-ID")
        with mock.patch.object(self_api.admin_api, "_introspect",
                               return_value=({"id": "u2", "email": "e"}, None)), \
             mock.patch.object(self_api.key_requests, "create_request") as cr:
            self.assertTrue(self_api.handle(h, "POST"))
        self.assertEqual(h.status, 400)
        cr.assert_not_called()

    def test_post_invalid_400(self):
        h = self._handler(body={"kind": "bogus"})
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=({"id": "u2", "email": "e"}, None)):
            self.assertTrue(self_api.handle(h, "POST"))
        self.assertEqual(h.status, 400)

    def test_post_conflict_409(self):
        h = self._handler(body={"kind": "new", "purpose": "x"})
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=({"id": "u2", "email": "e"}, None)), \
             mock.patch.object(self_api.key_requests, "create_request",
                               return_value=(None, (409, "已有待审批的同类申请"))):
            self.assertTrue(self_api.handle(h, "POST"))
        self.assertEqual(h.status, 409)

    def test_post_no_token_401(self):
        # 先鉴权再分流：新端点同样未鉴权 401、内省不被调用
        h = self._handler(body={"kind": "new", "purpose": "x"})
        h.headers.pop("Authorization")
        with mock.patch.object(self_api.admin_api, "_introspect",
                               side_effect=AssertionError("不应被调用")):
            self.assertTrue(self_api.handle(h, "POST"))
        self.assertEqual(h.status, 401)

    # ---- issue #80：POST /self/key-requests/<id>/cancel 撤回 ----

    def test_cancel_ok_200(self):
        h = self._handler(path="/self/key-requests/kr-1/cancel")
        with mock.patch.object(self_api.admin_api, "_introspect",
                               return_value=({"id": "u2", "email": "e@x.com"}, None)), \
             mock.patch.object(self_api.key_requests, "cancel_request",
                               return_value=({"id": "kr-1", "status": "canceled"}, None)) as cr:
            self.assertTrue(self_api.handle(h, "POST"))
        self.assertEqual(h.status, 200)
        cr.assert_called_once_with("kr-1", "e@x.com")  # 属主判定用内省 email（服务端）

    def test_cancel_no_email_502(self):
        # fail-closed（对齐 #79 评审 P1）：无 email 无法判定归属，直接 502 且不调 store
        h = self._handler(path="/self/key-requests/kr-1/cancel")
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=({"id": "u2"}, None)), \
             mock.patch.object(self_api.key_requests, "cancel_request") as cr:
            self.assertTrue(self_api.handle(h, "POST"))
        self.assertEqual(h.status, 502)
        cr.assert_not_called()

    def test_cancel_404_passthrough(self):
        # 非本人/未找到（key_requests 同码不区分）透传 404
        h = self._handler(path="/self/key-requests/kr-9/cancel")
        with mock.patch.object(self_api.admin_api, "_introspect",
                               return_value=({"id": "u2", "email": "e"}, None)), \
             mock.patch.object(self_api.key_requests, "cancel_request",
                               return_value=(None, (404, "request not found"))):
            self.assertTrue(self_api.handle(h, "POST"))
        self.assertEqual(h.status, 404)

    def test_cancel_no_token_401(self):
        h = self._handler(path="/self/key-requests/kr-1/cancel")
        h.headers.pop("Authorization")
        with mock.patch.object(self_api.admin_api, "_introspect",
                               side_effect=AssertionError("不应被调用")):
            self.assertTrue(self_api.handle(h, "POST"))
        self.assertEqual(h.status, 401)


class TestShapeKey(unittest.TestCase):
    """白名单塑形（issue #81 反转）：key 明文对本人保留下发；其余多余字段（userID/scopes 等）
    仍一律剥掉——属主隔离唯一闸门=查询的 userID=me.id 服务端过滤。"""

    def test_keeps_plaintext_strips_extra(self):
        node = {"id": "gid://axonhub/APIKey/5", "name": "k1", "status": "enabled", "createdAt": "t",
                "profiles": {"activeProfile": "体验档"},
                "key": "ah-plain-visible-to-owner", "userID": "gid://axonhub/User/1", "scopes": ["*"]}
        shaped = self_api._shape_key(node)
        # issue #83：usage 键固定存在（query_own_keys 填充实值；裸 _shape_key 下为 None）
        self.assertEqual(set(shaped), {"id", "name", "key", "status", "createdAt", "profiles", "usage"})
        self.assertEqual(shaped["key"], "ah-plain-visible-to-owner")  # 本人明文可见（issue #81）
        self.assertNotIn("gid://axonhub/User/1", json.dumps(shaped))  # userID 仍剥
        self.assertNotIn("scopes", shaped)

    def test_query_sends_me_id(self):
        # query_own_keys 把 me.id 绑进服务端过滤变量（本人边界由服务端 userID 等值保证）
        captured = {}

        class FakeAx:
            def gql(self, query, variables=None):
                captured["q"] = query
                captured["v"] = variables
                return {"apiKeys": {"edges": []}}

        with mock.patch.object(self_api, "_get_ax", return_value=FakeAx()):
            self.assertEqual(self_api.query_own_keys("gid://axonhub/User/9"), [])
        self.assertEqual(captured["v"], {"uid": "gid://axonhub/User/9", "projectID": _PID})
        self.assertIn("userID", captured["q"])  # 明文下发的唯一闸门：服务端本人过滤
        self.assertIn("projectID", captured["q"])  # issue #89：项目过滤同入服务端查询
        self.assertIn(" key ", captured["q"])  # issue #81：查询取明文字段，本人可见


class TestQueryOwnKeysUsage(unittest.TestCase):
    """issue #83：/self/keys 内嵌本人各档用量（admin token 代查 apiKeyQuotaUsages）。
    闸门：key 集先经 userID=me.id 锁死本人，用量只对本人 key id 逐个查询。"""

    def _fake_ax(self, key_nodes, usages_by_id, fail_ids=()):
        calls = []

        class FakeAx:
            def gql(self, query, variables=None):
                calls.append((query, variables or {}))
                if "apiKeyQuotaUsages" in query:
                    kid = variables["apiKeyId"]
                    if kid in fail_ids:
                        raise RuntimeError("quota query down")
                    return {"apiKeyQuotaUsages": usages_by_id.get(kid, [])}
                return {"apiKeys": {"edges": [{"node": n} for n in key_nodes]}}

        return FakeAx(), calls

    def test_usage_embedded_and_bound_per_key(self):
        # 用量按 key id 逐查内嵌；条目白名单四键（多余字段剥掉）；零用量原样为 0
        nodes = [
            {"id": "gid://axonhub/APIKey/5", "name": "k1", "status": "enabled"},
            {"id": "gid://axonhub/APIKey/6", "name": "k2", "status": "enabled"},
        ]
        usages = {"gid://axonhub/APIKey/5": [
            {"profileName": "体验档", "quota": {"cost": 3}, "window": {"start": "s", "end": "e"},
             "usage": {"requestCount": 0, "totalTokens": 0, "totalCost": 0},
             "internalField": "剥掉"}]}
        fake, calls = self._fake_ax(nodes, usages)
        with mock.patch.object(self_api, "_get_ax", return_value=fake):
            keys = self_api.query_own_keys("gid://axonhub/User/9")
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0]["usage"], [
            {"profileName": "体验档", "quota": {"cost": 3}, "window": {"start": "s", "end": "e"},
             "usage": {"requestCount": 0, "totalTokens": 0, "totalCost": 0}}])
        self.assertEqual(keys[1]["usage"], [])  # 无用量记录 → 空数组；页面按「无当前档条目」显示 —（非 0）
        usage_calls = [v for q, v in calls if "apiKeyQuotaUsages" in q]
        self.assertEqual(usage_calls, [{"apiKeyId": "gid://axonhub/APIKey/5"},
                                       {"apiKeyId": "gid://axonhub/APIKey/6"}])

    def test_usage_failure_degrades_to_null(self):
        # 单 key 用量查询失败置 None，不拖垮列表其余 key（用量是增强展示，列表为主功能）
        nodes = [{"id": "gid://axonhub/APIKey/5", "name": "k1", "status": "enabled"},
                 {"id": "gid://axonhub/APIKey/6", "name": "k2", "status": "enabled"}]
        usages = {"gid://axonhub/APIKey/6": [
            {"profileName": "标准档", "quota": {}, "window": {}, "usage": {}}]}
        fake, _ = self._fake_ax(nodes, usages, fail_ids={"gid://axonhub/APIKey/5"})
        with mock.patch.object(self_api, "_get_ax", return_value=fake):
            keys = self_api.query_own_keys("gid://axonhub/User/9")
        self.assertIsNone(keys[0]["usage"])
        self.assertEqual(len(keys[1]["usage"]), 1)

    def test_me_gate_unchanged_with_usage(self):
        # 本人边界仍在第一条查询的 userID=me.id；用量查询只跟随本人 key id（无用户入参面）
        fake, calls = self._fake_ax([], {})
        with mock.patch.object(self_api, "_get_ax", return_value=fake):
            self.assertEqual(self_api.query_own_keys("gid://axonhub/User/9"), [])
        keys_calls = [v for q, v in calls if "apiKeys(" in q]
        self.assertEqual(keys_calls, [{"uid": "gid://axonhub/User/9", "projectID": _PID}])
        self.assertFalse(any("apiKeyQuotaUsages" in q for q, _ in calls))  # 无 key 不查用量


class TestSelfKeyUsageStats(unittest.TestCase):
    """GET /self/key-usage-stats?key=<gid>&window=day|month|all&tz=<offset_min>：
    本人 key 按时间窗代查 token 用量（apiKeyTokenUsageStats 代查透传，不设限档也有用量可显示）。
    属主闸门：key gid 须 ∈ 本人本项目 key 集（query_own_key_ids），否则 404 不区分存在性。"""

    _KID = "gid://axonhub/APIKey/9"
    _STATS = {"inputTokens": 1200, "outputTokens": 3400, "cachedTokens": 50,
              "reasoningTokens": 10, "topModels": [{"modelId": "gpt-x", "inputTokens": 1}]}

    def _run(self, path, me_id="gid://axonhub/User/2", ids=None, stats=None, stats_exc=None):
        h = _FakeHandler(path=path, auth="Bearer emp-token")
        if ids is None:
            ids = [self._KID]
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=({"id": me_id}, None)), \
             mock.patch.object(self_api, "query_own_key_ids", return_value=ids) as qids, \
             mock.patch.object(self_api, "query_usage_stats",
                               return_value=stats if stats is not None else self._STATS) as qus:
            if stats_exc is not None:
                qus.side_effect = stats_exc
            self.assertTrue(self_api.handle(h, "GET"))
        return h, qids, qus

    def test_day_window_200_whitelist(self):
        h, qids, qus = self._run(f"/self/key-usage-stats?key={self._KID}&window=day")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.body_json()["stats"], self._STATS)
        qids.assert_called_once_with("gid://axonhub/User/2", _PID)
        # 窗口起点 = 今日 00:00（tz 缺省 UTC），ISO8601 带偏移
        args = qus.call_args
        gte = args.kwargs.get("created_at_gte", args[0][1] if len(args[0]) > 1 else None)
        self.assertIsNotNone(gte)
        self.assertTrue(gte.endswith("+00:00"), gte)
        self.assertTrue(gte[11:19] == "00:00:00", gte)

    def test_month_window_first_day(self):
        h, _, qus = self._run(f"/self/key-usage-stats?key={self._KID}&window=month&tz=-480")
        self.assertEqual(h.status, 200)
        gte = qus.call_args.kwargs["created_at_gte"]
        self.assertTrue(gte[8:10] == "01" and gte[11:19] == "00:00:00", gte)
        self.assertTrue(gte.endswith("+08:00"), gte)  # tz=-480（getTimezoneOffset 语义）→ UTC+8

    def test_all_window_no_gte(self):
        h, _, qus = self._run(f"/self/key-usage-stats?key={self._KID}&window=all")
        self.assertEqual(h.status, 200)
        self.assertIsNone(qus.call_args.kwargs["created_at_gte"])

    def test_other_key_404(self):
        # gid 不在本人 key 集 → 404（不区分存在性），代查不发起
        h, _, qus = self._run("/self/key-usage-stats?key=gid://axonhub/APIKey/666&window=day")
        self.assertEqual(h.status, 404)
        qus.assert_not_called()

    def test_invalid_window_400(self):
        h, _, qus = self._run(f"/self/key-usage-stats?key={self._KID}&window=year")
        self.assertEqual(h.status, 400)
        qus.assert_not_called()

    def test_missing_key_param_400(self):
        h, _, qus = self._run("/self/key-usage-stats?window=day")
        self.assertEqual(h.status, 400)
        qus.assert_not_called()

    def test_missing_project_header_400(self):
        h = _FakeHandler(path=f"/self/key-usage-stats?key={self._KID}&window=day",
                         auth="Bearer t", project=None)
        with mock.patch.object(self_api.admin_api, "_introspect", return_value=({"id": "u"}, None)), \
             mock.patch.object(self_api, "query_own_key_ids") as qids:
            self.assertTrue(self_api.handle(h, "GET"))
        self.assertEqual(h.status, 400)
        qids.assert_not_called()

    def test_stats_failure_503(self):
        h, _, _ = self._run(f"/self/key-usage-stats?key={self._KID}&window=day",
                            stats_exc=RuntimeError("gql down"))
        self.assertEqual(h.status, 503)


class TestUsageStatsQuery(unittest.TestCase):
    """query_usage_stats 白名单塑形 + 空窗兜底（零值条）。"""

    def test_whitelist_shape_and_zero_fallback(self):
        calls = []

        class FakeAx:
            def gql(self, query, variables=None):
                calls.append((query, variables or {}))
                return {"apiKeyTokenUsageStats": [
                    {"apiKeyId": "gid://axonhub/APIKey/9", "inputTokens": 5, "outputTokens": 7,
                     "cachedTokens": 1, "reasoningTokens": 0, "topModels": [],
                     "internalField": "剥掉"}]}

        with mock.patch.object(self_api, "_get_ax", return_value=FakeAx()):
            s = self_api.query_usage_stats("gid://axonhub/APIKey/9", created_at_gte="2026-09-02T00:00:00+08:00")
        self.assertEqual(s, {"inputTokens": 5, "outputTokens": 7, "cachedTokens": 1,
                             "reasoningTokens": 0, "topModels": []})
        self.assertEqual(calls[0][1], {"input": {"apiKeyIds": ["gid://axonhub/APIKey/9"],
                                                 "createdAtGTE": "2026-09-02T00:00:00+08:00"}})

        class EmptyAx:
            def gql(self, query, variables=None):
                return {"apiKeyTokenUsageStats": []}

        with mock.patch.object(self_api, "_get_ax", return_value=EmptyAx()):
            s = self_api.query_usage_stats("gid://axonhub/APIKey/9")
        self.assertEqual(s, {"inputTokens": 0, "outputTokens": 0, "cachedTokens": 0,
                             "reasoningTokens": 0, "topModels": []})


class TestWindowGte(unittest.TestCase):
    """_window_gte 窗口界：day=今日 00:00 / month=本月 1 号 00:00（tz 偏移）/ all=None。"""

    def test_day_month_all(self):
        import datetime
        self.assertIsNone(self_api._window_gte("all", 0))
        day = self_api._window_gte("day", 0)
        self.assertTrue(day.endswith("+00:00") and day[11:19] == "00:00:00", day)
        month = self_api._window_gte("month", -480)
        self.assertTrue(month.endswith("+08:00") and month[8:10] == "01", month)
        # tz 语义校验：UTC+8 的今天 00:00 = UTC 昨天 16:00
        tz8 = datetime.timezone(datetime.timedelta(hours=8))
        expect = datetime.datetime.now(tz8).replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertEqual(day[:10], datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"))


if __name__ == "__main__":
    unittest.main()
