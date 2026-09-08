#!/usr/bin/env python3
"""大请求体降级送检测试（issue #137 P1-1）：体超 MAX_BODY 截断后 json.loads 必抛，
原路径落 500——网关 failureMode=failOpen 下任何 5xx 都等于全层放行（撤防）。
修复后语义：截得原始字节容错文本抽取，照常过密钥/词表/EDM 同一检测管线——
命中仍 451，未命中放行，绝不因体大/截断/畸形 Content-Length 而 5xx。

seam 纪律同 test_block_observe：词表/format-rules/settings/shadow_log 全部指向 tmp。
不走真实 socket——BytesIO 假连接直驱 Handler：>MAX_BODY 体的 TCP 尾部 RST 会让
活栈用例不稳定，且被测行为（截断/解析失败降级）与网络无关。
"""
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as shim_app  # noqa: E402


class _Buf(io.BytesIO):
    def close(self):  # Handler.finish() 会 close 流；保留缓冲供断言
        pass


class _FakeConn:
    """BaseHTTPRequestHandler 最小连接替身：读侧 makefile 回 BytesIO；
    写侧 wbufsize=0 走 socketserver._SocketWriter → sendall 收集。"""

    def __init__(self, request_bytes: bytes):
        self.rbuf = _Buf(request_bytes)
        self.wbuf = _Buf()

    def makefile(self, mode, *args):
        return self.rbuf

    def sendall(self, data):
        self.wbuf.write(data)


def _raw_post(path: str, body: bytes, content_length=None):
    """按原始字节驱动 shim Handler（可表达截断体/非法 Content-Length），返回 (HTTP状态, JSON应答)。"""
    lines = [f"POST {path} HTTP/1.0", "Host: shim", "Content-Type: application/json"]
    lines.append(f"Content-Length: {len(body) if content_length is None else content_length}")
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode() + body
    conn = _FakeConn(raw)
    shim_app.Handler(conn, ("127.0.0.1", 0), None)
    head, _, resp_body = conn.wbuf.getvalue().partition(b"\r\n\r\n")
    status = int(head.split(None, 2)[1])
    return status, json.loads(resp_body or b"{}")


def _req_body(content: str) -> bytes:
    return json.dumps({"body": {"messages": [{"role": "user", "content": content}]}},
                      ensure_ascii=False).encode()


def _resp_body(content: str) -> bytes:
    return json.dumps({"body": {"choices": [{"message": {"content": content}}]}},
                      ensure_ascii=False).encode()


class DegradedBodyTest(unittest.TestCase):
    """请求/响应两侧：超 MAX_BODY 体截断后不再 500，降级扫描命中仍 451。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.wordlist = os.path.join(d, "terms.json")
        with open(self.wordlist, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "terms": [{"value": "凤凰计划", "rule_id": "confidential.codename"}]}, f,
                      ensure_ascii=False)
        self.format_rules = os.path.join(d, "format-rules.json")
        with open(self.format_rules, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "rules": [
                {"code": "secrets.test_sk", "action": "reject", "enabled": True,
                 "shim_patterns": ["sk[A-Za-z0-9]{8,}"]},
            ]}, f)
        self.settings = os.path.join(d, "settings.json")
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"version": 1}, f)
        self.shadow = os.path.join(d, "shadow.jsonl")
        self._saved = (shim_app.WORDLIST_PATH, shim_app.SETTINGS_PATH, shim_app.FORMAT_RULES_PATH)
        shim_app.WORDLIST_PATH = self.wordlist
        shim_app.SETTINGS_PATH = self.settings
        shim_app.FORMAT_RULES_PATH = self.format_rules
        self._saved_env = os.environ.get("SHADOW_LOG_PATH")
        os.environ["SHADOW_LOG_PATH"] = self.shadow

    def tearDown(self):
        shim_app.WORDLIST_PATH, shim_app.SETTINGS_PATH, shim_app.FORMAT_RULES_PATH = self._saved
        if self._saved_env is None:
            os.environ.pop("SHADOW_LOG_PATH", None)
        else:
            os.environ["SHADOW_LOG_PATH"] = self._saved_env
        self._tmp.cleanup()

    def test_oversized_request_with_term_451(self):
        # >256KB 请求体（商密词在截断点之前）→ 降级扫描命中 → 451（修复前是 500 → failOpen 放行）
        body = _req_body("项目纪要：" + "凤凰计划" + "进展同步。" + "x" * (300 * 1024))
        self.assertGreater(len(body), shim_app.MAX_BODY)
        status, resp = _raw_post("/request", body)
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)
        self.assertIn("confidential.codename", resp["action"]["reason"])

    def test_oversized_request_with_secret_451(self):
        body = _req_body("我的 key 是 sk-ABCDEFGH12345 。" + "x" * (300 * 1024))
        self.assertGreater(len(body), shim_app.MAX_BODY)
        status, resp = _raw_post("/request", body)
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)
        self.assertIn("secrets.test_sk", resp["action"]["reason"])

    def test_oversized_clean_request_passes_no_5xx(self):
        # >256KB 无敏感内容 → 放行且不 5xx（敏感词在截断点之后不可见，记账为降级路径已知边界）
        body = _req_body("y" * (300 * 1024) + "凤凰计划")  # 词在 256KB 截断点之后
        status, resp = _raw_post("/request", body)
        self.assertEqual(status, 200)
        self.assertNotEqual(status, 500)
        self.assertEqual(resp["action"].get("reason"), "pass")

    def test_exactly_max_body_normal_json_path(self):
        # 恰 MAX_BODY 的合法 JSON：不截断走正常解析路径（防修复把边界体也打进降级）
        base = _req_body("凤凰计划")
        body = _req_body("凤凰计划" + "z" * (shim_app.MAX_BODY - len(base)))
        self.assertEqual(len(body), shim_app.MAX_BODY)
        status, resp = _raw_post("/request", body)
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)

    def test_malformed_content_length_no_5xx(self):
        # 非法 Content-Length：不崩连接、不 500，按无体处理放行
        status, resp = _raw_post("/request", _req_body("凤凰计划"), content_length="abc")
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("reason"), "pass")

    def test_negative_content_length_no_5xx(self):
        # 负 Content-Length：修复前 rfile.read(负数) 读流至 EOF（真实连接上会挂住）
        status, resp = _raw_post("/request", _req_body("今天天气怎么样"), content_length="-5")
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("reason"), "pass")

    def test_oversized_response_with_term_451(self):
        # /response 同构路径（issue #137：1650 行同样截断 500）
        body = _resp_body("结论：凤凰计划按期推进。" + "x" * (300 * 1024))
        self.assertGreater(len(body), shim_app.MAX_BODY)
        status, resp = _raw_post("/response", body)
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)

    def test_oversized_clean_response_passes_no_5xx(self):
        body = _resp_body("y" * (300 * 1024))
        status, resp = _raw_post("/response", body)
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("reason"), "pass")


if __name__ == "__main__":
    unittest.main()
