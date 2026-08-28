#!/usr/bin/env python3
"""auto 智能路由 /classify 真实分类器单测（issue #117）。

被测语义（app.py do_POST /classify 分支 + route_resolve + router_classify）：

- 分类器：judge 通道外部 LLM pcomplex 校准模式（#114 票选候选 B 生产落点）——模型/地址
  沿用 settings judge.model/judge.base_url + JUDGE_API_KEY 凭据，系统提示只让模型输出
  JSON {"p_complex": 0~1}，shim 侧按 routing.threshold 判档（>= 阈值 → complex）；
- routing 节（settings.json，热更新）：enabled（默认 false 零行为）/threshold/tiers 两档
  映射/timeout（超时 fail-open）/max_concurrency（独立并发预算，与 judge 预算不互挤）；
- 会话策略（方案 A）：同会话首轮定档、后续继承（complex 存态不再分类——永不降档）；
  simple 存态每轮仍分类做升档检查：本轮 p_complex ≥ 0.85 才升 complex（理由=escalate）；
  升档时检出 messages 含 thinking/redacted_thinking blocks → 放弃升档（reason=thinking_lock，
  shim 在 extAuthz 点只回传 model 名改不了 messages，保缓存与上下文完整的保守等价实现）；
  tool-loop 硬锁（最后一条消息 role=tool 或含 tool_calls）→ 不分类直接继承；
  会话状态进程内 LRU（TTL 3600s 命中续期，重启即丢=新会话重新定档）；
  会话 key 优先级：请求头 x-session-id > body.metadata.session_id > 首轮 user 消息哈希；
- 外发纪律（#93 既定）：分类输入先过 mask_pipeline（L1/L2 掩码）再外发；
- fail-open：分类器超时/异常/verdict 形状损坏/预算占满 → 200 不带 x-resolved-model 头
  （网关 CEL 回退 + modelAliases 落旗舰）；超时/异常落 shadow_log error 条（layer="router"），
  预算占满 print 不落条（skip 非层异常，同 #93 语义不污染 error_rate）；
- 决策日志：shadow_log layer="router"——model（原值 auto）/resolved_model/tier/p_complex/
  reason（classify/session_inherit/escalate/tool_loop_lock/thinking_lock/fail_open）/
  latency_ms/session 命中位；无原文、非 None 才写键（体积纪律同既有层）。

seam 纪律同 test_admin_api.py JudgeShadowMaskTest：进程内 ThreadingHTTPServer 跑
app.Handler；分类器上游（judge 通道 /chat/completions）用本地假服务顶替（_FakeRouterJudge，
线路边界）；临时 settings.json/wordlist/format-rules 覆写 shim_app 模块级路径；
SHADOW_LOG_PATH env 注入 tmp（shadow_log 每次调用现读 env）；会话 LRU 每用例清空。

运行：cd shim && .venv/bin/python -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

# 让测试可 import shim 目录下的 app（discover 从 shim/tests 启动）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as shim_app  # noqa: E402
import shadow_log  # noqa: E402


# ---- 假路由分类器上游：只顶替 judge 通道路线边界（POST /chat/completions）----
# STATE 驱动行为：p=固定 p_complex 输出；mode=ok/garbage/http500/slow（slow 先睡再答）。
class _FakeRouterJudge(BaseHTTPRequestHandler):
    STATE = {"mode": "ok", "p": 0.1, "sleep": 0.0, "calls": 0, "captured": None}
    _lock = threading.Lock()

    def log_message(self, *args):  # 静默
        pass

    @classmethod
    def reset(cls, p=0.1, mode="ok", sleep=0.0):
        with cls._lock:
            cls.STATE.update({"mode": mode, "p": p, "sleep": sleep, "calls": 0, "captured": None})

    @classmethod
    def set_p(cls, p):
        """用例中途改分类分数（不清 calls/captured 计数——多轮会话测试连续观测用）。"""
        with cls._lock:
            cls.STATE["p"] = p

    @classmethod
    def calls(cls):
        with cls._lock:
            return cls.STATE["calls"]

    @classmethod
    def captured(cls):
        with cls._lock:
            return cls.STATE["captured"]

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            with self._lock:
                self.STATE["calls"] += 1
                self.STATE["captured"] = body
            st = self.STATE
            if st["mode"] == "http500":
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if st["mode"] == "slow":
                time.sleep(st["sleep"])
            content = "not a json verdict" if st["mode"] == "garbage" else json.dumps({"p_complex": st["p"]})
            out = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        except Exception:
            pass  # 客户端超时断连等：假服务静默（slow 模式写回已断的连接会 BrokenPipe）


def _start_server(handler_cls):
    """127.0.0.1:0 ephemeral 端口起真实 HTTP 服务（daemon 线程，测试进程退出即收）。"""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class RouterTestBase(unittest.TestCase):
    """公共 fixture：临时 settings.json（judge 节指向假分类器 + routing 节）+ 空词表/
    format-rules；覆写 shim_app 模块级路径与 JUDGE_API_KEY；SHADOW_LOG_PATH 注入 tmp；
    会话 LRU 清空；ROUTING_* env 摘除（开发机导出隔离，对齐既有 env 纪律）。"""

    # 两档映射测试值：cheap/flag 便于断言不与真实模型名混淆
    TIERS = {"simple": "cheap-model", "complex": "flag-model"}

    @classmethod
    def setUpClass(cls):
        cls._judge_srv = _start_server(_FakeRouterJudge)
        cls._shim_srv = _start_server(shim_app.Handler)
        cls._shim_base = f"http://127.0.0.1:{cls._shim_srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls._judge_srv.shutdown()
        cls._shim_srv.shutdown()

    def _settings_payload(self, routing=None):
        """最小 settings（app 侧逐键护栏，无需整 schema 齐全）：judge 节指向假分类器，
        l1/l2 开（掩码管线走真路径）；routing 节按需附加。"""
        s = {
            "version": 1,
            "judge": {"enabled": True, "model": "router-judge-model",
                      "base_url": f"http://127.0.0.1:{self._judge_srv.server_address[1]}",
                      "timeout": 8},
            "l1": {"enabled": True},
            "l2": {"enabled": True},
        }
        if routing is not None:
            s["routing"] = routing
        return s

    def _routing(self, **over):
        r = {"enabled": True, "threshold": 0.5, "tiers": dict(self.TIERS),
             "timeout": 5, "max_concurrency": 2}
        r.update(over)
        return r

    def setUp(self):
        _FakeRouterJudge.reset()
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.wordlist_path = os.path.join(d, "wordlist.json")
        with open(self.wordlist_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "terms": []}, f)
        self.format_rules_path = os.path.join(d, "format-rules.json")
        with open(self.format_rules_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "rules": []}, f)
        self.log_path = os.path.join(d, "shadow.jsonl")
        self.settings_path = os.path.join(d, "settings.json")
        self._write_settings(self._settings_payload(routing=self._routing()))
        self._saved = (shim_app.SETTINGS_PATH, shim_app.WORDLIST_PATH,
                       shim_app.FORMAT_RULES_PATH, shim_app.JUDGE_API_KEY)
        shim_app.SETTINGS_PATH = self.settings_path
        shim_app.WORDLIST_PATH = self.wordlist_path
        shim_app.FORMAT_RULES_PATH = self.format_rules_path
        shim_app.JUDGE_API_KEY = "test-key"
        self._saved_env = {k: os.environ.pop(k, None)
                           for k in ("ROUTING_ENABLED", "ROUTING_THRESHOLD",
                                     "ROUTING_TIMEOUT", "ROUTING_MAX_CONCURRENCY",
                                     # issue #119 可选增补键 env 层（同纪律摘除防开发机导出污染）
                                     "ROUTING_PROMPT", "ROUTING_ESCALATE_CONF",
                                     "ROUTING_SESSION_TTL", "ROUTING_TOOL_LOOP_LOCK",
                                     "ROUTING_THINKING_LOCK")}
        self._saved_env["SHADOW_LOG_PATH"] = os.environ.get("SHADOW_LOG_PATH")
        os.environ["SHADOW_LOG_PATH"] = self.log_path
        shim_app._router_sessions.clear()

    def tearDown(self):
        (shim_app.SETTINGS_PATH, shim_app.WORDLIST_PATH,
         shim_app.FORMAT_RULES_PATH, shim_app.JUDGE_API_KEY) = self._saved
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shim_app._router_sessions.clear()
        while shim_app._ROUTER_INFLIGHT > 0:  # 防御：预算计数器不跨用例泄漏
            shim_app.router_budget_exit()
        self._tmp.cleanup()

    def _write_settings(self, payload):
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    def _post(self, payload, headers=None):
        """POST /classify；返回 (status, x-resolved-model 头或 None, 响应体 dict)。"""
        req = urllib.request.Request(
            self._shim_base + "/classify",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"})
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers.get("x-resolved-model"), json.load(r)

    def _records(self, layer="router"):
        return shadow_log.tail(50, layer=layer, path=self.log_path)

    @staticmethod
    def _msgs(*texts, session_first="任务一"):
        """构造 messages：首轮 user + 后续 user 轮次（无 session 头时首轮 user 内容作指纹）。"""
        return [{"role": "user", "content": t} for t in (session_first, *texts)]

    def _auto(self, texts, session_first="任务一", headers=None, metadata=None, extra_tail=None):
        """POST model=auto 多轮形态：首轮 user + 追加轮次 + 可选尾消息（tool/thinking 形态）。"""
        msgs = self._msgs(*texts, session_first=session_first)
        if extra_tail:
            msgs.extend(extra_tail)
        payload = {"model": "auto", "messages": msgs}
        if metadata:
            payload["metadata"] = metadata
        return self._post(payload, headers=headers)


class RouterDisabledZeroBehaviorTest(RouterTestBase):
    """enabled=false（新层进场先关）：所有 model 200 无头、零分类调用、零落条。"""

    def test_disabled_no_header_no_classify_no_record(self):
        self._write_settings(self._settings_payload(routing=self._routing(enabled=False)))
        for payload in ({"model": "auto", "messages": self._msgs()},
                        {"model": "echo-test", "messages": self._msgs()},
                        {"model": "gpt-5.6-luna"}):
            status, hdr, body = self._post(payload)
            self.assertEqual(status, 200)
            self.assertIsNone(hdr)
            self.assertIsNone(body["resolved_model"])
        self.assertEqual(_FakeRouterJudge.calls(), 0)
        self.assertFalse(os.path.exists(self.log_path))

    def test_routing_section_absent_same_as_disabled(self):
        """settings 无 routing 节（旧文件形态）→ 同 disabled：200 无头零调用。"""
        self._write_settings(self._settings_payload(routing=None))
        status, hdr, _ = self._post({"model": "auto", "messages": self._msgs()})
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        self.assertEqual(_FakeRouterJudge.calls(), 0)


class RouterClassifyTest(RouterTestBase):
    """两档改写 + 阈值边界 + 掩码纪律 + 决策日志形状。"""

    def test_simple_tier_rewrite_and_decision_log(self):
        """p_complex=0.1 < 阈值 0.5 → simple 档：x-resolved-model=tiers.simple；
        决策日志 layer=router 全字段形状（model 原值 auto/resolved_model/tier/p_complex/
        reason=classify/latency_ms/session=False），无原文。"""
        status, hdr, body = self._post({"model": "auto", "messages": self._msgs()})
        self.assertEqual(status, 200)
        self.assertEqual(hdr, "cheap-model")
        self.assertEqual(body["resolved_model"], "cheap-model")
        recs = self._records()
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["layer"], "router")
        self.assertEqual(rec["model"], "auto")
        self.assertEqual(rec["resolved_model"], "cheap-model")
        self.assertEqual(rec["tier"], "simple")
        self.assertEqual(rec["p_complex"], 0.1)
        self.assertEqual(rec["reason"], "classify")
        self.assertEqual(rec["session"], False)
        self.assertIsInstance(rec["latency_ms"], int)
        self.assertIsNone(rec["error"])

    def test_complex_tier_rewrite(self):
        """p_complex=0.9 ≥ 阈值 → complex 档：x-resolved-model=tiers.complex。"""
        _FakeRouterJudge.reset(p=0.9)
        status, hdr, _ = self._post({"model": "auto", "messages": self._msgs()})
        self.assertEqual(status, 200)
        self.assertEqual(hdr, "flag-model")
        self.assertEqual(self._records()[0]["tier"], "complex")

    def test_threshold_boundary(self):
        """阈值边界：p == threshold → complex（>= 判档）；p 略低 → simple。"""
        _FakeRouterJudge.reset(p=0.5)
        _, hdr, _ = self._post({"model": "auto", "messages": self._msgs()})
        self.assertEqual(hdr, "flag-model")
        _FakeRouterJudge.set_p(0.49)
        _, hdr, _ = self._post({"model": "auto", "messages": self._msgs(session_first="另一个任务")})
        self.assertEqual(hdr, "cheap-model")

    def test_threshold_hot_update(self):
        """threshold 热更新：settings 改写后下一请求即生效（0.5→0.95 后 p=0.9 落 simple）。"""
        _FakeRouterJudge.reset(p=0.9)
        _, hdr, _ = self._post({"model": "auto", "messages": self._msgs()})
        self.assertEqual(hdr, "flag-model")
        self._write_settings(self._settings_payload(routing=self._routing(threshold=0.95)))
        _, hdr, _ = self._post({"model": "auto", "messages": self._msgs(session_first="第二个任务")})
        self.assertEqual(hdr, "cheap-model")

    def test_tiers_hot_update(self):
        """tiers 映射热更新（PUT 场景的运行侧语义）：simple 映射值改写即生效。"""
        _, hdr, _ = self._post({"model": "auto", "messages": self._msgs()})
        self.assertEqual(hdr, "cheap-model")
        self._write_settings(self._settings_payload(
            routing=self._routing(tiers={"simple": "cheap-v2", "complex": "flag-model"})))
        _, hdr, _ = self._post({"model": "auto", "messages": self._msgs(session_first="另一个任务")})
        self.assertEqual(hdr, "cheap-v2")

    def test_non_auto_models_no_header_no_classify(self):
        """enabled=true 时非 auto model → 200 无头（不再回显原值）、零分类调用、零落条。"""
        for model in ("echo-test", "gpt-5.6-luna", "deepseek-v4-flash"):
            status, hdr, _ = self._post({"model": model, "messages": self._msgs()})
            self.assertEqual(status, 200)
            self.assertIsNone(hdr)
        self.assertEqual(_FakeRouterJudge.calls(), 0)
        self.assertFalse(os.path.exists(self.log_path))

    def test_classifier_input_masked_before_send(self):
        """外发纪律（#93 既定）：分类输入先过 mask_pipeline——手机号命中 L1 掩码规则后，
        假分类器收到的文本只有掩码占位，不含原值。"""
        with open(self.format_rules_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "rules": [
                {"code": "pii.phone", "action": "mask", "enabled": True, "entity": "ZH_PHONE",
                 "replacement": "【PII:手机号】", "shim_patterns": ["(?<!\\d)1[3-9]\\d{9}(?!\\d)"]}]},
                      f, ensure_ascii=False)
        status, hdr, _ = self._post({"model": "auto", "messages": [
            {"role": "user", "content": "打我手机 13800138000 讨论下这个函数"}]})
        self.assertEqual(status, 200)
        self.assertEqual(hdr, "cheap-model")
        sent = _FakeRouterJudge.captured()["messages"][1]["content"]
        self.assertIn("【PII:手机号】", sent)
        self.assertNotIn("13800138000", sent)

    def test_classifier_request_shape(self):
        """分类调用形状（#114 pcomplex 评测定稿）：judge 通道 /chat/completions、
        settings judge.model、temperature 0、max_tokens 64、system+单 user 两消息。"""
        self._post({"model": "auto", "messages": self._msgs()})
        req = _FakeRouterJudge.captured()
        self.assertEqual(req["model"], "router-judge-model")
        self.assertEqual(req["temperature"], 0)
        self.assertEqual(req["max_tokens"], 64)
        self.assertEqual([m["role"] for m in req["messages"]], ["system", "user"])
        self.assertIn("p_complex", req["messages"][0]["content"])
        self.assertEqual(req["messages"][1]["content"], "任务一")

    def test_tiers_broken_value_fails_open(self):
        """tiers 映射值含非法字符（手改 settings 绕过 admin 校验场景）→ 200 无头 +
        error 落条（配置坏=分类不可用，要可感知）。"""
        self._write_settings(self._settings_payload(
            routing=self._routing(tiers={"simple": "bad\r\nx-injected: 1", "complex": "flag-model"})))
        status, hdr, _ = self._post({"model": "auto", "messages": self._msgs()})
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["error"], "unavailable")
        self.assertEqual(recs[0]["reason"], "fail_open")

    def test_tiers_wrong_type_falls_back_to_default(self):
        """tiers 类型护栏（app 侧逐键纪律）：JSON 里 tiers 写成字符串 → 该键回退内置默认
        （默认 simple=deepseek-v4-flash / complex=gpt-5.6-luna，与 modelAliases 兜底一致）。"""
        self._write_settings(self._settings_payload(routing=self._routing(tiers="bogus")))
        _, hdr, _ = self._post({"model": "auto", "messages": self._msgs()})
        self.assertEqual(hdr, "deepseek-v4-flash")


class RouterFailOpenTest(RouterTestBase):
    """fail-open：超时/异常/verdict 损坏 → 200 无头 + error 落条；预算占满 print 不落条。"""

    def test_garbage_verdict_fails_open(self):
        """分类器返回非 JSON verdict → 200 无头 + shadow_log error 条（reason=fail_open）。"""
        _FakeRouterJudge.reset(mode="garbage")
        status, hdr, _ = self._post({"model": "auto", "messages": self._msgs()})
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["layer"], "router")
        self.assertEqual(recs[0]["error"], "unavailable")
        self.assertEqual(recs[0]["reason"], "fail_open")
        self.assertEqual(recs[0]["model"], "auto")
        self.assertIsInstance(recs[0]["latency_ms"], int)

    def test_http_error_fails_open(self):
        """分类器上游 500 → 200 无头 + error 落条。"""
        _FakeRouterJudge.reset(mode="http500")
        status, hdr, _ = self._post({"model": "auto", "messages": self._msgs()})
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        self.assertEqual(self._records()[0]["error"], "unavailable")

    def test_timeout_fails_open(self):
        """分类器超时（routing.timeout=0.2s < 假服务 sleep 1s）→ 200 无头 + error 落条。"""
        self._write_settings(self._settings_payload(routing=self._routing(timeout=0.2)))
        _FakeRouterJudge.reset(mode="slow", sleep=1.0)
        t0 = time.monotonic()
        status, hdr, _ = self._post({"model": "auto", "messages": self._msgs()})
        elapsed = time.monotonic() - t0
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        self.assertLess(elapsed, 1.0)  # 超时截断生效，不等慢上游
        self.assertEqual(self._records()[0]["error"], "unavailable")

    def test_p_complex_out_of_range_or_missing_fails_open(self):
        """verdict 形状损坏（p_complex 越界/缺失/布尔）→ 一律 fail-open。"""
        for bad in (1.5, -0.1, True):
            with self.subTest(p=bad):
                _FakeRouterJudge.reset(p=bad)
                status, hdr, _ = self._post({"model": "auto", "messages": self._msgs()})
                self.assertEqual(status, 200)
                self.assertIsNone(hdr)
        # 缺失键（garbage 之外的第二种坏形状：合法 JSON 但无 p_complex 由 http 层覆盖，
        # 此处用布尔断言后统一验 error 落条数）
        self.assertEqual(len(self._records()), 3)
        self.assertTrue(all(r["error"] == "unavailable" for r in self._records()))

    def test_budget_full_fails_open_without_error_record(self):
        """并发预算占满（routing.max_concurrency=1 且名额已占）→ 200 无头 + 零分类调用 +
        落决策条但**不带** error（skip 非层异常，同 #93 语义不污染 error_rate；
        条本身保留 fail_open 事件供阈值校准回放）+ print 告警行。"""
        self._write_settings(self._settings_payload(routing=self._routing(max_concurrency=1)))
        self.assertTrue(shim_app.router_budget_try_enter(1))  # 占满唯一名额（确定性，不用线程压）
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                status, hdr, _ = self._post({"model": "auto", "messages": self._msgs()})
        finally:
            shim_app.router_budget_exit()
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        self.assertEqual(_FakeRouterJudge.calls(), 0)
        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["reason"], "fail_open")
        self.assertIsNone(recs[0]["error"])  # 不落 error 条=不污染 router 层 error_rate
        self.assertNotIn("resolved_model", recs[0])
        self.assertIn("concurrency budget", buf.getvalue())

    def test_budget_counter_logic(self):
        """路由预算计数器纯逻辑（独立 judge 预算键）：enter 到 limit 拒绝不占位；exit 释放可再进。"""
        try:
            self.assertTrue(shim_app.router_budget_try_enter(2))
            self.assertTrue(shim_app.router_budget_try_enter(2))
            self.assertFalse(shim_app.router_budget_try_enter(2))
            shim_app.router_budget_exit()
            self.assertTrue(shim_app.router_budget_try_enter(2))
        finally:
            shim_app.router_budget_exit()
            shim_app.router_budget_exit()
        self.assertEqual(shim_app._ROUTER_INFLIGHT, 0)
        # 与 judge 预算独立：占满路由预算不影响 judge 名额
        self.assertTrue(shim_app.router_budget_try_enter(1))
        try:
            self.assertTrue(shim_app.judge_budget_try_enter(2))
        finally:
            shim_app.judge_budget_exit()
            shim_app.router_budget_exit()

    def test_empty_messages_no_input_fails_open_no_error(self):
        """无可判输入（messages 空/无文本）→ 200 无头 + 落条但**不带** error
        （空输入非层异常，同 #92 空 text 不落 error 条纪律），零分类调用。"""
        status, hdr, _ = self._post({"model": "auto", "messages": []})
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        self.assertEqual(_FakeRouterJudge.calls(), 0)
        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertIsNone(recs[0]["error"])
        self.assertEqual(recs[0]["reason"], "fail_open")

    def test_malformed_messages_fail_open(self):
        """畸形 messages（非 dict 元素）：掩码管线抛异常不得让 handler 线程崩掉、
        连接无响应断开（无 200 无 error 落条）——按 fail-open 回 200 + error=unavailable
        （对齐 /request 链路兜底先例），分类器零调用。"""
        status, hdr, body = self._post({"model": "auto", "messages": ["junk"]})
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        self.assertIsNone(body.get("resolved_model"))
        self.assertEqual(_FakeRouterJudge.calls(), 0)
        rec = self._records()[0]
        self.assertEqual(rec["reason"], "fail_open")
        self.assertEqual(rec["error"], "unavailable")
        self.assertIsNone(rec.get("resolved_model"))  # None 值键不落条（体积纪律）


class RouterSessionTest(RouterTestBase):
    """会话策略（方案 A）：首轮定档、继承、只升不降、tool-loop 锁、thinking 锁、LRU/TTL。"""

    def test_session_inherit_complex_no_reclassify(self):
        """首轮 complex 定档 → 同会话二轮直接继承（reason=session_inherit，session=True），
        **不再调分类器**（永不降档，complex 存态无需再分类，省一次 LLM 调用）。"""
        _FakeRouterJudge.reset(p=0.9)
        _, hdr, _ = self._auto([], headers={"x-session-id": "s1"})
        self.assertEqual(hdr, "flag-model")
        _FakeRouterJudge.set_p(0.0)  # 即便本轮分类会判 simple
        _, hdr, _ = self._auto(["追问一"], headers={"x-session-id": "s1"})
        self.assertEqual(hdr, "flag-model")  # 不降档
        self.assertEqual(_FakeRouterJudge.calls(), 1)  # 二轮未分类
        rec = self._records()[0]
        self.assertEqual(rec["reason"], "session_inherit")
        self.assertEqual(rec["session"], True)
        self.assertEqual(rec["resolved_model"], "flag-model")

    def test_session_inherit_simple_reclassifies_for_escalation(self):
        """simple 存态每轮仍分类（升档检查），本轮 p<0.85 → 维持 simple（reason=session_inherit）。"""
        _, hdr, _ = self._auto([], headers={"x-session-id": "s2"})
        self.assertEqual(hdr, "cheap-model")
        _FakeRouterJudge.set_p(0.84)  # <0.85 升档门槛（不清 calls 计数）
        _, hdr, _ = self._auto(["追问一"], headers={"x-session-id": "s2"})
        self.assertEqual(hdr, "cheap-model")
        self.assertEqual(_FakeRouterJudge.calls(), 2)
        rec = self._records()[0]
        self.assertEqual(rec["reason"], "session_inherit")
        self.assertEqual(rec["tier"], "simple")
        self.assertEqual(rec["p_complex"], 0.84)

    def test_escalate_on_strong_confidence(self):
        """升档：simple 存态 + 本轮 p_complex ≥ 0.85 → complex（reason=escalate），
        会话存态更新（后续轮直接继承不再分类）。"""
        _, hdr, _ = self._auto([], headers={"x-session-id": "s3"})
        self.assertEqual(hdr, "cheap-model")
        _FakeRouterJudge.set_p(0.85)  # 边界：==0.85 即升（不清 calls 计数）
        _, hdr, _ = self._auto(["追问：写完整实现"], headers={"x-session-id": "s3"})
        self.assertEqual(hdr, "flag-model")
        rec = self._records()[0]
        self.assertEqual(rec["reason"], "escalate")
        self.assertEqual(rec["tier"], "complex")
        # 三轮：存态已 complex → 继承不再分类
        _, hdr, _ = self._auto(["再追问"], headers={"x-session-id": "s3"})
        self.assertEqual(hdr, "flag-model")
        self.assertEqual(_FakeRouterJudge.calls(), 2)
        self.assertEqual(self._records()[0]["reason"], "session_inherit")

    def test_tool_loop_lock_inherits_without_classify(self):
        """tool-loop 硬锁：最后一条消息 role=tool（工具结果回传）→ 不分类直接继承存态
        （reason=tool_loop_lock），分类器零调用。"""
        _, hdr, _ = self._auto([], headers={"x-session-id": "s4"})
        self.assertEqual(hdr, "cheap-model")
        _FakeRouterJudge.set_p(1.0)  # 即便本轮会强升档（不清 calls 计数）
        _, hdr, _ = self._auto(
            [], headers={"x-session-id": "s4"},
            extra_tail=[{"role": "assistant", "content": "调用工具"},
                        {"role": "tool", "content": "工具结果"}])
        self.assertEqual(hdr, "cheap-model")  # 锁内不换档
        self.assertEqual(_FakeRouterJudge.calls(), 1)
        self.assertEqual(self._records()[0]["reason"], "tool_loop_lock")

    def test_tool_loop_lock_on_tool_calls_tail(self):
        """tool-loop 硬锁第二形态：最后一条消息含 tool_calls（assistant 发起调用待结果）。"""
        _, hdr, _ = self._auto([], headers={"x-session-id": "s5"})
        self.assertEqual(hdr, "cheap-model")
        _, hdr, _ = self._auto(
            [], headers={"x-session-id": "s5"},
            extra_tail=[{"role": "assistant", "content": None,
                         "tool_calls": [{"id": "c1", "function": {"name": "f"}}]}])
        self.assertEqual(hdr, "cheap-model")
        self.assertEqual(_FakeRouterJudge.calls(), 1)
        self.assertEqual(self._records()[0]["reason"], "tool_loop_lock")

    def test_tool_loop_without_session_no_header(self):
        """tool-loop 硬锁且无会话可继承 → 200 无头（网关兜底旗舰，与 complex 语义等价）、
        零分类调用、落条记 reason=tool_loop_lock + session=False。"""
        _FakeRouterJudge.reset(p=0.0)
        status, hdr, _ = self._post({"model": "auto", "messages": [
            {"role": "user", "content": "任务"},
            {"role": "tool", "content": "工具结果"}]})
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        self.assertEqual(_FakeRouterJudge.calls(), 0)
        rec = self._records()[0]
        self.assertEqual(rec["reason"], "tool_loop_lock")
        self.assertEqual(rec["session"], False)

    def test_tool_result_tail_locks_with_session(self):
        """tool-loop 硬锁 Anthropic 形态：尾消息 content 含 tool_result block（OpenAI 的
        role=tool 对应物）→ 同样锁定，有会话存态直接继承、分类器零调用。"""
        _, hdr, _ = self._auto([], headers={"x-session-id": "s-toolres"})
        self.assertEqual(hdr, "cheap-model")
        _FakeRouterJudge.set_p(1.0)  # 若漏锁会强升档（不清 calls 计数）
        _, hdr, _ = self._auto(
            [], headers={"x-session-id": "s-toolres"},
            extra_tail=[{"role": "assistant", "content": [
                             {"type": "tool_use", "id": "t1", "name": "exec", "input": {}}]},
                        {"role": "user", "content": [
                             {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}])
        self.assertEqual(hdr, "cheap-model")  # 锁内不换档
        self.assertEqual(_FakeRouterJudge.calls(), 1)
        self.assertEqual(self._records()[0]["reason"], "tool_loop_lock")

    def test_tool_result_tail_locks_without_session(self):
        """tool_result 尾锁且无会话可继承 → 200 无头、零分类调用、reason=tool_loop_lock。"""
        _FakeRouterJudge.reset(p=0.0)
        status, hdr, _ = self._post({"model": "auto", "messages": [
            {"role": "user", "content": "任务"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "exec", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}]})
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        self.assertEqual(_FakeRouterJudge.calls(), 0)
        rec = self._records()[0]
        self.assertEqual(rec["reason"], "tool_loop_lock")
        self.assertEqual(rec["session"], False)

    def test_thinking_lock_blocks_escalation(self):
        """thinking 锁：升档决策时 messages 含 thinking blocks（Anthropic 形态）→ 放弃升档
        （shim 只回传 model 名改不了 messages，换模型不剥 thinking 会沉默烧钱——保缓存与
        上下文完整的保守等价实现），维持 simple，reason=thinking_lock；存态保持 simple，
        后续无 thinking 的轮次仍可升档。"""
        _, hdr, _ = self._auto([], headers={"x-session-id": "s6"})
        self.assertEqual(hdr, "cheap-model")
        _FakeRouterJudge.set_p(0.95)
        thinking_tail = [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "推理过程"},
                {"type": "text", "text": "回答"}]},
            {"role": "user", "content": "写完整 Rust 并发实现"},
        ]
        _, hdr, _ = self._auto([], headers={"x-session-id": "s6"}, extra_tail=thinking_tail)
        self.assertEqual(hdr, "cheap-model")  # 放弃升档
        rec = self._records()[0]
        self.assertEqual(rec["reason"], "thinking_lock")
        self.assertEqual(rec["tier"], "simple")
        self.assertEqual(rec["p_complex"], 0.95)
        # 存态仍 simple：下一轮无 thinking blocks 且强置信 → 正常升档
        _, hdr, _ = self._auto(["继续：写测试"], headers={"x-session-id": "s6"})
        self.assertEqual(hdr, "flag-model")
        self.assertEqual(self._records()[0]["reason"], "escalate")

    def test_redacted_thinking_also_locks(self):
        """redacted_thinking 形态同样触发 thinking 锁。"""
        _, hdr, _ = self._auto([], headers={"x-session-id": "s7"})
        self.assertEqual(hdr, "cheap-model")
        _FakeRouterJudge.set_p(0.95)
        tail = [{"role": "assistant", "content": [
                     {"type": "redacted_thinking", "data": "..."},
                     {"type": "text", "text": "回答"}]},
                {"role": "user", "content": "深挖"}]
        _, hdr, _ = self._auto([], headers={"x-session-id": "s7"}, extra_tail=tail)
        self.assertEqual(hdr, "cheap-model")
        self.assertEqual(self._records()[0]["reason"], "thinking_lock")

    def test_fresh_simple_verdict_with_thinking_locked(self):
        """thinking 锁守首轮定档（评审 P2-1）：无会话存态（TTL 过期/LRU 逐出/无头续聊）
        且 messages 含 thinking blocks 时，simple 判决**不下结论**——不回 x-resolved-model
        （交网关 modelAliases 旗舰兜底=维持原状）、不落会话卡（下轮重判），
        reason=thinking_lock、session=False、p_complex 照记。同一沉默烧钱场景：
        换便宜模型不剥 thinking 会烧缓存。"""
        msgs = [
            {"role": "user", "content": "任务一"},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "推理"}, {"type": "text", "text": "回答"}]},
            {"role": "user", "content": "轻量收尾"},
        ]
        _FakeRouterJudge.reset(p=0.1)
        status, hdr, _ = self._post({"model": "auto", "messages": msgs},
                                    headers={"x-session-id": "s-th1"})
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)  # 不给结论，网关兜底旗舰
        self.assertEqual(_FakeRouterJudge.calls(), 1)  # 判决已发生，只是不落结论
        rec = self._records()[0]
        self.assertIsNone(rec.get("resolved_model"))  # None 值键不落条（体积纪律）
        self.assertIsNone(rec.get("tier"))
        self.assertEqual(rec["reason"], "thinking_lock")
        self.assertFalse(rec["session"])
        self.assertEqual(rec["p_complex"], 0.1)
        # 未落卡：下一轮（无 thinking blocks）同会话正常重判定档
        _, hdr, _ = self._auto(["再追问"], headers={"x-session-id": "s-th1"})
        self.assertEqual(hdr, "cheap-model")
        self.assertEqual(_FakeRouterJudge.calls(), 2)
        self.assertEqual(self._records()[0]["reason"], "classify")

    def test_fresh_complex_verdict_with_thinking_unaffected(self):
        """对照：首轮定档判 complex 时 thinking 锁不拦（thinking 本就是旗舰产出，
        发 complex 卡与网关兜底旗舰等价）——正常回头、落卡（reason=classify）。"""
        msgs = [
            {"role": "user", "content": "任务一"},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "推理"}, {"type": "text", "text": "回答"}]},
            {"role": "user", "content": "继续深挖架构"},
        ]
        _FakeRouterJudge.reset(p=0.95)
        status, hdr, _ = self._post({"model": "auto", "messages": msgs},
                                    headers={"x-session-id": "s-th2"})
        self.assertEqual(status, 200)
        self.assertEqual(hdr, "flag-model")
        rec = self._records()[0]
        self.assertEqual(rec["reason"], "classify")
        self.assertEqual(rec["tier"], "complex")
        self.assertFalse(rec["session"])  # session 标记=命中存态；首轮定档本身为 False
        # 卡已落：下一轮同会话直接继承、不再分类
        _, hdr, _ = self._auto(["再追问"], headers={"x-session-id": "s-th2"})
        self.assertEqual(hdr, "flag-model")
        self.assertEqual(_FakeRouterJudge.calls(), 1)
        self.assertEqual(self._records()[0]["reason"], "session_inherit")

    def test_session_key_metadata(self):
        """会话 key 第二来源：body.metadata.session_id（无 x-session-id 头时）。"""
        _FakeRouterJudge.reset(p=0.9)
        _, hdr, _ = self._auto([], metadata={"session_id": "m1"})
        self.assertEqual(hdr, "flag-model")
        _, hdr, _ = self._auto(["追问"], metadata={"session_id": "m1"})
        self.assertEqual(hdr, "flag-model")
        self.assertEqual(_FakeRouterJudge.calls(), 1)
        self.assertEqual(self._records()[0]["session"], True)

    def test_session_key_first_user_hash(self):
        """会话 key 兜底来源：首轮 user 消息内容哈希（chat 协议无状态，客户端重发全历史，
        第一条 user 消息是同会话稳定指纹）。"""
        _FakeRouterJudge.reset(p=0.9)
        _, hdr, _ = self._post({"model": "auto", "messages": [
            {"role": "user", "content": "帮我评审这个方案"}]})
        self.assertEqual(hdr, "flag-model")
        # 同首轮 user + 追加历史 → 命中会话
        _, hdr, _ = self._post({"model": "auto", "messages": [
            {"role": "user", "content": "帮我评审这个方案"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "补充一点"}]})
        self.assertEqual(hdr, "flag-model")
        self.assertEqual(_FakeRouterJudge.calls(), 1)
        # 不同首轮 user → 新会话重新定档
        _, hdr, _ = self._post({"model": "auto", "messages": [
            {"role": "user", "content": "另一个会话"}]})
        self.assertEqual(_FakeRouterJudge.calls(), 2)

    def test_header_priority_over_metadata(self):
        """会话 key 优先级：x-session-id 头 > metadata.session_id（同头不同 metadata 仍命中）。"""
        _FakeRouterJudge.reset(p=0.9)
        _, hdr, _ = self._auto([], headers={"x-session-id": "h1"},
                               metadata={"session_id": "m-other"})
        self.assertEqual(hdr, "flag-model")
        _, hdr, _ = self._auto(["追问"], headers={"x-session-id": "h1"},
                               metadata={"session_id": "m-changed"})
        self.assertEqual(hdr, "flag-model")
        self.assertEqual(_FakeRouterJudge.calls(), 1)

    def test_sessions_isolated(self):
        """不同会话 key 互不串档。"""
        _FakeRouterJudge.reset(p=0.9)
        self._auto([], headers={"x-session-id": "iso-a"})
        _FakeRouterJudge.set_p(0.1)
        _, hdr, _ = self._auto([], headers={"x-session-id": "iso-b"})
        self.assertEqual(hdr, "cheap-model")
        _, hdr, _ = self._auto(["追问"], headers={"x-session-id": "iso-a"})
        self.assertEqual(hdr, "flag-model")

    def test_ttl_expiry_reclassifies(self):
        """TTL 3600s 过期 → 会话条目失效重新定档（reason=classify，session=False）。"""
        _FakeRouterJudge.reset(p=0.9)
        self._auto([], headers={"x-session-id": "ttl1"})
        key = "h:ttl1"
        self.assertIn(key, shim_app._router_sessions)
        shim_app._router_sessions[key][1] = time.time() - 1  # 直接置过期
        _, hdr, _ = self._auto(["过期后续聊"], headers={"x-session-id": "ttl1"})
        self.assertEqual(hdr, "flag-model")
        self.assertEqual(_FakeRouterJudge.calls(), 2)
        rec = self._records()[0]
        self.assertEqual(rec["reason"], "classify")
        self.assertEqual(rec["session"], False)

    def test_hit_renews_ttl(self):
        """命中续期：继承后条目过期时间戳前移（TTL 3600s 重置）。"""
        _FakeRouterJudge.reset(p=0.9)
        self._auto([], headers={"x-session-id": "ttl2"})
        key = "h:ttl2"
        shim_app._router_sessions[key][1] = time.time() + 100  # 人为缩短，便于观测续期
        self._auto(["追问"], headers={"x-session-id": "ttl2"})
        exp2 = shim_app._router_sessions[key][1]
        self.assertGreater(exp2, time.time() + 3500)  # 续期满 TTL

    def test_lru_eviction(self):
        """LRU 容量封顶：超出 ROUTER_SESSION_MAX 逐出最久未用条目（重启即丢同义——
        被逐会话按新会话重新定档，无害）。"""
        _FakeRouterJudge.reset(p=0.9)
        with mock.patch.object(shim_app, "ROUTER_SESSION_MAX", 3):
            for sid in ("lru-1", "lru-2", "lru-3", "lru-4"):
                self._auto([], headers={"x-session-id": sid})
            self.assertNotIn("h:lru-1", shim_app._router_sessions)  # 最旧被逐
            self.assertEqual(len(shim_app._router_sessions), 3)
            # 被逐会话回聊 → 重新定档（再调一次分类器）
            self._auto(["追问"], headers={"x-session-id": "lru-1"})
            self.assertEqual(_FakeRouterJudge.calls(), 5)


class RouterConfigurableTest(RouterTestBase):
    """issue #119：routing 节可选增补键热配——prompt（分类系统提示）/escalate_conf（升档
    强置信门槛）/session_ttl（会话存态 TTL）/tool_loop_lock/thinking_lock（两道锁开关）。
    缺席=内置默认保现网行为逐点不变（_routing() fixture 不含新键，既有 Router* 用例
    全跑在默认态上，本类 test_defaults_preserve_current_behavior 再逐点锚定一遍）。"""

    def test_defaults_preserve_current_behavior(self):
        """默认值守恒（不写五个新键）：prompt 逐字=ROUTER_PROMPT_SYSTEM 常量；
        升档门槛 0.85（0.84 不升/0.85 升）；会话 TTL 3600 命中续期；两道锁全开。"""
        # prompt 逐字同常量
        self._post({"model": "auto", "messages": self._msgs(session_first="默认形态任务")})
        self.assertEqual(_FakeRouterJudge.captured()["messages"][0]["content"],
                         shim_app.ROUTER_PROMPT_SYSTEM)
        # 升档门槛默认 0.85：0.84 不升、0.85 升
        _, hdr, _ = self._auto([], headers={"x-session-id": "cfg-d1"})
        self.assertEqual(hdr, "cheap-model")
        _FakeRouterJudge.set_p(0.84)
        _, hdr, _ = self._auto(["追问"], headers={"x-session-id": "cfg-d1"})
        self.assertEqual(hdr, "cheap-model")
        self.assertEqual(self._records()[0]["reason"], "session_inherit")
        _FakeRouterJudge.set_p(0.85)
        _, hdr, _ = self._auto(["再追问"], headers={"x-session-id": "cfg-d1"})
        self.assertEqual(hdr, "flag-model")
        self.assertEqual(self._records()[0]["reason"], "escalate")
        # TTL 默认 3600：命中续期后过期戳 >now+3500
        self.assertGreater(shim_app._router_sessions["h:cfg-d1"][1], time.time() + 3500)
        # 锁默认全开①：tool_result 尾消息锁定——零分类调用、无头、reason=tool_loop_lock
        _FakeRouterJudge.reset(p=0.1)
        status, hdr, _ = self._post({"model": "auto", "messages": [
            {"role": "user", "content": "默认锁任务"},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}]})
        self.assertEqual(status, 200)
        self.assertIsNone(hdr)
        self.assertEqual(_FakeRouterJudge.calls(), 0)
        self.assertEqual(self._records()[0]["reason"], "tool_loop_lock")
        # 锁默认全开②：首轮 simple 判决带 thinking blocks → thinking_lock 不下结论
        _FakeRouterJudge.reset(p=0.1)
        _, hdr, _ = self._post({"model": "auto", "messages": [
            {"role": "user", "content": "默认锁任务二"},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "推理"}, {"type": "text", "text": "回答"}]},
            {"role": "user", "content": "轻量收尾"}]}, headers={"x-session-id": "cfg-d2"})
        self.assertIsNone(hdr)
        self.assertEqual(self._records()[0]["reason"], "thinking_lock")

    def test_custom_prompt_used(self):
        """prompt 自定义：分类请求 system 消息=新 prompt（settings 热更新，下一请求即生效）。"""
        self._write_settings(self._settings_payload(
            routing=self._routing(prompt="自定义路由提示词XYZ")))
        self._post({"model": "auto", "messages": self._msgs(session_first="自定义prompt任务")})
        req = _FakeRouterJudge.captured()
        self.assertEqual(req["messages"][0]["content"], "自定义路由提示词XYZ")
        self.assertEqual(req["messages"][1]["content"], "自定义prompt任务")  # 其余形状不变

    def test_escalate_conf_configurable(self):
        """escalate_conf=0.5：simple 存态 + 本轮 p=0.6（≥0.5，旧默认门槛 0.85 下不升）→
        升档 complex（reason=escalate）；边界 p=0.49<0.5 不升维持 simple。"""
        self._write_settings(self._settings_payload(
            routing=self._routing(escalate_conf=0.5)))
        _, hdr, _ = self._auto([], headers={"x-session-id": "cfg-e1"})
        self.assertEqual(hdr, "cheap-model")
        _FakeRouterJudge.set_p(0.6)
        _, hdr, _ = self._auto(["追问：写完整实现"], headers={"x-session-id": "cfg-e1"})
        self.assertEqual(hdr, "flag-model")
        rec = self._records()[0]
        self.assertEqual(rec["reason"], "escalate")
        self.assertEqual(rec["p_complex"], 0.6)
        # 边界：0.49 < 0.5 不升
        _FakeRouterJudge.set_p(0.1)  # 新会话首轮先定 simple
        _, hdr, _ = self._auto([], headers={"x-session-id": "cfg-e2"})
        self.assertEqual(hdr, "cheap-model")
        _FakeRouterJudge.set_p(0.49)
        _, hdr, _ = self._auto(["追问"], headers={"x-session-id": "cfg-e2"})
        self.assertEqual(hdr, "cheap-model")
        self.assertEqual(self._records()[0]["reason"], "session_inherit")

    def test_session_ttl_configurable(self):
        """session_ttl=1：存态写入过期戳为新 TTL 量级（非默认 3600）；1s 后命中即过期，
        按新会话重新定档（reason=classify、session=False、分类器再次调用）。"""
        self._write_settings(self._settings_payload(
            routing=self._routing(session_ttl=1)))
        _FakeRouterJudge.reset(p=0.9)
        _, hdr, _ = self._auto([], headers={"x-session-id": "cfg-t1"})
        self.assertEqual(hdr, "flag-model")
        self.assertLess(shim_app._router_sessions["h:cfg-t1"][1], time.time() + 100)
        time.sleep(1.1)
        _, hdr, _ = self._auto(["过期后续聊"], headers={"x-session-id": "cfg-t1"})
        self.assertEqual(hdr, "flag-model")
        self.assertEqual(_FakeRouterJudge.calls(), 2)
        rec = self._records()[0]
        self.assertEqual(rec["reason"], "classify")
        self.assertEqual(rec["session"], False)

    def test_tool_loop_lock_disabled(self):
        """tool_loop_lock=false：tool_result 尾消息不再锁定——走正常分类（分类器调用发生、
        按判决回结论、reason=classify）。对照默认开态同形状的零调用锁定由既有
        test_tool_result_tail_locks_without_session 锚定。"""
        self._write_settings(self._settings_payload(
            routing=self._routing(tool_loop_lock=False)))
        status, hdr, _ = self._post({"model": "auto", "messages": [
            {"role": "user", "content": "任务"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "exec", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}]})
        self.assertEqual(status, 200)
        self.assertEqual(hdr, "cheap-model")  # p=0.1 判 simple 正常回结论
        self.assertEqual(_FakeRouterJudge.calls(), 1)
        rec = self._records()[0]
        self.assertEqual(rec["reason"], "classify")
        self.assertEqual(rec["tier"], "simple")

    def test_thinking_lock_disabled(self):
        """thinking_lock=false：两处 thinking 锁点都解除——①首轮 simple 判决带 thinking
        blocks 正常下结论（默认态 thinking_lock 无头）；②simple 存态升档检出 thinking
        不再放弃（默认态维持 simple 落 thinking_lock）。"""
        self._write_settings(self._settings_payload(
            routing=self._routing(thinking_lock=False)))
        thinking_tail = [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "推理过程"},
                {"type": "text", "text": "回答"}]},
            {"role": "user", "content": "轻量收尾"},
        ]
        # ① 首轮定档锁点：p=0.1 判 simple → 正常回 cheap-model 落卡（默认态无头不落卡）
        _, hdr, _ = self._post(
            {"model": "auto", "messages": [{"role": "user", "content": "任务一"}] + thinking_tail},
            headers={"x-session-id": "cfg-k1"})
        self.assertEqual(hdr, "cheap-model")
        self.assertEqual(self._records()[0]["reason"], "classify")
        # ② 升档锁点：simple 存态 + 强置信 + thinking blocks → 正常升档（默认态放弃）
        _FakeRouterJudge.set_p(0.95)
        _, hdr, _ = self._auto([], headers={"x-session-id": "cfg-k1"}, extra_tail=thinking_tail)
        self.assertEqual(hdr, "flag-model")
        rec = self._records()[0]
        self.assertEqual(rec["reason"], "escalate")
        self.assertEqual(rec["p_complex"], 0.95)


if __name__ == "__main__":
    unittest.main()
