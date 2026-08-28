#!/usr/bin/env python3
"""auto 路由 /classify 桩端点单测（issue #115 spike）。

被测语义（app.py do_POST /classify 分支，agentgateway extAuthz HTTP 授权服务形态）：
- 协议语义：任何输入都 200（2xx=放行；本端点只分类不鉴权，永不阻断——非 2xx 会被网关
  当 deny 决策，fail-open 由网关 failureMode=allow 管，不由本端点）；
- model=="auto" → 200 + 响应头 x-resolved-model=CLASSIFY_STUB_AUTO_MODEL（写死 echo-test，
  真实分档逻辑 #117）；
- 其他合法 model（白名单 [A-Za-z0-9._:-]{1,128}）→ 200 + x-resolved-model 原值回显
  （实证全量请求过改写通路）；
- body 缺失/空/截断 JSON/非对象/无 model 字段/model 非字符串 → 200 不带响应头
  （网关 CEL has(extauthz.resolved_model) 为 false，回退原 model + modelAliases 兜底）；
- model 含 CRLF 等非法字符 → 200 不带响应头（用户输入进响应头前的响应拆分防护）；
- 响应体恒为 {"resolved_model": <str|null>}（调试/备用 metadata CEL json(response.body) 通路）。

seam 纪律同 test_rules_layer.py：进程内起真实 ThreadingHTTPServer 跑 app.Handler；
本端点不读 settings/词表/settings 环境，无需临时文件隔离。

运行：cd shim && .venv/bin/python -m unittest discover -s tests
"""
import json
import os
import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

# 让测试可 import shim 目录下的 app（discover 从 shim/tests 启动）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as shim_app  # noqa: E402

_SHIM = ThreadingHTTPServer(("127.0.0.1", 0), shim_app.Handler)
threading.Thread(target=_SHIM.serve_forever, daemon=True).start()
_BASE = f"http://127.0.0.1:{_SHIM.server_address[1]}"


def _post_classify(raw: bytes):
    """POST /classify，返回 (status, x-resolved-model 头或 None, 响应体 dict)。"""
    req = urllib.request.Request(
        _BASE + "/classify", data=raw,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.headers.get("x-resolved-model"), json.load(r)


class ClassifyStubTest(unittest.TestCase):
    """桩行为：auto 改写 / 原值回显 / 缺省无头三态。"""

    def test_auto_resolves_to_stub_model(self):
        """model=auto → 200 + x-resolved-model=写死的桩目标（echo-test）。"""
        status, hdr, body = _post_classify(
            json.dumps({"model": "auto", "messages": [{"role": "user", "content": "hi"}]}).encode())
        self.assertEqual(status, 200)
        self.assertEqual(hdr, shim_app.CLASSIFY_STUB_AUTO_MODEL)
        self.assertEqual(hdr, "echo-test")  # 桩写死值锚定（#117 接入真实分类时同步改本断言）
        self.assertEqual(body["resolved_model"], shim_app.CLASSIFY_STUB_AUTO_MODEL)

    def test_non_auto_echoes_original(self):
        """model=echo-test → 200 + 原值回显（非 auto 请求也过改写通路，值不变）。"""
        status, hdr, body = _post_classify(json.dumps({"model": "echo-test"}).encode())
        self.assertEqual(status, 200)
        self.assertEqual(hdr, "echo-test")
        self.assertEqual(body["resolved_model"], "echo-test")

    def test_real_model_echoes_original(self):
        """真实旗舰模型名（gpt-5.6-luna，含 . 与 - ）过白名单原值回显。"""
        status, hdr, _ = _post_classify(json.dumps({"model": "gpt-5.6-luna"}).encode())
        self.assertEqual(status, 200)
        self.assertEqual(hdr, "gpt-5.6-luna")

    def test_missing_model_no_header(self):
        """无 model 字段 → 200 不带响应头（网关 CEL 回退原 model）。"""
        status, hdr, body = _post_classify(json.dumps({"messages": []}).encode())
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        self.assertIsNone(body["resolved_model"])

    def test_empty_body_no_header(self):
        """空 body → 200 不带响应头。"""
        status, hdr, _ = _post_classify(b"")
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)

    def test_truncated_json_no_header(self):
        """截断 JSON（extAuthz allowPartialMessage 截断场景）→ 200 不带响应头，不 500。"""
        status, hdr, _ = _post_classify(b'{"model": "auto", "messages": [{"role": "us')
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)

    def test_non_object_json_no_header(self):
        """JSON 非对象（数组/标量）→ 200 不带响应头。"""
        for raw in (b"[1,2]", b'"auto"', b"123"):
            status, hdr, _ = _post_classify(raw)
            self.assertEqual(status, 200)
            self.assertIsNone(hdr)

    def test_non_string_model_no_header(self):
        """model 非字符串（数字/null）→ 200 不带响应头。"""
        for model in (123, None, True, ["auto"]):
            status, hdr, _ = _post_classify(json.dumps({"model": model}).encode())
            self.assertEqual(status, 200)
            self.assertIsNone(hdr)

    def test_crlf_model_no_header(self):
        """model 含 CRLF → 200 不带响应头（响应拆分防护：用户输入不进未校验的响应头）。"""
        status, hdr, _ = _post_classify(
            json.dumps({"model": "auto-ok\r\nx-injected: 1"}).encode())
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)

    def test_oversize_model_no_header(self):
        """model 超 128 字符白名单边界 → 200 不带响应头。"""
        status, hdr, _ = _post_classify(json.dumps({"model": "a" * 129}).encode())
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)

    def test_never_blocks(self):
        """协议锚点：全部输入形态都 200（extAuthz 2xx=放行，本端点无 4xx/5xx 路径）。"""
        for raw in (b"", b"not-json", json.dumps({"model": "auto"}).encode(),
                    json.dumps({"model": "echo-test"}).encode(), b"\xff\xfe"):
            status, _, _ = _post_classify(raw)
            self.assertEqual(status, 200)

    def test_get_classify_200_no_header(self):
        """GET /classify → 200 不带响应头（extAuthz 对 /v1 路由同方法转发授权调用，
        GET /v1/models 等会打到 GET /classify；非 2xx 会被网关当 deny 直回客户端）。"""
        req = urllib.request.Request(_BASE + "/classify")  # GET
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIsNone(r.headers.get("x-resolved-model"))
            self.assertIsNone(json.load(r)["resolved_model"])


if __name__ == "__main__":
    unittest.main()
