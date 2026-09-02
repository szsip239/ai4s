#!/usr/bin/env python3
"""Key 级 DLP 绕行链路测试（issue #129）：/request、/response、/bv1-authz 三入口。

seam 纪律：活 shim Handler 起本地端口（同 test_admin_api 模式），词表/settings/名单/shadow_log
全部指向 tmp；断言只盯 webhook 协议应答形状与 shadow_log 审计条（不落原文、不记 token）。
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as shim_app
import bypass_keys
import shadow_log


def _start(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


_SHIM = _start(shim_app.Handler)
_BASE = f"http://127.0.0.1:{_SHIM.server_address[1]}"


def _post(path, payload, token=None):
    req = urllib.request.Request(_BASE + path, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class BypassLinkTest(unittest.TestCase):
    """绕行名单对检测链的实际效果（词表命中作为 shim 侧 451 代表）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.wordlist = os.path.join(d, "terms.json")
        with open(self.wordlist, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "terms": [{"value": "凤凰计划", "rule_id": "confidential.codename"}]}, f,
                      ensure_ascii=False)
        self.settings = os.path.join(d, "settings.json")
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"version": 1}, f)
        self.bypass = os.path.join(d, "bypass-keys.json")
        self.shadow = os.path.join(d, "shadow.jsonl")
        self._saved = (shim_app.WORDLIST_PATH, shim_app.SETTINGS_PATH)
        shim_app.WORDLIST_PATH = self.wordlist
        shim_app.SETTINGS_PATH = self.settings
        self._saved_env = {k: os.environ.get(k) for k in ("BYPASS_KEYS_PATH", "SHADOW_LOG_PATH")}
        os.environ["BYPASS_KEYS_PATH"] = self.bypass
        os.environ["SHADOW_LOG_PATH"] = self.shadow

    def tearDown(self):
        shim_app.WORDLIST_PATH, shim_app.SETTINGS_PATH = self._saved
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    @staticmethod
    def _req_payload():
        return {"body": {"messages": [{"role": "user", "content": "凤凰计划进展如何"}]}}

    def _resp_payload(self):
        return {"body": {"choices": [{"message": {"content": "凤凰计划已延期"}}]}}

    def _shadow_layers(self):
        return [r["layer"] for r in shadow_log.tail(50, path=self.shadow)]

    # ---- /request ----

    def test_request_wordlist_hit_no_bypass_451(self):
        status, body = _post("/request", self._req_payload())
        self.assertEqual(status, 200)
        self.assertEqual(body["action"]["status_code"], 451)

    def test_request_scope_all_pass_and_audit(self):
        bypass_keys.add("sk-trusted", "CI", "all", None, "tester", path=self.bypass)
        status, body = _post("/request", self._req_payload(), token="sk-trusted")
        self.assertEqual(status, 200)
        self.assertNotIn("status_code", body["action"])  # PassAction
        self.assertIn("bypass", self._shadow_layers())  # 审计落条

    def test_request_scope_layers_covering_l2_pass(self):
        bypass_keys.add("sk-l2", "n", "layers", ["l2"], "tester", path=self.bypass)
        _, body = _post("/request", self._req_payload(), token="sk-l2")
        self.assertNotIn("status_code", body["action"])

    def test_request_scope_layers_not_covering_l2_still_451(self):
        bypass_keys.add("sk-pg-only", "n", "layers", ["pg"], "tester", path=self.bypass)
        _, body = _post("/request", self._req_payload(), token="sk-pg-only")
        self.assertEqual(body["action"]["status_code"], 451)

    def test_request_disabled_entry_not_bypassed(self):
        entry = bypass_keys.add("sk-off", "n", "all", None, "tester", path=self.bypass)
        bypass_keys.set_enabled(entry["id"], False, path=self.bypass)
        _, body = _post("/request", self._req_payload(), token="sk-off")
        self.assertEqual(body["action"]["status_code"], 451)

    def test_request_unknown_token_not_bypassed(self):
        _, body = _post("/request", self._req_payload(), token="sk-nobody")
        self.assertEqual(body["action"]["status_code"], 451)

    # ---- /response ----

    def test_response_bypass_scope_all_pass(self):
        bypass_keys.add("sk-trusted", "CI", "all", None, "tester", path=self.bypass)
        _, body = _post("/response", self._resp_payload(), token="sk-trusted")
        self.assertEqual(body["action"].get("reason"), "pass")

    def test_response_layers_covering_response_pass(self):
        bypass_keys.add("sk-r", "n", "layers", ["response"], "tester", path=self.bypass)
        _, body = _post("/response", self._resp_payload(), token="sk-r")
        self.assertEqual(body["action"].get("reason"), "pass")

    def test_response_layers_not_covering_still_451(self):
        # layers=["l2"] 时响应侧词表（l2 检测族）同样跳过——与「模块关=处处关」同语义；
        # 用不含 l2/response 的 layers 验证响应侧仍拦截
        bypass_keys.add("sk-nr", "n", "layers", ["pg"], "tester", path=self.bypass)
        _, body = _post("/response", self._resp_payload(), token="sk-nr")
        self.assertEqual(body["action"]["status_code"], 451)
        # pg 是请求侧层：响应侧无可跳层 → 不落「按层跳过」审计条（审计只记本侧真实跳过）
        self.assertNotIn("bypass", self._shadow_layers())

    def test_request_layers_response_only_still_451_no_audit(self):
        # layers=["response"] 只影响响应侧：请求侧照常检测且不落「按层跳过」审计条
        bypass_keys.add("sk-ro", "n", "layers", ["response"], "tester", path=self.bypass)
        _, body = _post("/request", self._req_payload(), token="sk-ro")
        self.assertEqual(body["action"]["status_code"], 451)
        self.assertNotIn("bypass", self._shadow_layers())

    def test_response_layers_l2_family_also_skipped(self):
        bypass_keys.add("sk-l2r", "n", "layers", ["l2"], "tester", path=self.bypass)
        _, body = _post("/response", self._resp_payload(), token="sk-l2r")
        self.assertEqual(body["action"].get("reason"), "pass")

    # ---- /bv1-authz（全绕入口的 fail-closed 门）----

    def test_bv1_authz_scope_all_200_and_audit(self):
        bypass_keys.add("sk-full", "CI", "all", None, "tester", path=self.bypass)
        status, _ = _post("/bv1-authz", {"model": "gpt-x"}, token="sk-full")
        self.assertEqual(status, 200)
        self.assertIn("bypass", self._shadow_layers())
        # extAuthz 透传的是原始 OpenAI 请求体（model 在顶层），审计条须带上模型名
        recs = [r for r in shadow_log.tail(50, path=self.shadow) if r["layer"] == "bypass"]
        self.assertTrue(any(r.get("model") == "gpt-x" for r in recs))

    def test_bv1_authz_layers_scope_403(self):
        bypass_keys.add("sk-partial", "n", "layers", ["l2"], "tester", path=self.bypass)
        status, _ = _post("/bv1-authz", {"model": "gpt-x"}, token="sk-partial")
        self.assertEqual(status, 403)

    def test_bv1_authz_unknown_token_403(self):
        status, _ = _post("/bv1-authz", {"model": "gpt-x"}, token="sk-nobody")
        self.assertEqual(status, 403)

    def test_bv1_authz_no_header_403(self):
        status, _ = _post("/bv1-authz", {"model": "gpt-x"})
        self.assertEqual(status, 403)

    def test_bv1_authz_disabled_entry_403(self):
        entry = bypass_keys.add("sk-off2", "n", "all", None, "tester", path=self.bypass)
        bypass_keys.set_enabled(entry["id"], False, path=self.bypass)
        status, _ = _post("/bv1-authz", {"model": "gpt-x"}, token="sk-off2")
        self.assertEqual(status, 403)

    def test_bv1_authz_get_403(self):
        req = urllib.request.Request(_BASE + "/bv1-authz", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
