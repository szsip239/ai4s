#!/usr/bin/env python3
"""DLP 内容阻断观测闭环测试（issue #130）：/request 的词表/归一化 secrets/EDM 451 分支
须 shadow_log 落条（layer=block, blocked=True, rule_ids 脱敏字段, model）+ 控制台出口可查。

seam 纪律同 test_bypass：活 shim Handler 起本地端口，词表/format-rules/settings/shadow_log
全部指向 tmp；断言只盯 webhook 协议应答形状与 shadow_log 条（不落原文、不记 token）。
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
import shadow_log


def _start(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


_SHIM = _start(shim_app.Handler)
_BASE = f"http://127.0.0.1:{_SHIM.server_address[1]}"


def _post(path, payload, model=None):
    req = urllib.request.Request(_BASE + path, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if model:
        req.add_header("x-model", model)  # 生产链路模型名来源（issue #116 网关 CEL 注入）
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class BlockObserveTest(unittest.TestCase):
    """内容阻断 451 的观测闭环（落条 + 脱敏字段）。"""

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

    def _blocks(self):
        return [r for r in shadow_log.tail(50, path=self.shadow) if r.get("layer") == "block"]

    @staticmethod
    def _payload(text):
        return {"body": {"messages": [{"role": "user", "content": text}]}}

    def test_wordlist_451_records_block(self):
        status, body = _post("/request", self._payload("凤凰计划进展如何"), model="echo-test")
        self.assertEqual(status, 200)
        self.assertEqual(body["action"]["status_code"], 451)
        blocks = self._blocks()
        self.assertEqual(len(blocks), 1)
        rec = blocks[0]
        self.assertTrue(rec["blocked"])
        self.assertIn("confidential.codename", rec["rule_ids"])
        self.assertEqual(rec.get("model"), "echo-test")  # x-model 头 → 审计带模型名
        self.assertNotIn("凤凰计划", json.dumps(rec, ensure_ascii=False))  # 不落原文

    def test_normalized_secret_451_records_block(self):
        # 归一化 secrets（L1 shim 侧）：全角/分隔变体归一后命中 shim_patterns
        _, body = _post("/request", self._payload("我的 key 是 sk-AＢＣＤＥＦＧＨ12345"))
        self.assertEqual(body["action"]["status_code"], 451)
        blocks = self._blocks()
        self.assertEqual(len(blocks), 1)
        self.assertIn("secrets.test_sk", blocks[0]["rule_ids"])
        self.assertNotIn("model", blocks[0])  # 无 x-model 头 → 键级省略纪律

    def test_clean_request_no_block_record(self):
        _, body = _post("/request", self._payload("今天天气怎么样"))
        self.assertEqual(body["action"].get("reason"), "pass")
        self.assertEqual(self._blocks(), [])


if __name__ == "__main__":
    unittest.main()
