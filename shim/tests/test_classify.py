#!/usr/bin/env python3
"""auto 路由 /classify 端点协议锚单测（issue #115 桩退役收口，issue #117 行为基线）。

issue #115 的桩行为（auto→echo-test 写死、其他合法 model 原值回显）已随 #117 真实分类器
上线退役。本文件锚定桩退役后的协议不变量（routing.enabled=false 缺省态=现网行为零变化）：

- 协议语义：任何输入都 200（2xx=放行；本端点只分类不鉴权，永不阻断——非 2xx 会被网关
  当 deny 决策，fail-open 由网关 failureMode=allow 管，不由本端点）；
- routing.enabled=false（settings 无 routing 节或显式 false）→ 对所有 model 回 200
  **不带** x-resolved-model 响应头（网关 CEL has(extauthz.resolved_model) 为 false，
  回退 llmRequest.model；auto 由 modelAliases 静态兜底落旗舰——与现网等价）；
- 非 auto model 不再回显原值（transformations 无头回退原 model，语义等价）；
- body 缺失/空/截断 JSON/非对象/无 model 字段/model 非字符串/含 CRLF → 200 不带响应头；
- 全方法恒 200（GET/PUT/DELETE/OPTIONS）：extAuthz 按原请求方法转发授权调用（#115 坑 1），
  非 2xx 会被网关当 deny 直回客户端；
- 响应体恒为 {"resolved_model": null}（调试/备用 metadata CEL json(response.body) 通路）。

enabled=true 的分类/会话/降级行为见 test_router.py。

seam 纪律同桩版：进程内起真实 ThreadingHTTPServer 跑 app.Handler；缺省态不读 settings
（SETTINGS_PATH 指到不存在路径，routing 缺省 disabled），无需分类器假服务。

运行：cd shim && .venv/bin/python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
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

# 缺省态隔离（对齐 JudgeShadowMaskTest env 纪律）：SETTINGS_PATH 指到不存在路径
# （routing 节缺席=disabled），并摘除开发机可能导出的 ROUTING_* env
_TMP = tempfile.TemporaryDirectory()
shim_app.SETTINGS_PATH = os.path.join(_TMP.name, "no-such-settings.json")
for _k in ("ROUTING_ENABLED", "ROUTING_THRESHOLD", "ROUTING_TIMEOUT", "ROUTING_MAX_CONCURRENCY"):
    os.environ.pop(_k, None)


def _post_classify(raw: bytes):
    """POST /classify，返回 (status, x-resolved-model 头或 None, 响应体 dict)。"""
    req = urllib.request.Request(
        _BASE + "/classify", data=raw,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.headers.get("x-resolved-model"), json.load(r)


class ClassifyStubRetiredTest(unittest.TestCase):
    """桩退役基线（routing 缺省 disabled）：所有输入 200 无头——现网行为零变化。"""

    def test_auto_no_header_when_disabled(self):
        """model=auto → 200 不带响应头（桩的 echo-test 改写已退役；网关 CEL 回退 auto
        → modelAliases 静态兜底落旗舰，与现网等价）。"""
        status, hdr, body = _post_classify(
            json.dumps({"model": "auto", "messages": [{"role": "user", "content": "hi"}]}).encode())
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        self.assertIsNone(body["resolved_model"])

    def test_non_auto_no_header_when_disabled(self):
        """model=echo-test → 200 不带响应头（桩的原值回显已退役；transformations 无头
        回退 llmRequest.model，语义等价）。"""
        status, hdr, body = _post_classify(json.dumps({"model": "echo-test"}).encode())
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        self.assertIsNone(body["resolved_model"])

    def test_real_model_no_header_when_disabled(self):
        """真实旗舰模型名（gpt-5.6-luna）→ 200 不带响应头。"""
        status, hdr, _ = _post_classify(json.dumps({"model": "gpt-5.6-luna"}).encode())
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)

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

    def test_never_blocks(self):
        """协议锚点：全部输入形态都 200（extAuthz 2xx=放行，本端点无 4xx/5xx 路径）。"""
        for raw in (b"", b"not-json", json.dumps({"model": "auto"}).encode(),
                    json.dumps({"model": "echo-test"}).encode(), b"\xff\xfe"):
            status, _, _ = _post_classify(raw)
            self.assertEqual(status, 200)

    def test_all_methods_200_no_header(self):
        """全方法恒 200 不带响应头（#115 坑 1：extAuthz 按原请求方法转发授权调用——
        GET /v1/models 等会打到同方法 /classify；非 2xx 会被网关当 deny 直回客户端，
        failureMode 只管传输层错误）。"""
        for method in ("GET", "PUT", "DELETE", "OPTIONS", "HEAD"):
            with self.subTest(method=method):
                req = urllib.request.Request(_BASE + "/classify", method=method)
                with urllib.request.urlopen(req, timeout=5) as r:
                    self.assertEqual(r.status, 200)
                    self.assertIsNone(r.headers.get("x-resolved-model"))
                    if method != "HEAD":  # HEAD 无响应体
                        self.assertIsNone(json.load(r)["resolved_model"])


if __name__ == "__main__":
    unittest.main()
