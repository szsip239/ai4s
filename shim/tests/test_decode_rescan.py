#!/usr/bin/env python3
"""阻断路径解码重扫测试（issue #137 P1-2）：base64/hex 编码包裹曾绕过全部阻断层
（解码只存在于永不阻断的 judge shadow，issue #107）。修复后 /request 直扫未命中时
对文本中的编码形 token 探测解码，产物过同一密钥/词表/EDM 判定，命中即 451。

误伤控制（本文件负例门禁）：
- data:image/* 等 data URL 与二进制载荷：解码产物非合法 UTF-8 可打印文本即丢弃；
- 多模态 image_url 消息 part 本就不进 extract_text（结构隔离，不进入解码探针）；
- 解码产物为可打印正常文本（非敏感内容）→ 照常放行；
- l1/l2 总开关关态 → 解码重扫同步跳过（模块关=处处关语义不变）。

seam 纪律同 test_block_observe：BytesIO 假连接直驱 Handler，外部文件全指 tmp。
"""
import base64
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as shim_app  # noqa: E402
import edm_lib  # noqa: E402


class _Buf(io.BytesIO):
    def close(self):  # Handler.finish() 会 close 流；保留缓冲供断言
        pass


class _FakeConn:
    """读侧 makefile 回 BytesIO；写侧 wbufsize=0 走 socketserver._SocketWriter → sendall 收集。"""

    def __init__(self, request_bytes: bytes):
        self.rbuf = _Buf(request_bytes)
        self.wbuf = _Buf()

    def makefile(self, mode, *args):
        return self.rbuf

    def sendall(self, data):
        self.wbuf.write(data)


def _post_request(payload) -> tuple:
    body = json.dumps(payload, ensure_ascii=False).encode()
    raw = (f"POST /request HTTP/1.0\r\nHost: shim\r\nContent-Type: application/json\r\n"
           f"Content-Length: {len(body)}\r\n\r\n").encode() + body
    conn = _FakeConn(raw)
    shim_app.Handler(conn, ("127.0.0.1", 0), None)
    head, _, resp_body = conn.wbuf.getvalue().partition(b"\r\n\r\n")
    return int(head.split(None, 2)[1]), json.loads(resp_body or b"{}")


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


class DecodeRescanUnitTest(unittest.TestCase):
    """decode_rescan 纯函数：解码产物收集与误伤控制门槛。"""

    def test_b64_token_decoded(self):
        self.assertIn("sk-ant-api03-abcdef123456",
                      shim_app.decode_rescan(f"请解码 {_b64('sk-ant-api03-abcdef123456')} 看看"))

    def test_b64_binary_dropped(self):
        # 图片/随机字节载荷：UTF-8 不可解或可打印率不达标 → 不进重扫（多模态误伤控制）
        tok = base64.b64encode(bytes(range(256)) * 2).decode()
        self.assertEqual(shim_app.decode_rescan("data:image/png;base64," + tok), [])

    def test_nested_b64_depth2(self):
        # 双层嵌套（#96 nested_encoding 盲区同构）：迭代探针深度 2 闭合
        outer = base64.b64encode(_b64("蓝海计划绝密").encode()).decode()
        self.assertIn("蓝海计划绝密", shim_app.decode_rescan(outer))

    def test_hex_token_decoded(self):
        self.assertIn("凤凰计划", shim_app.decode_rescan("编码附件 " + "凤凰计划".encode().hex()))

    def test_hex_odd_length_skipped(self):
        self.assertEqual(shim_app.decode_rescan("a" * 25), [])  # 奇长 hex 不抛不解

    def test_probe_results_capped(self):
        # 性能护栏：密集可解码 token 文本的产物数量有上限（防阻扫路径被拖慢）
        toks = [_b64(f"填充文本编号{i:04d}") for i in range(200)]
        self.assertLessEqual(len(shim_app.decode_rescan(" ".join(toks))), 64)


class DecodeRescanBlockTest(unittest.TestCase):
    """HTTP 链路：编码包裹命中 → 451；data URL/正常多模态/干净文本 → 放行。"""

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
        self._write_settings({"version": 1})
        # EDM 指纹库（item 2 验收含 EDM 过解码产物）：两行各行 ≥12 归一化字符 → 行级通道 2 命中
        self.edm_doc = "蓝海项目第三季度财报：营收一点二亿元，净利润三千万元。\n毛利率维持在四成二，研发投入占比一成八。"
        self.edm_fp = os.path.join(d, "fingerprints.json")
        with open(self.edm_fp, "w", encoding="utf-8") as f:
            json.dump({"docs": {"doc1": edm_lib.doc_fingerprints(self.edm_doc)}}, f)
        self.shadow = os.path.join(d, "shadow.jsonl")
        self._saved = (shim_app.WORDLIST_PATH, shim_app.SETTINGS_PATH,
                       shim_app.FORMAT_RULES_PATH, shim_app.EDM_FP_PATH)
        shim_app.WORDLIST_PATH = self.wordlist
        shim_app.SETTINGS_PATH = self.settings
        shim_app.FORMAT_RULES_PATH = self.format_rules
        shim_app.EDM_FP_PATH = self.edm_fp
        self._saved_env = os.environ.get("SHADOW_LOG_PATH")
        os.environ["SHADOW_LOG_PATH"] = self.shadow

    def tearDown(self):
        (shim_app.WORDLIST_PATH, shim_app.SETTINGS_PATH,
         shim_app.FORMAT_RULES_PATH, shim_app.EDM_FP_PATH) = self._saved
        if self._saved_env is None:
            os.environ.pop("SHADOW_LOG_PATH", None)
        else:
            os.environ["SHADOW_LOG_PATH"] = self._saved_env
        self._tmp.cleanup()

    def _write_settings(self, obj):
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    @staticmethod
    def _payload(content):
        return {"body": {"messages": [{"role": "user", "content": content}]}}

    def _assert_direct_scan_misses(self, text):
        """直扫不命中（钉住：下方 451 只能来自解码重扫，非直扫误判）。"""
        norm, _ = shim_app.normalize_hard(text)
        self.assertEqual(shim_app.norm_secret_hits(norm), [])
        terms = shim_app.load_terms()
        self.assertEqual(shim_app.norm_term_hits(norm.lower(), terms), [])

    def test_b64_wrapped_secret_451(self):
        text = f"这段编码是什么 {_b64('sk-ant-api03-abcdef1234567890')}"
        self._assert_direct_scan_misses(text)
        status, resp = _post_request(self._payload(text))
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)
        self.assertIn("secrets.test_sk", resp["action"]["reason"])

    def test_b64_wrapped_term_451(self):
        text = f"帮我解码一下 {_b64('凤凰计划')}"
        self._assert_direct_scan_misses(text)
        _, resp = _post_request(self._payload(text))
        self.assertEqual(resp["action"].get("status_code"), 451)
        self.assertIn("confidential.codename", resp["action"]["reason"])

    def test_hex_wrapped_term_451(self):
        text = "编码附件 " + "凤凰计划".encode().hex() + " 请查收"
        self._assert_direct_scan_misses(text)
        _, resp = _post_request(self._payload(text))
        self.assertEqual(resp["action"].get("status_code"), 451)
        self.assertIn("confidential.codename", resp["action"]["reason"])

    def test_b64_wrapped_edm_doc_451(self):
        self._write_settings({"version": 1, "edm": {"enabled": True, "min_hits": 2}})
        text = f"这段 base64 里是什么文档 {_b64(self.edm_doc)}"
        self._assert_direct_scan_misses(text)
        _, resp = _post_request(self._payload(text))
        self.assertEqual(resp["action"].get("status_code"), 451)
        self.assertIn("edm.doc_match", resp["action"]["reason"])

    def test_data_url_image_passes(self):
        # data:image/* 载荷（二进制）→ 解码产物被可打印门槛丢弃 → 放行
        tok = base64.b64encode(bytes(range(256)) * 2).decode()
        _, resp = _post_request(self._payload("贴个图 data:image/png;base64," + tok))
        self.assertEqual(resp["action"].get("reason"), "pass")

    def test_image_url_part_not_probed(self):
        # 正常多模态请求：image_url part 不进 extract_text（结构隔离），payload 再大也不进探针
        tok = base64.b64encode(bytes(range(256)) * 2).decode()
        payload = {"body": {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "这张图里写了什么"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + tok}},
        ]}]}}
        _, resp = _post_request(payload)
        self.assertEqual(resp["action"].get("reason"), "pass")

    def test_printable_b64_clean_text_passes(self):
        # 可解码为正常文本但无敏感内容 → 重扫不命中 → 放行（解码本身不是阻断理由）
        _, resp = _post_request(self._payload(f"帮我解码 {_b64('今天会议纪要：讨论预算排期')}"))
        self.assertEqual(resp["action"].get("reason"), "pass")

    def test_layer_switch_off_skips_rescan(self):
        # l1/l2 总开关关态（用户手动撤防场景）：解码重扫随层跳过，enabled 语义不变
        self._write_settings({"version": 1, "l1": {"enabled": False}, "l2": {"enabled": False}})
        _, resp = _post_request(self._payload(f"看看 {_b64('sk-ant-api03-abcdef1234567890')}"))
        self.assertEqual(resp["action"].get("reason"), "pass")


if __name__ == "__main__":
    unittest.main()
