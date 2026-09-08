#!/usr/bin/env python3
"""auto 路由 /classify 端点协议锚单测（issue #115 桩退役收口，issue #117 行为基线）。

issue #115 的桩行为（auto→echo-test 写死、其他合法 model 原值回显）已随 #117 真实分类器
上线退役。本文件锚定桩退役后的协议不变量（routing.enabled=false 缺省态=现网行为零变化）：

- 协议语义：任何输入都 200（2xx=放行；非 2xx 会被网关当 deny 决策，fail-open 由网关
  failureMode=allow 管，不由本端点）——**唯一例外（issue #140 补漏）**：tool 载体
  （tool_calls arguments 等）内嵌 L1 密钥红线命中时返 451 deny（agentgateway webhook
  恒默认 scope 看不到 toolInput，本端点是栈内唯一可见位置，详见 ToolCarrierL1Test）；
  issue #139 起前置共享密钥守卫：请求头 X-Shim-Local-Token 与 env SHIM_LOCAL_TOKEN
  不匹配或 env 未配置 → 恒 403（fail-closed）；本文件用例统一带有效头（模块级 setdefault），
  守卫本身的 403 契约见 test_detection_surface.py LocalTokenGuardTest；
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
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

# 让测试可 import shim 目录下的 app（discover 从 shim/tests 启动）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as shim_app  # noqa: E402
import bypass_keys  # noqa: E402

_SHIM = ThreadingHTTPServer(("127.0.0.1", 0), shim_app.Handler)
threading.Thread(target=_SHIM.serve_forever, daemon=True).start()
_BASE = f"http://127.0.0.1:{_SHIM.server_address[1]}"

# issue #139：/classify 挂共享密钥守卫（env 未配置恒 403）——测试进程配置固定 token，
# 用例统一带有效头（守卫 403 契约由 test_detection_surface.py 锚定）
os.environ.setdefault("SHIM_LOCAL_TOKEN", "test-local-token")
_LOCAL_HEADERS = {"X-Shim-Local-Token": os.environ["SHIM_LOCAL_TOKEN"]}

# 缺省态隔离（对齐 JudgeShadowMaskTest env 纪律）：SETTINGS_PATH 指到不存在路径
# （routing 节缺席=disabled），并摘除开发机可能导出的 ROUTING_* env
_TMP = tempfile.TemporaryDirectory()
shim_app.SETTINGS_PATH = os.path.join(_TMP.name, "no-such-settings.json")
for _k in ("ROUTING_ENABLED", "ROUTING_THRESHOLD", "ROUTING_TIMEOUT", "ROUTING_MAX_CONCURRENCY"):
    os.environ.pop(_k, None)

# issue #140 补漏：tool 载体扫描读 format-rules 真实口径（容器内默认 /dlp/... 本机不存在，
# fail-open 空规则会让 ToolCarrierL1Test 全漏）——指向仓库单一源，与现网同规则
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
shim_app.FORMAT_RULES_PATH = os.path.join(_REPO, "deploy", "dlp", "format-rules.json")


def _post_classify(raw: bytes):
    """POST /classify，返回 (status, x-resolved-model 头或 None, 响应体 dict)。"""
    req = urllib.request.Request(
        _BASE + "/classify", data=raw,
        headers={"Content-Type": "application/json", **_LOCAL_HEADERS})
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
        """协议锚点：干净输入（无 tool 载体红线命中）全形态 200（extAuthz 2xx=放行；
        唯一 4xx 路径=tool 载体 L1 命中 451，见 ToolCarrierL1Test）。"""
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
                req = urllib.request.Request(_BASE + "/classify", method=method,
                                             headers=_LOCAL_HEADERS)
                with urllib.request.urlopen(req, timeout=5) as r:
                    self.assertEqual(r.status, 200)
                    self.assertIsNone(r.headers.get("x-resolved-model"))
                    if method != "HEAD":  # HEAD 无响应体
                        self.assertIsNone(json.load(r)["resolved_model"])


class ToolCarrierL1Test(unittest.TestCase):
    """issue #140 补漏：tool 载体 L1 红线扫描移入 /classify（agentgateway promptGuard
    webhook 恒默认 scope（systemPrompt+messages）——assistant tool_calls arguments
    （toolInput）不进 webhook body，/request 链路结构性看不到该载体（上游硬限制：
    webhook guards always inspect the default scope）；/classify extAuthz
    includeRequestBody 收完整原始 body，是栈内唯一可见位置。
    命中即 451（extAuthz 非 2xx=deny 直回客户端）+ block 条（巡检告警复用）。
    只扫载体字段（_tool_carrier_texts），content 正文归 /request webhook 面不重复扫。"""

    _SECRET_MSGS = [{"role": "user", "content": "继续"},
                    {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {
                            "name": "read_config",
                            "arguments": '{"path": "a", "token": "sk-ant-api03-x1x2x3x4x5x6x7x8x9x0y1y2"}'}}]}]

    def _post(self, obj, token=None):
        raw = json.dumps(obj).encode()
        headers = {"Content-Type": "application/json", **_LOCAL_HEADERS}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(_BASE + "/classify", data=raw, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_tool_calls_args_secret_451(self):
        """回归形态（dlp-regression toolscope 段）：assistant tool_calls arguments 内嵌
        sk-ant key → 451 + shim 报文格式（code=规则族）。"""
        status, body = self._post({"model": "echo-test", "messages": self._SECRET_MSGS})
        self.assertEqual(status, 451)
        self.assertEqual(body["error"]["code"], "secrets.anthropic_sk")
        self.assertIn("Blocked by ai4s DLP", body["error"]["message"])

    def test_tool_calls_args_dict_form_451(self):
        """非规范形态（arguments 为 dict，透传代理可能不序列化）同兜。"""
        msgs = [{"role": "assistant", "content": None, "tool_calls": [
            {"id": "c", "type": "function", "function": {
                "name": "f", "arguments": {"token": "ghp_AbCdEfGhIjKlMnOpQrStUvWx1234"}}}]}]
        status, body = self._post({"model": "echo-test", "messages": msgs})
        self.assertEqual(status, 451)
        self.assertEqual(body["error"]["code"], "secrets.github_token")

    def test_clean_tool_calls_200(self):
        """干净 tool 调用放行（不误伤正常 function calling 流量）。"""
        msgs = [{"role": "user", "content": "继续"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c", "type": "function", "function": {"name": "f", "arguments": '{"a": 1}'}}]}]
        status, _ = self._post({"model": "echo-test", "messages": msgs})
        self.assertEqual(status, 200)

    def test_content_body_secret_out_of_scope(self):
        """分工钉档：content 正文的密钥归 /request webhook 默认 scope 面，本端点不重复拦。"""
        status, _ = self._post({"model": "echo-test", "messages": [
            {"role": "user", "content": "key sk-ant-api03-x1x2x3x4x5x6x7x8x9x0y1y2 看下"}]})
        self.assertEqual(status, 200)

    def test_l1_disabled_passes(self):
        """l1 总开关语义同 /request（issue #40）：关闭即本兜底层同撤。"""
        p = os.path.join(_TMP.name, "settings-l1off.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"l1": {"enabled": False}}, f)
        orig = shim_app.SETTINGS_PATH
        shim_app.SETTINGS_PATH = p
        try:
            status, _ = self._post({"model": "echo-test", "messages": self._SECRET_MSGS})
            self.assertEqual(status, 200)
        finally:
            shim_app.SETTINGS_PATH = orig

    def test_bypass_key_layers_l1_passes(self):
        """key 绕行语义同 /request（issue #129）：layers 含 l1 的白名单 key 跳过本扫描。"""
        tok = "bypass-carrier-token"
        bp_path = os.path.join(_TMP.name, "bypass-keys.json")
        bypass_keys.add(tok, "t", "layers", ["l1"], "test", path=bp_path)
        os.environ["BYPASS_KEYS_PATH"] = bp_path
        try:
            status, _ = self._post({"model": "echo-test", "messages": self._SECRET_MSGS}, token=tok)
            self.assertEqual(status, 200)
        finally:
            os.environ.pop("BYPASS_KEYS_PATH", None)

    def test_bypass_key_other_layers_still_451(self):
        """绕行精确性：layers 不含 l1（如只绕 pg）的 key 不豁免本扫描。"""
        tok = "bypass-other-layer-token"
        bp_path = os.path.join(_TMP.name, "bypass-keys2.json")
        bypass_keys.add(tok, "t", "layers", ["pg"], "test", path=bp_path)
        os.environ["BYPASS_KEYS_PATH"] = bp_path
        try:
            status, _ = self._post({"model": "echo-test", "messages": self._SECRET_MSGS}, token=tok)
            self.assertEqual(status, 451)
        finally:
            os.environ.pop("BYPASS_KEYS_PATH", None)


if __name__ == "__main__":
    unittest.main()
