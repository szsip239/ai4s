#!/usr/bin/env python3
"""POST /dlp-admin/bypass-keys/match 服务端 key 匹配测试（issue #138）。

背景：控制台白名单面板原在浏览器拉全量 key 明文算 SHA-256 与绕行名单比对（名单只存
哈希），改服务端匹配后明文只在 shim 内存过手。seam 纪律同 test_admin_api.py：真 shim
Handler + 本地假 axonhub 顶替线路边界（内省 me 与 apiKeys 按 id 批量查询两族报文），
bypass 名单走 BYPASS_KEYS_PATH env 指向 tmp 文件（bypass_keys 模块自身 path seam，
env 在调用时读取）；discover 同进程下 admin_api 可能已被别的测试模块先导入，故
AXONHUB_ADMIN_URL 在 setUp 用 mock.patch.object 指到本文件假服务（线路边界同款）。
"""
import hashlib
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_FAKE_STATE = {
    "tokens": {},        # token -> me dict（内省族）；未注册 token → 401
    "keys": [],          # 假 axonhub 全部 key：{"id": gid, "key": 明文}
    "visible": {},       # token -> 可见 gid 集；缺席=全可见（模拟无权限 id 静默缺席）
    "apikeys_calls": 0,  # apiKeys 查询计数（断言 400/403/空数组路径不打上游）
}


class _FakeAxonhub(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默
        pass

    def do_POST(self):
        if self.path != "/admin/graphql":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            body = {}
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        if "apiKeys" in (body.get("query") or ""):
            _FAKE_STATE["apikeys_calls"] += 1
            ids = set(((body.get("variables") or {}).get("ids")) or [])
            vis = _FAKE_STATE["visible"].get(token)
            edges = [{"node": {"id": k["id"], "key": k["key"]}}
                     for k in _FAKE_STATE["keys"]
                     if k["id"] in ids and (vis is None or k["id"] in vis)]
            payload, code = {"data": {"apiKeys": {"edges": edges}}}, 200
        else:
            me = _FAKE_STATE["tokens"].get(token)
            if me is None:
                payload, code = {"errors": [{"message": "unauthorized"}]}, 401
            else:
                payload, code = {"data": {"me": me}}, 200
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _start(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


_FAKE_AXONHUB = _start(_FakeAxonhub)
_FAKE_URL = f"http://127.0.0.1:{_FAKE_AXONHUB.server_address[1]}/admin/graphql"
os.environ["AXONHUB_ADMIN_URL"] = _FAKE_URL  # 单跑本文件时的模块级注入（同 test_admin_api.py 机制）

import admin_api  # noqa: E402
import app as shim_app  # noqa: E402
import bypass_keys  # noqa: E402

_SHIM = _start(shim_app.Handler)
_BASE = f"http://127.0.0.1:{_SHIM.server_address[1]}"

_K1 = {"id": "gid://axonhub/APIKey/9", "key": "ah-secret-aaa"}
_K2 = {"id": "gid://axonhub/APIKey/10", "key": "ah-secret-bbb"}
_K1_HASH = hashlib.sha256(_K1["key"].encode()).hexdigest()


def _post_match(payload, token="t"):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(_BASE + "/dlp-admin/bypass-keys/match", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class BypassKeysMatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.keys_path = os.path.join(self.tmp.name, "bypass-keys.json")
        _FAKE_STATE.update({"tokens": {"reader": {"id": "gid://axonhub/User/7", "isOwner": False,
                                                  "scopes": ["read_api_keys"]},
                                       "owner": {"id": "gid://axonhub/User/1", "isOwner": True,
                                                 "scopes": []}},
                            "keys": [_K1, _K2], "visible": {}, "apikeys_calls": 0})
        for p in (mock.patch.object(admin_api, "AXONHUB_ADMIN_URL", _FAKE_URL),
                  mock.patch.dict(os.environ, {"BYPASS_KEYS_PATH": self.keys_path})):
            p.start()
            self.addCleanup(p.stop)

    def test_match_hit(self):
        bypass_keys.add(_K1["key"], "CI 管道", "all", None, "admin@corp", path=self.keys_path)
        status, body = _post_match({"keyIds": [_K1["id"], _K2["id"]]}, token="reader")
        self.assertEqual(status, 200)
        self.assertEqual(body["matches"], [{"keyId": _K1["id"], "entryId": _K1_HASH,
                                            "label": "CI 管道", "scope": "all", "enabled": True}])

    def test_no_match(self):
        status, body = _post_match({"keyIds": [_K1["id"], _K2["id"]]}, token="reader")
        self.assertEqual((status, body["matches"]), (200, []))

    def test_disabled_entry_reported(self):
        entry = bypass_keys.add(_K1["key"], "旧管道", "layers", ["l2"], "a", path=self.keys_path)
        bypass_keys.set_enabled(entry["id"], False, path=self.keys_path)
        status, body = _post_match({"keyIds": [_K1["id"]]}, token="reader")
        self.assertEqual(status, 200)
        self.assertEqual(body["matches"], [{"keyId": _K1["id"], "entryId": _K1_HASH,
                                            "label": "旧管道", "scope": "layers", "enabled": False}])

    def test_partial_missing_id_skipped(self):
        bypass_keys.add(_K1["key"], "CI 管道", "all", None, "a", path=self.keys_path)
        status, body = _post_match({"keyIds": [_K1["id"], "gid://axonhub/APIKey/999999"]},
                                   token="reader")
        self.assertEqual(status, 200)
        self.assertEqual([m["keyId"] for m in body["matches"]], [_K1["id"]])

    def test_invisible_id_skipped(self):
        # 调用方无权读 _K1（axonhub 侧不出现该 edge）→ 静默跳过，整体仍 200
        bypass_keys.add(_K1["key"], "CI 管道", "all", None, "a", path=self.keys_path)
        _FAKE_STATE["visible"]["reader"] = {_K2["id"]}
        status, body = _post_match({"keyIds": [_K1["id"], _K2["id"]]}, token="reader")
        self.assertEqual((status, body["matches"]), (200, []))

    def test_owner_pass(self):
        bypass_keys.add(_K1["key"], "CI 管道", "all", None, "a", path=self.keys_path)
        status, body = _post_match({"keyIds": [_K1["id"]]}, token="owner")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["matches"]), 1)

    def test_missing_scope_403(self):
        _FAKE_STATE["tokens"]["noscope"] = {"id": "gid://axonhub/User/8", "isOwner": False,
                                            "scopes": ["read_channels"]}
        status, body = _post_match({"keyIds": [_K1["id"]]}, token="noscope")
        self.assertEqual(status, 403)
        self.assertIn("read_api_keys", body.get("error", ""))
        self.assertEqual(_FAKE_STATE["apikeys_calls"], 0)  # 鉴权失败不打上游

    def test_no_token_401(self):
        status, _ = _post_match({"keyIds": [_K1["id"]]}, token=None)
        self.assertEqual(status, 401)

    def test_over_500_400(self):
        ids = [f"gid://axonhub/APIKey/{i}" for i in range(501)]
        status, _ = _post_match({"keyIds": ids}, token="reader")
        self.assertEqual(status, 400)
        self.assertEqual(_FAKE_STATE["apikeys_calls"], 0)  # 超限直接拒，不打上游

    def test_bad_body_400(self):
        status, _ = _post_match({"keyIds": "gid://axonhub/APIKey/9"}, token="reader")
        self.assertEqual(status, 400)
        status, _ = _post_match({"keyIds": [_K1["id"], 42]}, token="reader")
        self.assertEqual(status, 400)
        status, _ = _post_match({"keyIds": [_K1["id"], ""]}, token="reader")
        self.assertEqual(status, 400)
        self.assertEqual(_FAKE_STATE["apikeys_calls"], 0)

    def test_empty_ids_200_no_upstream(self):
        status, body = _post_match({"keyIds": []}, token="reader")
        self.assertEqual((status, body["matches"]), (200, []))
        self.assertEqual(_FAKE_STATE["apikeys_calls"], 0)

    def test_response_has_no_plaintext(self):
        bypass_keys.add(_K1["key"], "CI 管道", "all", None, "a", path=self.keys_path)
        status, body = _post_match({"keyIds": [_K1["id"], _K2["id"]]}, token="reader")
        self.assertEqual(status, 200)
        self.assertNotIn(_K1["key"], json.dumps(body))
        self.assertNotIn(_K2["key"], json.dumps(body))


if __name__ == "__main__":
    unittest.main()
