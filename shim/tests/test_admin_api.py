#!/usr/bin/env python3
"""shim admin 平面（issue #31 骨架）接口行为 + 原子写工具测试。

seam 纪律：
- HTTP 接口：进程内起真实 ThreadingHTTPServer 跑 app.Handler（含 admin dispatch 挂接）；
  axonhub 内省只在线路边界用本地假 HTTP 服务顶替，不 mock shim 内部函数。
- 原子写：直接测 admin_api.write_json_atomic。
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

# 让测试可 import shim 目录下的 app / admin_api（discover 从 shim/tests 启动）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---- 假 axonhub：只顶替内省线路边界（POST /admin/graphql）----
# token → me 映射驱动行为；未注册 token 一律 401（对齐 axonhub 拒绝语义）。
_FAKE_STATE = {
    "mode": "ok",   # ok=按 token 表应答；drop=无响应断连（模拟内省不可达）
    "tokens": {},   # token -> {"id":..,"isOwner":..,"scopes":[..]}；me 可为 None（内省通过但无身份）
}


class _FakeAxonhub(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默
        pass

    def do_POST(self):
        if self.path != "/admin/graphql":
            self.send_error(404)
            return
        if _FAKE_STATE["mode"] == "drop":
            self.connection.close()  # 无响应断连 → 客户端网络错误
            return
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        auth = self.headers.get("Authorization") or ""
        token = auth.removeprefix("Bearer ")
        me = _FAKE_STATE["tokens"].get(token)
        if me is None and token not in _FAKE_STATE["tokens"]:
            body, code = b'{"errors":[{"message":"unauthorized"}]}', 401
        else:
            body, code = json.dumps({"data": {"me": me}}).encode(), 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_server(handler_cls):
    """127.0.0.1:0 ephemeral 端口起真实 HTTP 服务（daemon 线程，测试进程退出即收）。"""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# 假 axonhub 先于被测模块导入启动：内省 URL 经环境变量注入（与容器默认 http://axonhub:8090 同机制）
_FAKE_AXONHUB = _start_server(_FakeAxonhub)
os.environ["AXONHUB_ADMIN_URL"] = f"http://127.0.0.1:{_FAKE_AXONHUB.server_address[1]}/admin/graphql"

import admin_api  # noqa: E402  须在环境变量注入后导入（模块级读 URL）
import app as shim_app  # noqa: E402  真实 dispatch（含 admin 挂接）

_SHIM = _start_server(shim_app.Handler)
_SHIM_BASE = f"http://127.0.0.1:{_SHIM.server_address[1]}"


def _get(path, token=None, scheme="Bearer"):
    """对测试 shim 发 GET；返回 (status, json body)。非 2xx 不抛异常。scheme 可换大小写变体。"""
    req = urllib.request.Request(_SHIM_BASE + path)
    if token:
        req.add_header("Authorization", f"{scheme} {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class AdminApiTest(unittest.TestCase):
    def setUp(self):
        # 每用例复位假 axonhub 行为
        _FAKE_STATE["mode"] = "ok"
        _FAKE_STATE["tokens"] = {}

    def test_healthz_no_auth(self):
        """GET /dlp-admin/healthz 无鉴权 → 200 {"status":"ok"}。"""
        status, body = _get("/dlp-admin/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})

    def test_ping_without_token_401(self):
        """GET /dlp-admin/ping 无 Authorization 头 → 401（不进内省）。"""
        status, _ = _get("/dlp-admin/ping")
        self.assertEqual(status, 401)

    def test_ping_bad_token_401(self):
        """内省拒绝（axonhub 非 200 / me 为空）→ 401。"""
        # token 未在假 axonhub 注册 → 假服务回 401 → shim 401
        status, _ = _get("/dlp-admin/ping", token="bogus-token")
        self.assertEqual(status, 401)
        # token 注册但 me 为 None（内省通过却无身份）→ 401
        _FAKE_STATE["tokens"]["null-me-token"] = None
        status, _ = _get("/dlp-admin/ping", token="null-me-token")
        self.assertEqual(status, 401)

    def test_ping_with_read_scope_200(self):
        """有 read_channels 系统 scope → 200，内省结果透传。"""
        _FAKE_STATE["tokens"]["reader-token"] = {
            "id": "42", "isOwner": False, "scopes": ["read_channels"],
        }
        status, body = _get("/dlp-admin/ping", token="reader-token")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"user_id": "42", "is_owner": False, "scopes": ["read_channels"]})

    def test_ping_missing_scope_403(self):
        """内省通过但缺 read_channels → 403。"""
        _FAKE_STATE["tokens"]["noscope-token"] = {
            "id": "43", "isOwner": False, "scopes": ["read_users"],
        }
        status, _ = _get("/dlp-admin/ping", token="noscope-token")
        self.assertEqual(status, 403)

    def test_ping_owner_pass(self):
        """isOwner=true 直通（无需任何 scope）→ 200。"""
        _FAKE_STATE["tokens"]["owner-token"] = {
            "id": "1", "isOwner": True, "scopes": [],
        }
        status, body = _get("/dlp-admin/ping", token="owner-token")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"user_id": "1", "is_owner": True, "scopes": []})

    def test_ping_introspection_down_503(self):
        """内省不可达（无响应断连）→ 503：admin 平面 fail-closed，不适用检测链 fail-open。"""
        _FAKE_STATE["mode"] = "drop"
        status, _ = _get("/dlp-admin/ping", token="reader-token")
        self.assertEqual(status, 503)

    def test_bearer_scheme_case_insensitive(self):
        """RFC 6750 scheme 大小写不敏感：小写 bearer + 合法 token → 200。"""
        _FAKE_STATE["tokens"]["reader-token"] = {
            "id": "42", "isOwner": False, "scopes": ["read_channels"],
        }
        status, body = _get("/dlp-admin/ping", token="reader-token", scheme="bearer")
        self.assertEqual(status, 200)
        self.assertEqual(body["user_id"], "42")

    def test_unknown_path_gated_by_auth(self):
        """未知 admin 路径（healthz 除外）先过鉴权再 404：无凭据探测不泄露路由存在性。"""
        # 无 token 探未知路径 → 401（而非 404）
        status, _ = _get("/dlp-admin/nope")
        self.assertEqual(status, 401)
        # 内省通过但缺 scope → 403（鉴权判定照旧，先于路由查找）
        _FAKE_STATE["tokens"]["noscope-token"] = {
            "id": "43", "isOwner": False, "scopes": ["read_users"],
        }
        status, _ = _get("/dlp-admin/nope", token="noscope-token")
        self.assertEqual(status, 403)
        # 合法 token（read scope）探未知路径 → 404
        _FAKE_STATE["tokens"]["reader-token"] = {
            "id": "42", "isOwner": False, "scopes": ["read_channels"],
        }
        status, _ = _get("/dlp-admin/nope", token="reader-token")
        self.assertEqual(status, 404)


class WriteJsonAtomicTest(unittest.TestCase):
    """原子写工具（seam 2）：写 JSON 配置的落盘完整性。"""

    def test_roundtrip(self):
        """写后读回一致；风格对齐词表文件（ensure_ascii=False、indent=2、尾部换行）。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "confidential-terms.json")
            obj = {"version": 2, "terms": [{"value": "凤凰计划", "rule_id": "confidential.codename"}]}
            admin_api.write_json_atomic(path, obj)
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            self.assertEqual(json.loads(raw), obj)
            self.assertIn("凤凰计划", raw)  # ensure_ascii=False：中文不转义
            self.assertTrue(raw.startswith("{\n  "))  # indent=2
            self.assertTrue(raw.endswith("}\n"))  # 尾部换行

    def test_replace_failure_keeps_old_file(self):
        """os.replace 抛错时旧文件完整、tmp 无残留。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fingerprints.json")
            old = {"version": 1, "docs": {"旧文档": ["aa"]}}
            admin_api.write_json_atomic(path, old)
            with mock.patch("os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    admin_api.write_json_atomic(path, {"version": 2, "docs": {}})
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), old)  # 旧文件未被破坏
            # 备份在 replace 前已落盘：.bak 与旧文件同值（可人工回滚），tmp 已清理
            with open(path + ".bak", encoding="utf-8") as f:
                self.assertEqual(json.load(f), old)
            self.assertEqual(sorted(os.listdir(d)), ["fingerprints.json", "fingerprints.json.bak"])

    def test_second_write_rolls_backup(self):
        """.bak 滚动备份（issue #31 spec）：连续写两次后，<path>.bak 是第一次的值。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "confidential-terms.json")
            v1 = {"version": 1, "terms": [{"value": "凤凰计划", "rule_id": "r1"}]}
            v2 = {"version": 2, "terms": [{"value": "蓝鲸系统", "rule_id": "r2"}]}
            admin_api.write_json_atomic(path, v1)
            admin_api.write_json_atomic(path, v2)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), v2)  # 新值落盘
            with open(path + ".bak", encoding="utf-8") as f:
                self.assertEqual(json.load(f), v1)  # .bak 保留上一版

    def test_no_backup_when_target_absent(self):
        """目标文件不存在时跳过备份：无 .bak 产生且不报错。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "new.json")
            admin_api.write_json_atomic(path, {"version": 1})
            self.assertEqual(os.listdir(d), ["new.json"])

    def test_concurrent_writes_valid_json(self):
        """多线程并发写同一文件：结果总是完整合法 JSON，无半写/截断，tmp 无残留。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "concurrent.json")
            payloads = [
                {"writer": i, "seq": j, "pad": "凤凰" * 200}
                for i in range(8)
                for j in range(25)
            ]
            errors = []

            def writer(own):
                try:
                    for p in own:
                        admin_api.write_json_atomic(path, p)
                except Exception as e:  # 收集线程内异常，主线程断言
                    errors.append(e)

            threads = [
                threading.Thread(target=writer, args=(payloads[i * 25:(i + 1) * 25],))
                for i in range(8)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            with open(path, encoding="utf-8") as f:
                final = json.load(f)  # 合法 JSON 即证明无半写（截断会解析失败）
            self.assertIn(final, payloads)
            self.assertFalse([f for f in os.listdir(d) if f.endswith(".tmp")])  # tmp 无残留


if __name__ == "__main__":
    unittest.main()
