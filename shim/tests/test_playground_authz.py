#!/usr/bin/env python3
"""playground 模型调用闸门测试（issue #128）：/playground-authz extAuthz 端点。

背景：beta6 上游 /admin/playground/chat 零 scope 校验（空 scopes 用户 200 直通渠道），
本网关在 agentgateway 补 extAuthz fail-closed 门。seam 纪律同 test_bypass.py：活 shim
Handler 起本地端口，内省经 mock.patch 替换 admin_api._introspect（不打真网络）。
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

_PID = "gid://axonhub/Project/6"
_OTHER = "gid://axonhub/Project/9"


def _start(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


_SHIM = _start(shim_app.Handler)
_BASE = f"http://127.0.0.1:{_SHIM.server_address[1]}"


def _post_authz(token=None, project=_PID):
    req = urllib.request.Request(_BASE + "/playground-authz", data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if project is not None:
        req.add_header("X-Project-ID", project)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _me(owner=False, scopes=(), projects=()):
    return {"id": "gid://axonhub/User/1", "email": "u@x", "isOwner": owner,
            "scopes": list(scopes),
            "projects": [{"projectID": p, "scopes": list(s)} for p, s in projects]}


class PlaygroundAuthzTest(unittest.TestCase):
    """端点行为：token/项目头缺失、内省失败（401/503）、放行三通道、拒绝面。"""

    def _with_me(self, me=None, err=None):
        """返回 mock 上下文：me 非 None → 内省成功；err 非 None → 内省失败码。"""
        return mock.patch.object(admin_api, "_introspect",
                                 return_value=(me, err) if err is None else (None, err))

    def test_missing_token_403(self):
        status, _ = _post_authz(token=None)
        self.assertEqual(status, 403)

    def test_missing_project_header_403(self):
        status, _ = _post_authz(token="t", project=None)
        self.assertEqual(status, 403)

    def test_invalid_token_401_deny(self):
        with self._with_me(err=401):
            status, _ = _post_authz(token="bad")
        self.assertEqual(status, 401)  # 非 2xx → 网关 deny

    def test_introspection_unavailable_503_deny(self):
        with self._with_me(err=503):
            status, _ = _post_authz(token="t")
        self.assertEqual(status, 503)  # fail-closed：内省不可达不放行

    def test_owner_allowed(self):
        with self._with_me(me=_me(owner=True)) as m:
            status, _ = _post_authz(token="t")
        self.assertEqual(status, 200)
        # 内省查询换形为带项目 scopes 的版本（闸门依赖项目成员判定）
        self.assertEqual(m.call_args[0][1], admin_api._ME_PROJECTS_QUERY)

    def test_system_write_requests_allowed(self):
        with self._with_me(me=_me(scopes=["write_requests"])):
            status, _ = _post_authz(token="t")
        self.assertEqual(status, 200)

    def test_project_member_write_requests_allowed(self):
        with self._with_me(me=_me(projects=[(_PID, ["read_requests", "write_requests"])])):
            status, _ = _post_authz(token="t")
        self.assertEqual(status, 200)

    def test_member_of_other_project_denied(self):
        with self._with_me(me=_me(projects=[(_OTHER, ["write_requests"])])):
            status, _ = _post_authz(token="t")
        self.assertEqual(status, 403)

    def test_invited_external_empty_scopes_denied(self):
        # issue #128 核心面：邀请注册落地姿态（项目成员但空 scopes）→ playground 拒绝
        with self._with_me(me=_me(projects=[(_PID, [])])):
            status, body = _post_authz(token="t")
        self.assertEqual(status, 403)
        self.assertIn("write_requests", body.get("error", ""))


class PlaygroundAllowedPureTest(unittest.TestCase):
    """playground_allowed 纯函数：三放行通道 + 精确匹配纪律（不通配 "*"）。"""

    def test_owner(self):
        self.assertTrue(admin_api.playground_allowed(_me(owner=True), _PID))

    def test_system_scope(self):
        self.assertTrue(admin_api.playground_allowed(_me(scopes=["write_requests"]), _PID))

    def test_project_scope_match(self):
        self.assertTrue(admin_api.playground_allowed(_me(projects=[(_PID, ["write_requests"])]), _PID))

    def test_project_scope_mismatch(self):
        self.assertFalse(admin_api.playground_allowed(_me(projects=[(_OTHER, ["write_requests"])]), _PID))

    def test_empty_everything(self):
        self.assertFalse(admin_api.playground_allowed(_me(), _PID))

    def test_wildcard_not_honored(self):
        # 与 admin_api._authorize 同款纪律：非 owner 的 "*" 不当万能钥匙，须显式 write_requests
        self.assertFalse(admin_api.playground_allowed(_me(scopes=["*"]), _PID))
        self.assertFalse(admin_api.playground_allowed(_me(projects=[(_PID, ["*"])]), _PID))

    def test_missing_projects_key(self):
        me = {"id": "u", "isOwner": False, "scopes": []}  # 无 projects 键（旧查询形状容错）
        self.assertFalse(admin_api.playground_allowed(me, _PID))


if __name__ == "__main__":
    unittest.main()
