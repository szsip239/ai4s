#!/usr/bin/env python3
"""shadow_log shadow 判定持久化测试（issue #92：观测闭环——判定不落 stdout 即止，可持久查询/统计）。

seam 纪律：shadow_log 为纯模块 seam（record/tail/stats 公开函数，path 显式注入 tmp 文件）；
不触内部行格式——断言只走 tail/stats 读回的字段语义。
"""
import os
import sys
import tempfile
import unittest

# 让测试可 import shim 目录下的 shadow_log（discover 从 shim/tests 启动）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_log


class TestRecordTail(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "shadow.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_then_tail_roundtrip_newest_first(self):
        shadow_log.record("judge", hit=True, confidence=0.9, latency_ms=120, entities=2, path=self.path)
        shadow_log.record("pg", hit=False, score=0.1, latency_ms=30, path=self.path)
        recs = shadow_log.tail(10, path=self.path)
        self.assertEqual(len(recs), 2)
        # 新到旧
        self.assertEqual(recs[0]["layer"], "pg")
        self.assertEqual(recs[1]["layer"], "judge")
        # 字段语义
        j = recs[1]
        self.assertTrue(j["hit"])
        self.assertAlmostEqual(j["confidence"], 0.9)
        self.assertEqual(j["latency_ms"], 120)
        self.assertEqual(j["entities"], 2)  # 只存命中实体数，不落实体字符串（不落原文）
        self.assertIsNone(j["error"])
        self.assertIn("ts", j)
        p = recs[0]
        self.assertFalse(p["hit"])
        self.assertAlmostEqual(p["score"], 0.1)

    def test_record_error_entry(self):
        shadow_log.record("judge", error="unavailable", latency_ms=8000, path=self.path)
        recs = shadow_log.tail(1, path=self.path)
        self.assertEqual(recs[0]["error"], "unavailable")
        self.assertIsNone(recs[0]["hit"])

    def test_tail_empty_file(self):
        self.assertEqual(shadow_log.tail(10, path=self.path), [])

    def test_record_never_raises_on_unwritable_path(self):
        # 检测路径纪律：持久化失败绝不抛（只记日志），不拖垮 /request 应答
        shadow_log.record("judge", hit=True, path="/nonexistent-dir-xyz/deep/shadow.jsonl")

    def test_record_blocked_roundtrip(self):
        """阻断事件字段（issue #103）：blocked/block_threshold/model 随条落盘并读回
        （alert_poller 阻断巡检消费）；未阻断条三键为 None；stats 消费不炸
        （blocked 条 hit=True 照常计入 hits）。"""
        shadow_log.record("pg", hit=True, score=0.998, latency_ms=140,
                          blocked=True, block_threshold=0.9, model="echo-test", path=self.path)
        shadow_log.record("pg", hit=False, score=0.3, latency_ms=50, path=self.path)
        recs = shadow_log.tail(10, path=self.path)
        self.assertEqual(len(recs), 2)
        b = recs[1]  # 新到旧：阻断条先落盘，是较旧一条
        self.assertIs(b["blocked"], True)
        self.assertEqual(b["block_threshold"], 0.9)
        self.assertEqual(b["model"], "echo-test")
        # 未阻断条保持旧形状：三个阻断键不写入（消费方 .get() 语义）
        self.assertNotIn("blocked", recs[0])
        self.assertNotIn("block_threshold", recs[0])
        self.assertNotIn("model", recs[0])
        s = shadow_log.stats("pg", window=10, path=self.path)
        self.assertEqual((s["total"], s["hits"], s["errors"]), (2, 1, 0))


class TestStats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "shadow.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_stats_counts_and_rates(self):
        # 窗口内 4 条 judge：2 正常（1 命中）+ 2 异常
        shadow_log.record("judge", hit=True, confidence=0.9, latency_ms=100, path=self.path)
        shadow_log.record("judge", hit=False, confidence=0.2, latency_ms=300, path=self.path)
        shadow_log.record("judge", error="unavailable", path=self.path)
        shadow_log.record("judge", error="unavailable", path=self.path)
        # 别层记录不混入
        shadow_log.record("pg", hit=True, score=0.95, latency_ms=50, path=self.path)
        s = shadow_log.stats("judge", window=20, path=self.path)
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["errors"], 2)
        self.assertAlmostEqual(s["error_rate"], 0.5)
        self.assertEqual(s["hits"], 1)
        self.assertEqual(s["avg_latency_ms"], 200)  # 仅计有 latency 的正常记录
        self.assertIsNotNone(s["last_ts"])

    def test_stats_window_caps(self):
        # 窗口只取最近 N 条
        for i in range(5):
            shadow_log.record("pg", hit=False, score=0.1, path=self.path)
        shadow_log.record("pg", error="unavailable", path=self.path)
        s = shadow_log.stats("pg", window=3, path=self.path)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["errors"], 1)

    def test_stats_empty(self):
        s = shadow_log.stats("judge", window=20, path=self.path)
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["errors"], 0)
        self.assertEqual(s["error_rate"], 0.0)
        self.assertEqual(s["hits"], 0)
        self.assertIsNone(s["avg_latency_ms"])
        self.assertIsNone(s["last_ts"])


class TestTrim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "shadow.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_oversize_trims_to_newer_half(self):
        os.environ["SHADOW_LOG_MAX_BYTES"] = "400"
        try:
            for i in range(20):
                shadow_log.record("judge", hit=(i % 2 == 0), confidence=0.8,
                                  latency_ms=100 + i, path=self.path)
        finally:
            del os.environ["SHADOW_LOG_MAX_BYTES"]
        self.assertLessEqual(os.path.getsize(self.path), 400)
        recs = shadow_log.tail(100, path=self.path)
        # 截尾后留存的是最新一段；全部记录仍是合法形状（tail 可读、无坏行混入）
        self.assertTrue(0 < len(recs) < 20)
        self.assertTrue(all(r["layer"] == "judge" for r in recs))


if __name__ == "__main__":
    unittest.main()
