#!/usr/bin/env python3
"""GraphQL 受限访问门测试（2026-09-03）：/graphql-authz extAuthz 端点。

背景：beta6 上游 RequestExecution/ChannelProbe/ProviderQuotaStatus/UserRole 四实体未挂
ent policy（node(id:) 任意 JWT 跨项目直读，活栈实证零权限员工读到非成员项目
requestBody 原文），channelProbeData/checkProviderQuotas 两操作无 scope 校验。
网关在 /admin/graphql 补 extAuthz fail-closed 门。seam 纪律同 test_playground_authz.py：
活 shim Handler 起本地端口，内省经 mock.patch 替换 admin_api._introspect（不打真网络）。
"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import admin_api
import app as shim_app


def _start(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


_SHIM = _start(shim_app.Handler)
_BASE = f"http://127.0.0.1:{_SHIM.server_address[1]}"

_NODE_EXEC = ('{"query":"query { node(id: \\"gid://axonhub/RequestExecution/13939\\") '
              '{ id ... on RequestExecution { requestBody } } }"}')
_NODE_EXEC_VARS = json.dumps({
    "query": "query N($id: ID!) { node(id: $id) { id ... on RequestExecution { requestBody } } }",
    "variables": {"id": "gid://axonhub/RequestExecution/13939"},
})
_SAFE_NODE_REQUEST = ('{"query":"query D($id: ID!) { node(id: $id) { ... on Request { id } } }",'
                      '"variables":{"id":"gid://axonhub/Request/13936"}}')
_SAFE_ME = '{"query":"query Me { me { id email isOwner scopes } }"}'
_PROBE = '{"query":"query P($input: ChannelProbeInput!) { channelProbeData(input: $input) { channelID } }","variables":{"input":{"channelID":"1"}}}'
_QUOTA = '{"query":"mutation { checkProviderQuotas }"}'


def _post_authz(body=_SAFE_ME, token="t"):
    req = urllib.request.Request(_BASE + "/graphql-authz",
                                 data=body.encode() if isinstance(body, str) else body,
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _me(owner=False, scopes=(), projects=()):
    return {"id": "gid://axonhub/User/1", "email": "u@x", "isOwner": owner,
            "scopes": list(scopes),
            "projects": [{"projectID": p, "scopes": list(s)} for p, s in projects]}


class GraphqlRequiredScopesPureTest(unittest.TestCase):
    """graphql_required_scopes 纯函数：受限模式识别与误报面。"""

    def test_safe_queries_pass(self):
        self.assertEqual(admin_api.graphql_required_scopes(_SAFE_ME), [])
        self.assertEqual(admin_api.graphql_required_scopes(_SAFE_NODE_REQUEST), [])
        self.assertEqual(admin_api.graphql_required_scopes(""), [])
        self.assertEqual(admin_api.graphql_required_scopes(None), [])

    def test_policy_covered_type_gids_not_flagged(self):
        # 控制台日常 node(id:) 全在有 policy 的类型上，不得误判
        for t in ("Request", "UsageLog", "Channel", "APIKey", "Project", "Thread"):
            body = f'{{"query":"{{ node(id: \\"gid://axonhub/{t}/1\\") {{ id }} }}"}}'
            self.assertEqual(admin_api.graphql_required_scopes(body), [], t)

    def test_unprotected_type_gids_flagged(self):
        cases = {
            "RequestExecution": "read_requests",
            "ChannelProbe": "read_channels",
            "ProviderQuotaStatus": "read_channels",
            "UserRole": "read_roles",
        }
        for t, scope in cases.items():
            body = f'{{"query":"{{ node(id: \\"gid://axonhub/{t}/1\\") {{ id }} }}"}}'
            self.assertEqual(admin_api.graphql_required_scopes(body), [scope], t)

    def test_gid_in_variables_flagged(self):
        # id 走 variables 也必须命中（node 实参只认字面 gid，无编码绕行）
        self.assertEqual(admin_api.graphql_required_scopes(_NODE_EXEC_VARS), ["read_requests"])

    def test_ops_flagged(self):
        self.assertEqual(admin_api.graphql_required_scopes(_PROBE), ["read_channels"])
        self.assertEqual(admin_api.graphql_required_scopes(_QUOTA), ["write_channels"])

    def test_combo_needs_all(self):
        combo = _NODE_EXEC[:-1] + ',"q2":' + json.dumps(_QUOTA) + '}'
        self.assertEqual(admin_api.graphql_required_scopes(combo), ["read_requests", "write_channels"])

    def test_word_boundary_no_false_positive(self):
        # 形似但非目标字段（如变量名/别名带前缀后缀）不命中
        self.assertEqual(admin_api.graphql_required_scopes('{"query":"{ mychannelProbeDataX }"}'), [])
        self.assertEqual(admin_api.graphql_required_scopes('{"query":"{ xcheckProviderQuotasy }"}'), [])


class GraphqlAuthzAllowedPureTest(unittest.TestCase):
    """graphql_authz_allowed 纯函数：owner 直通 / 系统 scope 精确匹配 / "*" 不通配。"""

    def test_owner(self):
        self.assertTrue(admin_api.graphql_authz_allowed(_me(owner=True), ["read_requests"]))

    def test_system_scope(self):
        self.assertTrue(admin_api.graphql_authz_allowed(_me(scopes=["read_requests"]), ["read_requests"]))

    def test_missing_scope(self):
        self.assertFalse(admin_api.graphql_authz_allowed(_me(scopes=["read_channels"]), ["read_requests"]))

    def test_project_level_not_honored(self):
        # node 直读不带项目上下文：项目级 read_requests 刻意不放行
        me = _me(projects=[("gid://axonhub/Project/1", ["read_requests", "write_requests"])])
        self.assertFalse(admin_api.graphql_authz_allowed(me, ["read_requests"]))

    def test_wildcard_not_honored(self):
        self.assertFalse(admin_api.graphql_authz_allowed(_me(scopes=["*"]), ["read_requests"]))

    def test_multi_requires_all(self):
        need = ["read_requests", "write_channels"]
        self.assertFalse(admin_api.graphql_authz_allowed(_me(scopes=["read_requests"]), need))
        self.assertTrue(admin_api.graphql_authz_allowed(_me(scopes=["read_requests", "write_channels"]), need))


class GraphqlAuthzEndpointTest(unittest.TestCase):
    """端点行为：常规查询零内省放行、受限模式内省三通道、fail-closed 拒绝面。"""

    def _with_me(self, me=None, err=None):
        return mock.patch.object(admin_api, "_introspect",
                                 return_value=(me, err) if err is None else (None, err))

    def test_safe_query_200_no_introspect(self):
        with self._with_me(me=_me(owner=True)) as m:
            status, _ = _post_authz(_SAFE_ME)
            status2, _ = _post_authz(_SAFE_NODE_REQUEST)
        self.assertEqual((status, status2), (200, 200))
        m.assert_not_called()  # 常规流量不付内省成本

    def test_missing_body_403(self):
        req = urllib.request.Request(_BASE + "/graphql-authz", data=b"", method="POST")
        req.add_header("Authorization", "Bearer t")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 403)

    def test_oversized_body_403(self):
        # Content-Length 声明超上限即拒（杜绝「危险字段推到截断点之后」绕行；网关
        # allowPartialMessage=false 先拒，本端兜底）。服务端读完头就 403 关连接，
        # urllib 边发 1 MiB 体边被断会 BrokenPipe——原始 socket 只发头读响应行。
        import socket
        with socket.create_connection(("127.0.0.1", _SHIM.server_address[1]), timeout=5) as s:
            s.sendall(b"POST /graphql-authz HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                      b"Authorization: Bearer t\r\nContent-Length: "
                      + str(shim_app.MAX_GRAPHQL_AUTHZ_BODY + 1).encode() + b"\r\n\r\n")
            resp = s.recv(4096)
        self.assertTrue(resp.split(b"\r\n", 1)[0].startswith(b"HTTP/1.1 403") or b" 403 " in resp.split(b"\r\n", 1)[0], resp[:100])

    def test_get_403(self):
        req = urllib.request.Request(_BASE + "/graphql-authz", method="GET")
        req.add_header("Authorization", "Bearer t")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 403)

    def test_restricted_missing_token_403(self):
        status, _ = _post_authz(_NODE_EXEC, token=None)
        self.assertEqual(status, 403)

    def test_restricted_owner_allowed(self):
        with self._with_me(me=_me(owner=True)):
            status, _ = _post_authz(_NODE_EXEC)
        self.assertEqual(status, 200)

    def test_restricted_system_scope_allowed(self):
        with self._with_me(me=_me(scopes=["read_requests"])):
            status, _ = _post_authz(_NODE_EXEC_VARS)
        self.assertEqual(status, 200)

    def test_restricted_zero_scope_employee_denied(self):
        # 核心面：项目成员但系统空 scopes 的邀请注册用户 → 跨项目 node 直读被拒
        with self._with_me(me=_me(projects=[("gid://axonhub/Project/1", ["read_requests", "write_requests"])])):
            status, body = _post_authz(_NODE_EXEC)
        self.assertEqual(status, 403)
        self.assertIn("read_requests", body.get("error", ""))

    def test_probe_zero_scope_denied(self):
        with self._with_me(me=_me()):
            status, _ = _post_authz(_PROBE)
        self.assertEqual(status, 403)

    def test_probe_system_read_channels_allowed(self):
        with self._with_me(me=_me(scopes=["read_channels"])):
            status, _ = _post_authz(_PROBE)
        self.assertEqual(status, 200)

    def test_quota_mutation_requires_write_channels(self):
        with self._with_me(me=_me(scopes=["read_channels"])):
            status, _ = _post_authz(_QUOTA)
        self.assertEqual(status, 403)
        with self._with_me(me=_me(scopes=["write_channels"])):
            status, _ = _post_authz(_QUOTA)
        self.assertEqual(status, 200)

    def test_introspection_unavailable_503(self):
        with self._with_me(err=503):
            status, _ = _post_authz(_NODE_EXEC)
        self.assertEqual(status, 503)

    def test_invalid_token_401(self):
        with self._with_me(err=401):
            status, _ = _post_authz(_NODE_EXEC)
        self.assertEqual(status, 401)


class GraphqlStringsPureTest(unittest.TestCase):
    """graphql_strings：递归取 JSON 负载全部字符串值（审计B 严重1 修复的扫描基础）。"""

    def test_flat_query(self):
        self.assertEqual(list(admin_api.graphql_strings({"query": "q1"})), ["q1"])

    def test_nested_variables_and_batch(self):
        payload = [{"query": "a", "variables": {"id": "b", "n": 1, "x": None}},
                   {"query": "c", "variables": {"deep": [{"d": "e"}]}}]
        self.assertEqual(sorted(admin_api.graphql_strings(payload)), ["a", "b", "c", "e"])

    def test_non_string_scalars_ignored(self):
        self.assertEqual(list(admin_api.graphql_strings({"a": 1, "b": True, "c": None})), [])


# JSON 转义形态的攻击载荷（审计B 严重1 回归）：\uXXXX 在源码层程序化构造，
# 保证请求体原文里真的是 6 字符转义序列（服务端 json.loads 后才还原成危险字面量）
_EU = chr(92) + "u002f"  # 斜杠的 JSON 转义形态
_EQ = chr(92) + "u0051"  # 大写 Q 的 JSON 转义形态
_ESC_GID_VARS = ('{"query":"query N($id: ID!) { node(id: $id) { ... on RequestExecution { requestBody } } }",'
                 '"variables":{"id":"gid:' + _EU + _EU + "axonhub" + _EU + "RequestExecution" + _EU + '13939"}}')
_ESC_OP = ('{"query":"mutation { checkProvider' + _EQ + 'uotas }"}')
_ESC_SAFE_REQUEST_GID = ('{"query":"{ node(id: \\"gid:' + _EU + _EU + "axonhub" + _EU + "Request" + _EU + '1\\") { id } }"}')
_BATCH = '[{"query":"query { me { id } }"},{"query":"mutation { checkProviderQuotas }"}]'


class GraphqlAuthzEscapeRegressionTest(unittest.TestCase):
    """审计B 严重1 回归：JSON \\u 转义绕过——修复前这些载荷原文不含字面模式会零内省放行。"""

    def _with_me(self, me=None, err=None):
        return mock.patch.object(admin_api, "_introspect",
                                 return_value=(me, err) if err is None else (None, err))

    def test_escaped_gid_zero_scope_denied(self):
        with self._with_me(me=_me(projects=[("gid://axonhub/Project/1", ["read_requests"])])):
            status, body = _post_authz(_ESC_GID_VARS)
        self.assertEqual(status, 403)
        self.assertIn("read_requests", body.get("error", ""))

    def test_escaped_gid_owner_allowed(self):
        with self._with_me(me=_me(owner=True)):
            status, _ = _post_authz(_ESC_GID_VARS)
        self.assertEqual(status, 200)

    def test_escaped_op_detected(self):
        with self._with_me(me=_me()):
            status, _ = _post_authz(_ESC_OP)
        self.assertEqual(status, 403)

    def test_batch_array_second_op_detected(self):
        with self._with_me(me=_me()):
            status, _ = _post_authz(_BATCH)
        self.assertEqual(status, 403)

    def test_escaped_safe_gid_not_flagged(self):
        # 转义形态的有 policy 类型（Request）解码后仍安全 → 零内省放行，无误报
        with self._with_me(me=_me()) as m:
            status, _ = _post_authz(_ESC_SAFE_REQUEST_GID)
        self.assertEqual(status, 200)
        m.assert_not_called()

    def test_non_json_body_403(self):
        with self._with_me(me=_me(owner=True)) as m:
            req = urllib.request.Request(_BASE + "/graphql-authz", data=b"not json {", method="POST")
            req.add_header("Authorization", "Bearer t")
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    status = r.status
            except urllib.error.HTTPError as e:
                status = e.code
        self.assertEqual(status, 403)  # 无法检查即拒（fail-closed）
        m.assert_not_called()


if __name__ == "__main__":
    unittest.main()