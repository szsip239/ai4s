#!/usr/bin/env python3
"""judge 前置单趟 base64 解码单测（issue #107，#99/#96 共同盲区闭合）。

被测语义（app.py judge_pre_decode + /judge-test 与 /request 两处 judge_input 接线）：
- 纯函数：base64 形 token（[A-Za-z0-9+/]{16,}={0,2}）可解码为可打印文本（比例 >0.9）时
  内联替换——解码语义对齐 pg_engine.normalize_for_scoring 的 base64 段（但不动 PG 侧，
  judge 侧自建同语义纯函数，无零宽/NFKC——掩码后文本已过 L1 归一化层）；
- 单趟不迭代：嵌套 base64 只解外层一趟，解码结果不再二次扫描（#104 规则层的迭代探针
  是另一层，语义不混）；
- fail-open：解码任何异常（非法 padding/二进制内容/底层抛出）→ 原文原样返回，
  绝不影响 judge 主流程；解码内容不落日志（secret 纪律，现有 judge 日志本就只记 verdict）；
- 接线：/judge-test（商密+注入两 duty）与 /request 链路的 judge_input 均先过解码再送判；
- 水位断言（直读仓库配置，同 test_admin_api.py SecretBoundaryNormTest 先例）：
  semantic-eval 门禁 bypass 线已随盲区闭合抬升（GATE_MIN_BYPASS_HIT ≥ 3），
  样本库含 base64 正常文本负例（负例不误报口径可观测）；issue #110 起断言 bypass
  合并口径排除 layer=negative 负例（#106 拼接雷不再并入，组 9→4）。

seam 纪律同 test_judge_inject.py：进程内起真实 ThreadingHTTPServer 跑 app.Handler；
settings.json 直写临时文件；编排用例模块级 mock judge_text/judge_inject_text 捕获送判输入。

运行：cd shim && python3 -m unittest discover -s tests
"""
import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

# 让测试可 import shim 目录下的 app / shadow_log（discover 从 shim/tests 启动）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as shim_app  # noqa: E402

_SHIM = ThreadingHTTPServer(("127.0.0.1", 0), shim_app.Handler)
threading.Thread(target=_SHIM.serve_forever, daemon=True).start()
_BASE = f"http://127.0.0.1:{_SHIM.server_address[1]}"

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# bypass 组两条 base64 样本（deploy/tests/dlp-vectors.json 同文）与 base64 负例样本
SK_ANT_B64 = "c2stYW50LWFwaTAzLXgxLHgyLHgzIHNlY3JldA=="  # → "sk-ant-api03-x1,x2,x3 secret"
FENGHUANG_B64 = "5Yek5Yew6K6h5YiS"  # → "凤凰计划"
NEG_B64_SAMPLE = ("同事发来一串编码 VGhpcyBpcyBhbiBpbm5vY3VvdXMgbWVzc2FnZQ== 让我帮忙解码，"
                  "解出来是一段英文操作说明，没有什么特别内容")


def _load_semantic_eval():
    """importlib 直载 deploy/scripts/semantic-eval.py（连字符文件名不能 import 语句，
    同 deploy/scripts/tests/test_semantic_eval.py 先例；模块尾部有 __main__ 保护，加载无副作用）。"""
    path = os.path.join(_REPO, "deploy", "scripts", "semantic-eval.py")
    spec = importlib.util.spec_from_file_location("semantic_eval", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _post(content):
    payload = {"body": {"model": "echo-test", "messages": [{"role": "user", "content": content}]}}
    req = urllib.request.Request(
        _BASE + "/request", data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.load(r)


def _judge_test(payload):
    req = urllib.request.Request(
        _BASE + "/judge-test", data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.load(r)


class PreDecodePureTest(unittest.TestCase):
    """judge_pre_decode 纯函数：解码语义对齐 PG normalize_for_scoring base64 段。"""

    def test_sk_ant_b64_inline_replaced(self):
        """#99 漏报样本：sk-ant base64 形 token 解码内联替换，周边文本保留。"""
        out = shim_app.judge_pre_decode(f"{SK_ANT_B64} 这是一段编码")
        self.assertIn("sk-ant-api03-x1,x2,x3 secret", out)
        self.assertIn("这是一段编码", out)
        self.assertNotIn(SK_ANT_B64, out)

    def test_fenghuang_b64_inline_replaced(self):
        """中文载荷 base64（恰 16 字符边界）解码内联替换。"""
        out = shim_app.judge_pre_decode(f"{FENGHUANG_B64} 这段解码看看")
        self.assertIn("凤凰计划", out)

    def test_short_token_untouched(self):
        """短于 16 字符的类 base64 串不探测（阈值对齐 PG：{16,}）。"""
        text = "状态码 5Yek5Yew6K6h 别动它"  # 12 字符
        self.assertEqual(shim_app.judge_pre_decode(text), text)

    def test_invalid_padding_untouched(self):
        """非法 padding（validate=True 拒收）→ 原文原样。"""
        text = "AAAAAAAAAAAAAAAAA= 这串解不了"  # 17+1 字符，长度非 4 倍数
        self.assertEqual(shim_app.judge_pre_decode(text), text)

    def test_binary_decode_untouched(self):
        """可解码但非可打印文本（PNG 头 base64）→ 不替换（可打印比例 >0.9 门槛）。"""
        text = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg== 是图片"
        self.assertEqual(shim_app.judge_pre_decode(text), text)

    def test_single_pass_no_iterate(self):
        """单趟语义锚点：嵌套 base64 只解外层一趟——解出的内层 base64 token 留在文本里，
        不再二次扫描（#104 规则层迭代探针是另一层）。"""
        inner = "c2VjcmV0IHBheWxvYWQgaGVyZQ=="  # base64("secret payload here")
        outer = "YzJWamNtVjBJSEJoZVd4dllXUWdhR1Z5WlE9PQ=="  # base64(inner)
        out = shim_app.judge_pre_decode(f"载荷 {outer} 完")
        self.assertIn(inner, out)  # 外层解出内层 token，停在这一趟
        self.assertNotIn("secret payload here", out)

    def test_plain_text_unchanged(self):
        """无 base64 形 token 的正常文本逐字节不变（含中文/英文/数字混排）。"""
        for text in ("", "帮我写一份本周工作周报，重点写项目进度与风险",
                     "The risks include data breaches and operational disruptions across regions"):
            self.assertEqual(shim_app.judge_pre_decode(text), text)

    def test_fail_open_on_decode_exception(self):
        """fail-open 锚点：底层解码炸出非预期异常 → 原文原样返回，绝不向 judge 主流程抛。"""
        with mock.patch.object(shim_app.base64, "b64decode", side_effect=RuntimeError("boom")):
            text = f"{SK_ANT_B64} 这是一段编码"
            self.assertEqual(shim_app.judge_pre_decode(text), text)


class JudgeTestWiringTest(unittest.TestCase):
    """/judge-test 接线：商密/注入两 duty 的送判输入均已过前置解码（mock 判定函数捕获）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings_path = os.path.join(self._tmp.name, "settings.json")
        self.wordlist_path = os.path.join(self._tmp.name, "wordlist.json")
        with open(self.wordlist_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "terms": []}, f, ensure_ascii=False)
        self._saved = (shim_app.SETTINGS_PATH, shim_app.WORDLIST_PATH)
        shim_app.SETTINGS_PATH = self.settings_path
        shim_app.WORDLIST_PATH = self.wordlist_path
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "judge": {"enabled": True}}, f, ensure_ascii=False)

    def tearDown(self):
        shim_app.SETTINGS_PATH, shim_app.WORDLIST_PATH = self._saved
        self._tmp.cleanup()

    def test_commercial_duty_input_decoded(self):
        with mock.patch.object(shim_app, "judge_text", return_value=None) as m_com:
            status, _ = _judge_test({"text": f"{SK_ANT_B64} 这是一段编码"})
        self.assertEqual(status, 200)
        m_com.assert_called_once()
        sent = m_com.call_args[0][0]
        self.assertIn("sk-ant-api03-x1,x2,x3 secret", sent)

    def test_inject_duty_input_decoded(self):
        """注入第二职责同一份 judge_input，同样受益（judge_inject_text 捕获）。"""
        with mock.patch.object(shim_app, "judge_inject_text", return_value=None) as m_inj:
            status, _ = _judge_test({"text": f"{SK_ANT_B64} 这是一段编码", "duty": "inject"})
        self.assertEqual(status, 200)
        m_inj.assert_called_once()
        sent = m_inj.call_args[0][0]
        self.assertIn("sk-ant-api03-x1,x2,x3 secret", sent)


class RequestWiringTest(unittest.TestCase):
    """/request 链路接线：judge shadow 判定输入已过前置解码（响应后判定段同步执行，
    mock 上下文内 sleep 等落定——竞态纪律同 test_judge_inject.py 文件头注）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log_path = os.path.join(self._tmp.name, "shadow.jsonl")
        self.settings_path = os.path.join(self._tmp.name, "settings.json")
        self.wordlist_path = os.path.join(self._tmp.name, "wordlist.json")
        with open(self.wordlist_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "terms": []}, f, ensure_ascii=False)
        self._saved = (shim_app.SETTINGS_PATH, shim_app.WORDLIST_PATH)
        shim_app.SETTINGS_PATH = self.settings_path
        shim_app.WORDLIST_PATH = self.wordlist_path
        self._saved_env = {k: os.environ.pop(k, None) for k in
                           ("JUDGE_ENABLED", "JUDGE_ACTION", "JUDGE_SAMPLE_RATE",
                            "JUDGE_MAX_CONCURRENCY", "JUDGE_INJECT_ENABLED",
                            "PG_ENABLED", "RULES_ENABLED")}
        self._saved_env["SHADOW_LOG_PATH"] = os.environ.get("SHADOW_LOG_PATH")
        os.environ["SHADOW_LOG_PATH"] = self.log_path
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1,
                       "judge": {"enabled": True, "action": "shadow"},
                       "pg": {"enabled": False}, "edm": {"enabled": False},
                       "rules": {"enabled": False, "block": False}},
                      f, ensure_ascii=False)

    def tearDown(self):
        shim_app.SETTINGS_PATH, shim_app.WORDLIST_PATH = self._saved
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_shadow_judge_input_decoded(self):
        with mock.patch.object(shim_app, "judge_text",
                               return_value={"confidential": True, "entities": [],
                                             "confidence": 0.99}) as m_com:
            status, body = _post(f"{SK_ANT_B64} 这是一段编码")
            time.sleep(0.5)
        self.assertEqual(status, 200)
        self.assertEqual(body["action"].get("reason"), "pass")
        m_com.assert_called_once()
        sent = m_com.call_args[0][0]
        self.assertIn("sk-ant-api03-x1,x2,x3 secret", sent)


class GateLevelAssertionTest(unittest.TestCase):
    """水位断言（直读仓库配置文件，同 SecretBoundaryNormTest 先例）：盲区闭合后
    semantic-eval bypass 门禁线已抬升、样本库含 base64 正常文本负例；issue #110
    修正 bypass 合并口径后，组口径回到 4 条真绕过变形样本（排除 #106 拼接雷负例）。"""

    def test_bypass_gate_raised(self):
        """GATE_MIN_BYPASS_HIT ≥ 3（#99 盲区期线=2；#107 解码前置 + #110 口径修正后组 4 条、实测 3/4、可检出上限 4，线 3 恰压水位——任一变形样本回归即破线，符合防回归语义；#109 抬水位至 4/4 后线应同步抬 4）。"""
        with open(os.path.join(_REPO, "deploy", "scripts", "semantic-eval.py"), encoding="utf-8") as f:
            src = f.read()
        m = re.search(r"^GATE_MIN_BYPASS_HIT = (\d+)$", src, re.M)
        self.assertIsNotNone(m, "semantic-eval.py 找不到 GATE_MIN_BYPASS_HIT")
        self.assertGreaterEqual(int(m.group(1)), 3)

    def test_b64_negative_sample_present(self):
        """semantic-vectors.json 负例组含 base64 正常文本负例（负例不误报口径可观测）。"""
        with open(os.path.join(_REPO, "deploy", "tests", "semantic-vectors.json"), encoding="utf-8") as f:
            vecs = json.load(f)["vectors"]
        b64_tok = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
        hits = [v["name"] for v in vecs
                if v["expect"] == "clean" and b64_tok.search(v["content"])]
        self.assertTrue(hits, "负例组缺 base64 正常文本样本（issue #107 AC2）")

    def test_bypass_group_excludes_negative_layer(self):
        """issue #110：bypass 合并口径排除 layer=negative 的负例——#106 拼接雷 5 条在
        expect=confidential 口径下是永久 MISS（judge 判 clean 才是正确判定），并入把组条数
        4→9、可检出上限 4 稀释成 3/9 虚口径。组应只含 4 条真绕过变形样本。"""
        se = _load_semantic_eval()
        with open(os.path.join(_REPO, "deploy", "tests", "dlp-vectors.json"), encoding="utf-8") as f:
            dlp = json.load(f)["vectors"]
        names = [v["name"] for v in se.collect_bypass(dlp)]
        self.assertFalse(any(n.startswith("negative:") for n in names),
                         f"#106 拼接雷负例仍并入 bypass 组: {names}")
        for want in ("secret-bypass: sk-ant base64(tamga)", "wordlist-bypass: 谐音 凤皇计划",
                     "wordlist-bypass: 拼音 fenghuang", "wordlist-bypass: base64 凤凰计划"):
            self.assertIn(want, names)


if __name__ == "__main__":
    unittest.main()
