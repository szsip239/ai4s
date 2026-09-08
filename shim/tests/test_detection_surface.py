#!/usr/bin/env python3
"""issue #139 检测面批次（2026-09-08 对抗性审查）：

1) 工具调用载体纳入检测面：assistant tool_calls[].function.arguments（OpenAI 形态）、
   Anthropic content blocks（tool_use input / tool_result content）此前不进 extract_text
   （请求侧直扫/EDM/词表全盲），也不进 mask_response_body（响应侧回扫全盲）——
   密钥藏进工具参数即可绕过全链。掩码管线（norm_mask_messages/mask_message_contents）
   同口径扩展：extract_text 是 judge 外发输入源（extract_text(masked_msgs)），
   载体字段不掩码等于把原文外发（issue #93 纪律破口）。
2) normalize_hard 升级：NFKC（含兼容象形字/连字/全角）+ Cyrillic/Greek 同形字折叠 +
   繁简映射表扩到常用字量级 + 零宽字符清除（对齐 pg_engine.normalize_for_scoring
   语义子集，不 import 防环）。
3) /judge-test /feishu-alert /classify 共享密钥守卫：env SHIM_LOCAL_TOKEN +
   请求头 X-Shim-Local-Token 匹配才放行；env 未配置/空 → 恒 403（fail-closed）。

seam 纪律同 test_degraded_body：词表/format-rules/settings 指向 tmp，BytesIO 假连接
直驱 Handler，不走真实 socket。守卫用例操纵进程级 env（SHIM_LOCAL_TOKEN 每请求热读），
setUp/tearDown 严格保存恢复。
"""
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as shim_app  # noqa: E402

_LOCAL_TOKEN = "test-local-token-139"


class _Buf(io.BytesIO):
    def close(self):  # Handler.finish() 会 close 流；保留缓冲供断言
        pass


class _FakeConn:
    """BaseHTTPRequestHandler 最小连接替身（同 test_degraded_body）。"""

    def __init__(self, request_bytes: bytes):
        self.rbuf = _Buf(request_bytes)
        self.wbuf = _Buf()

    def makefile(self, mode, *args):
        return self.rbuf

    def sendall(self, data):
        self.wbuf.write(data)


def _raw_req(method: str, path: str, body: bytes = b"", headers=None):
    """按原始字节驱动 shim Handler，返回 (HTTP状态, 应答体字节)。"""
    lines = [f"{method} {path} HTTP/1.0", "Host: shim"]
    if body:
        lines.append("Content-Type: application/json")
        lines.append(f"Content-Length: {len(body)}")
    for k, v in (headers or {}).items():
        lines.append(f"{k}: {v}")
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode() + body
    conn = _FakeConn(raw)
    shim_app.Handler(conn, ("127.0.0.1", 0), None)
    head, _, resp_body = conn.wbuf.getvalue().partition(b"\r\n\r\n")
    status = int(head.split(None, 2)[1])
    return status, resp_body


def _post(path: str, payload: dict, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode()
    status, resp_body = _raw_req("POST", path, body, headers)
    return status, json.loads(resp_body or b"{}")


def _req_payload(messages) -> dict:
    return {"body": {"messages": messages}}


class SurfaceSeamBase(unittest.TestCase):
    """公共 seam：tmp 词表（含繁体/同形字用例词）+ tmp format-rules（sk reject + 手机号 mask）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.wordlist = os.path.join(d, "terms.json")
        with open(self.wordlist, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "terms": [
                {"value": "凤凰计划", "rule_id": "confidential.codename"},
                {"value": "灯塔工单系统", "rule_id": "confidential.codename"},
                {"value": "砚台审计平台", "rule_id": "confidential.codename"},
                {"value": "不夜城", "rule_id": "confidential.codename"},
                # 混排词（含 CJK → 非 simple token，走 shim 子串直配）——词表不放纯 ASCII
                # 词：analyze() 会把 simple token 托付 Presidio HTTP，单测环境无该服务会 500
                {"value": "蓝鲸BlueWhale系统", "rule_id": "confidential.codename"},
            ]}, f, ensure_ascii=False)
        self.format_rules = os.path.join(d, "format-rules.json")
        with open(self.format_rules, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "rules": [
                {"code": "secrets.test_sk", "action": "reject", "enabled": True,
                 "shim_patterns": ["sk[A-Za-z0-9]{8,}"]},
                {"code": "pii.phone", "action": "mask", "enabled": True, "entity": "PHONE",
                 "replacement": "【PHONE】", "shim_patterns": ["1[3-9][0-9]{9}"]},
            ]}, f, ensure_ascii=False)
        self.settings = os.path.join(d, "settings.json")
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"version": 1}, f)
        self._saved = (shim_app.WORDLIST_PATH, shim_app.SETTINGS_PATH, shim_app.FORMAT_RULES_PATH)
        shim_app.WORDLIST_PATH = self.wordlist
        shim_app.SETTINGS_PATH = self.settings
        shim_app.FORMAT_RULES_PATH = self.format_rules
        self._saved_env = {k: os.environ.get(k) for k in ("SHADOW_LOG_PATH", "SHIM_LOCAL_TOKEN")}
        os.environ["SHADOW_LOG_PATH"] = os.path.join(d, "shadow.jsonl")
        self._saved_feishu = shim_app.FEISHU_WEBHOOK
        shim_app.FEISHU_WEBHOOK = ""  # 守卫放行的 /feishu-alert 用例不得真发（webhook 空 → 502）

    def tearDown(self):
        shim_app.WORDLIST_PATH, shim_app.SETTINGS_PATH, shim_app.FORMAT_RULES_PATH = self._saved
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shim_app.FEISHU_WEBHOOK = self._saved_feishu
        self._tmp.cleanup()


class ToolCallsExtractTest(unittest.TestCase):
    """项 1 提取侧：extract_text 纳入 OpenAI tool_calls 参数与 Anthropic tool_use/tool_result blocks。"""

    def test_tool_calls_arguments_extracted(self):
        # OpenAI 形态：assistant tool_calls[].function.arguments（JSON 字符串）进检测面
        msgs = [{"role": "assistant", "content": None,
                 "tool_calls": [{"id": "c1", "type": "function",
                                 "function": {"name": "read_file",
                                              "arguments": '{"path": "/docs/凤凰计划.txt"}'}}]}]
        self.assertIn("凤凰计划", shim_app.extract_text(msgs))

    def test_tool_calls_arguments_dict_form_extracted(self):
        # 容错：arguments 非规范 dict 形态（透传代理可能不序列化）→ JSON 化进检测面
        msgs = [{"role": "assistant", "content": "",
                 "tool_calls": [{"id": "c1", "type": "function",
                                 "function": {"name": "read_file",
                                              "arguments": {"path": "蓝鲸系统"}}}]}]
        self.assertIn("蓝鲸系统", shim_app.extract_text(msgs))

    def test_tool_role_content_extracted(self):
        # 回归锚：tool 角色 str content 本就在检测面（修复不得弄丢）
        msgs = [{"role": "tool", "tool_call_id": "c1", "content": "文件内容：凤凰计划 进展"}]
        self.assertIn("凤凰计划", shim_app.extract_text(msgs))

    def test_anthropic_tool_use_input_extracted(self):
        # Anthropic 形态：content blocks 里 tool_use 的 input（dict → JSON 化）
        msgs = [{"role": "assistant", "content": [
            {"type": "text", "text": "我先查一下"},
            {"type": "tool_use", "id": "t1", "name": "read", "input": {"path": "灯塔工单系统"}},
        ]}]
        text = shim_app.extract_text(msgs)
        self.assertIn("我先查一下", text)
        self.assertIn("灯塔工单系统", text)

    def test_anthropic_tool_result_extracted(self):
        # Anthropic 形态：tool_result 的 content（str 与嵌套 text blocks 两形态）
        msgs = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "结果：凤凰计划"},
                {"type": "tool_result", "tool_use_id": "t2",
                 "content": [{"type": "text", "text": "星云客服系统"}]},
            ]},
        ]
        text = shim_app.extract_text(msgs)
        self.assertIn("凤凰计划", text)
        self.assertIn("星云客服系统", text)


class ToolCallsPipelineTest(SurfaceSeamBase):
    """项 1 管线侧：/request 451、mask_response_body 回扫掩码、judge 外发掩码同口径。"""

    def test_request_secret_in_tool_calls_451(self):
        # 密钥藏 tool_calls arguments：修复前绕过全链，修复后 451
        msgs = [{"role": "assistant", "content": None,
                 "tool_calls": [{"id": "c1", "type": "function",
                                 "function": {"name": "curl",
                                              "arguments": '{"key": "sk-ABCDEFGH12345"}'}}]}]
        status, resp = _post("/request", _req_payload(msgs))
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)
        self.assertIn("secrets.test_sk", resp["action"]["reason"])

    def test_request_term_in_anthropic_tool_result_451(self):
        msgs = [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "凤凰计划 二期纪要"}]}]
        status, resp = _post("/request", _req_payload(msgs))
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)
        self.assertIn("confidential.codename", resp["action"]["reason"])

    def test_request_clean_tool_calls_pass(self):
        # 负例锚：干净 tool_calls 不误伤
        msgs = [{"role": "assistant", "content": None,
                 "tool_calls": [{"id": "c1", "type": "function",
                                 "function": {"name": "ls", "arguments": '{"dir": "/tmp"}'}}]}]
        status, resp = _post("/request", _req_payload(msgs))
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("reason"), "pass")

    def test_mask_response_body_tool_calls_masked(self):
        # 响应侧回扫：message.tool_calls arguments 命中 → 参数串整体掩码 + 实体列表
        body = {"choices": [{"message": {"role": "assistant", "content": None,
                                         "tool_calls": [{"id": "c1", "type": "function",
                                                         "function": {"name": "send",
                                                                      "arguments": "sk-ABCDEFGH12345"}}]}}]}
        out, hits = shim_app.mask_response_body(body)
        self.assertIn("secrets.test_sk", hits)
        args = out["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        self.assertNotIn("sk-ABCDEFGH12345", args)
        self.assertIn("已屏蔽", args)

    def test_mask_response_body_tool_calls_term_hit(self):
        # 响应侧词表命中同理（arguments 里藏商密词）
        body = {"choices": [{"message": {"role": "assistant", "content": None,
                                         "tool_calls": [{"id": "c1", "type": "function",
                                                         "function": {"name": "search",
                                                                      "arguments": '{"q": "凤凰计划"}'}}]}}]}
        _, hits = shim_app.mask_response_body(body)
        self.assertIn("confidential.codename", hits)

    def test_mask_response_body_clean_tool_calls_untouched(self):
        # 负例锚：干净 arguments 原样保留（不误改结构）
        body = {"choices": [{"message": {"role": "assistant", "content": None,
                                         "tool_calls": [{"id": "c1", "type": "function",
                                                         "function": {"name": "ls", "arguments": '{"dir": "/tmp"}'}}]}}]}
        out, hits = shim_app.mask_response_body(body)
        self.assertEqual(hits, [])
        self.assertEqual(out["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"],
                         '{"dir": "/tmp"}')

    def test_response_endpoint_secret_in_tool_calls_451(self):
        # /response 集成：choices message tool_calls 藏密钥 → 451（同 content 命中语义）
        body = {"choices": [{"message": {"role": "assistant", "content": None,
                                         "tool_calls": [{"id": "c1", "type": "function",
                                                         "function": {"name": "x",
                                                                      "arguments": "sk-ABCDEFGH12345"}}]}}]}
        status, resp = _post("/response", {"body": body})
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)

    def test_judge_input_pipeline_masks_tool_calls(self):
        # issue #93 纪律：extract_text 是 judge 外发输入源——载体字段必须同口径掩码，
        # 否则 PII 经 tool_calls 原文外发 judge（外部 API）
        msgs = [{"role": "assistant", "content": None,
                 "tool_calls": [{"id": "c1", "type": "function",
                                 "function": {"name": "sms",
                                              "arguments": '{"to": "13800138000"}'}}]}]
        masked, any_masked, ents = shim_app.norm_mask_messages(msgs)
        self.assertTrue(any_masked)
        self.assertIn("PHONE", ents)
        self.assertNotIn("13800138000", shim_app.extract_text(masked))
        self.assertIn("【PHONE】", masked[0]["tool_calls"][0]["function"]["arguments"])

    def test_judge_input_pipeline_masks_anthropic_tool_use(self):
        # Anthropic tool_use input（dict 形态）深走掩码
        msgs = [{"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "sms",
             "input": {"to": "13800138000", "text": "排期确认"}}]}]
        masked, any_masked, _ = shim_app.norm_mask_messages(msgs)
        self.assertTrue(any_masked)
        self.assertNotIn("13800138000", shim_app.extract_text(masked))
        self.assertEqual(masked[0]["content"][0]["input"]["text"], "排期确认")  # 非命中字段不动


class NormalizeHardTest(SurfaceSeamBase):
    """项 2：NFKC + 同形字折叠 + 繁简表扩充 + 零宽清除。"""

    def test_fullwidth_still_folds(self):
        # 回归锚：全角→半角（原 _FULLWIDTH 行为由 NFKC 接管，不退化）
        norm, _ = shim_app.normalize_hard("ｓｋＡＢＣＤＥＦＧＨ１２３４５")
        self.assertEqual(norm, "skABCDEFGH12345")

    def test_nfkc_ligature_idxmap(self):
        # NFKC 兼容分解一对多（ﬁ→fi）：产出字符全部回映同一原文下标（idx_map 不变量）
        norm, idx = shim_app.normalize_hard("aﬁb")
        self.assertEqual(norm, "afib")
        self.assertEqual(idx, [0, 1, 1, 2])

    def test_nfkc_compat_ideograph_term_hit(self):
        # 兼容象形字 不(U+F967) → 不：词表命中
        status, resp = _post("/request", _req_payload(
            [{"role": "user", "content": "不夜城 的预算表"}]))
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)

    def test_cyrillic_homoglyph_secret_hit(self):
        # Cyrillic ѕ(U+0455) 替换拉丁 s：归一化后命中密钥格式
        status, resp = _post("/request", _req_payload(
            [{"role": "user", "content": "ѕk-ABCDEFGH12345"}]))
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)
        self.assertIn("secrets.test_sk", resp["action"]["reason"])

    def test_cyrillic_homoglyph_term_hit(self):
        # Cyrillic а(U+0430) 替换拉丁 a：词表命中
        status, resp = _post("/request", _req_payload(
            [{"role": "user", "content": "蓝鲸BlueWhаle系统 进展"}]))
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)
        self.assertIn("confidential.codename", resp["action"]["reason"])

    def test_traditional_extended_term_hit(self):
        # 繁体表扩充：燈/單/硯/審 等原未映射字
        status, resp = _post("/request", _req_payload(
            [{"role": "user", "content": "燈塔工單系統 上線了"}]))
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)
        status, resp = _post("/request", _req_payload(
            [{"role": "user", "content": "硯台審計平台 季報"}]))
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)

    def test_zero_width_secret_hit(self):
        # 零宽字符（U+200B，显式转义抗格式化吞字）拆密钥形态：清除后命中（对齐 pg 归一化语义）
        status, resp = _post("/request", _req_payload(
            [{"role": "user", "content": "s\u200bk-ABCDEFGH12345"}]))
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("status_code"), 451)

    def test_clean_text_no_false_positive(self):
        # 负例锚：日常文本（含 Greek μ 等同形字折叠源字符）不误伤
        status, resp = _post("/request", _req_payload(
            [{"role": "user", "content": "今天天气怎么样，μs 级延迟可以接受"}]))
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("reason"), "pass")


class LocalTokenGuardTest(SurfaceSeamBase):
    """项 3：/judge-test /feishu-alert /classify 共享密钥守卫（fail-closed）。"""

    def test_unconfigured_env_all_three_403(self):
        # env 未配置 → 三端点恒 403（fail-closed：宁可端点不可用，不留未鉴权面）
        os.environ.pop("SHIM_LOCAL_TOKEN", None)
        status, _ = _post("/judge-test", {"text": "任意文本"})
        self.assertEqual(status, 403)
        status, _ = _post("/feishu-alert", {"event": "x"})
        self.assertEqual(status, 403)
        status, _ = _post("/classify", {"model": "auto"})
        self.assertEqual(status, 403)

    def test_unconfigured_env_classify_all_methods_403(self):
        # /classify 全方法 fail-closed（extAuthz 同方法转发，缺方法覆盖会留绕过口）
        os.environ.pop("SHIM_LOCAL_TOKEN", None)
        for method in ("GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"):
            with self.subTest(method=method):
                status, _ = _raw_req(method, "/classify")
                self.assertEqual(status, 403)

    def test_wrong_token_403(self):
        os.environ["SHIM_LOCAL_TOKEN"] = _LOCAL_TOKEN
        status, _ = _post("/judge-test", {"text": "x"}, headers={"X-Shim-Local-Token": "wrong"})
        self.assertEqual(status, 403)
        status, _ = _post("/classify", {"model": "auto"}, headers={"X-Shim-Local-Token": "wrong"})
        self.assertEqual(status, 403)

    def test_valid_token_passes_guard(self):
        # 头匹配 → 守卫放行，端点原语义不变（judge 未启用 → verdict null；
        # feishu webhook 未配置 → 502 证明已过守卫而非 403；classify 200 无头）
        os.environ["SHIM_LOCAL_TOKEN"] = _LOCAL_TOKEN
        h = {"X-Shim-Local-Token": _LOCAL_TOKEN}
        status, body = _post("/judge-test", {"text": "任意文本"}, headers=h)
        self.assertEqual(status, 200)
        self.assertIn("verdict", body)
        status, body = _post("/feishu-alert", {"event": "x"}, headers=h)
        self.assertEqual(status, 502)  # webhook 未配置 → send_feishu_text False → 502（非 403）
        status, body = _post("/classify", {"model": "auto"}, headers=h)
        self.assertEqual(status, 200)
        self.assertIsNone(body["resolved_model"])
        status, _ = _raw_req("GET", "/classify", headers=h)
        self.assertEqual(status, 200)

    def test_unguarded_endpoints_unaffected(self):
        # 锚：/healthz 与 /request 不在守卫范围（健康检查/检测主链路语义不变）
        os.environ.pop("SHIM_LOCAL_TOKEN", None)
        status, _ = _raw_req("GET", "/healthz")
        self.assertEqual(status, 200)
        status, resp = _post("/request", _req_payload([{"role": "user", "content": "今天天气怎么样"}]))
        self.assertEqual(status, 200)
        self.assertEqual(resp["action"].get("reason"), "pass")


if __name__ == "__main__":
    unittest.main()
