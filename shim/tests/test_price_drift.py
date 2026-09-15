#!/usr/bin/env python3
"""alert_poller 价格锚漂移周检单测（2026-09-15，issue #18 机制化接入巡检项 8）。

seam 纪律（同 test_alert_poller.py）：price_drift_hash/price_drift_decision/price_manual_decision
纯函数直接测；price_drift_check 只在线路边界 mock（_load_pricing_sync / send_feishu / open / time），
不 mock 模块内部判定函数。运行：cd shim && ./.venv/bin/python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alert_poller as ap

DRIFT_A = [("gpt-5.6-sol", "openai", {"prompt": 4}, ["prompt: 5.0 → 4", "completion: 30.0 → 20"])]
DRIFT_B = [("qwen3.6-27b", "alibaba", {"prompt": 0.6}, ["prompt: 0.2 → 0.6"])]


class TestPriceDriftHash(unittest.TestCase):
    def test_stable_for_same_content(self):
        self.assertEqual(ap.price_drift_hash(DRIFT_A), ap.price_drift_hash(list(DRIFT_A)))

    def test_changes_with_content(self):
        self.assertNotEqual(ap.price_drift_hash(DRIFT_A), ap.price_drift_hash(DRIFT_B))


class TestPriceDriftDecision(unittest.TestCase):
    def test_alert_on_new_drift(self):
        action, text, h = ap.price_drift_decision(DRIFT_A, [], None)
        self.assertEqual(action, "alert")
        self.assertIn("gpt-5.6-sol", text)
        self.assertIn("prompt: 5.0 → 4", text)
        self.assertIn("sync-pricing.py --check", text)  # 处置指引
        self.assertEqual(h, ap.price_drift_hash(DRIFT_A))

    def test_no_repeat_when_content_unchanged(self):
        prev = ap.price_drift_hash(DRIFT_A)
        action, _, h = ap.price_drift_decision(DRIFT_A, [], prev)
        self.assertIsNone(action)
        self.assertEqual(h, prev)

    def test_realert_when_content_changes(self):
        prev = ap.price_drift_hash(DRIFT_A)
        action, text, _ = ap.price_drift_decision(DRIFT_B, [], prev)
        self.assertEqual(action, "alert")
        self.assertIn("qwen3.6-27b", text)

    def test_recover_when_resolved(self):
        prev = ap.price_drift_hash(DRIFT_A)
        action, text, h = ap.price_drift_decision([], [], prev)
        self.assertEqual(action, "recover")
        self.assertIsNone(h)

    def test_silent_when_clean_and_no_prev(self):
        action, _, h = ap.price_drift_decision([], [], None)
        self.assertIsNone(action)
        self.assertIsNone(h)

    def test_manual_line_included(self):
        _, text, _ = ap.price_drift_decision(DRIFT_A, ["mystery-model"], None)
        self.assertIn("mystery-model", text)

    def test_lines_capped(self):
        many = [(f"m{i}", "p", {}, [f"x: {i} → {i}"]) for i in range(ap.PRICE_DRIFT_MAX_LINES + 3)]
        _, text, _ = ap.price_drift_decision(many, [], None)
        self.assertIn("另 3 项", text)


MANUALS_A = ["mystery-model"]
MANUALS_B = ["mystery-model", "mystery-2（zhipu 缺 input/output 价）"]


class TestPriceManualDecision(unittest.TestCase):
    def test_alert_on_new_manuals(self):
        action, text, h = ap.price_manual_decision(MANUALS_A, None)
        self.assertEqual(action, "alert")
        self.assertIn("待人工维护 1 项", text)
        self.assertIn("mystery-model", text)
        self.assertIn("official_aliases", text)  # 处置指引
        self.assertEqual(h, ap.price_manual_hash(MANUALS_A))

    def test_no_repeat_when_set_unchanged(self):
        prev = ap.price_manual_hash(MANUALS_A)
        action, _, h = ap.price_manual_decision(MANUALS_A, prev)
        self.assertIsNone(action)
        self.assertEqual(h, prev)

    def test_realert_when_set_changes(self):
        prev = ap.price_manual_hash(MANUALS_A)
        action, text, _ = ap.price_manual_decision(MANUALS_B, prev)
        self.assertEqual(action, "alert")
        self.assertIn("待人工维护 2 项", text)

    def test_recover_when_all_mapped(self):
        prev = ap.price_manual_hash(MANUALS_A)
        action, text, h = ap.price_manual_decision([], prev)
        self.assertEqual(action, "recover")
        self.assertIn("全部纳入官方目录同步", text)
        self.assertIsNone(h)

    def test_silent_when_clean_and_no_prev(self):
        action, _, h = ap.price_manual_decision([], None)
        self.assertIsNone(action)
        self.assertIsNone(h)


class _FakeSync:
    """线路边界桩：sync-pricing 模块形状（compute_drifts + fetch_catalog）。"""

    def __init__(self, drifts, manuals=(), boom=False):
        self._drifts, self._manuals, self._boom = drifts, manuals, boom
        self.seen_aliases = "unset"

    def fetch_catalog(self, _offline):
        if self._boom:
            raise ConnectionError("models.dev unreachable")
        return {"fake": "catalog"}

    def compute_drifts(self, official, _catalog, aliases=None):
        self.seen_aliases = aliases
        return list(self._drifts), list(self._manuals)


class TestPriceDriftCheck(unittest.TestCase):
    def _pricing_file(self, d, aliases=None):
        p = os.path.join(d, "pricing.json")
        cfg = {"official_prices_per_million_usd": {"x": {"prompt": 1}}}
        if aliases is not None:
            cfg["official_aliases"] = aliases
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        return p

    def test_skips_within_weekly_interval(self):
        state = {"priceDrift:lastRun": 1000.0}
        with mock.patch.object(ap.time, "time", return_value=1000.0 + ap.PRICE_DRIFT_INTERVAL - 10):
            ap.price_drift_check(state)  # 未到点：不加载 sync（若加载会抛文件不存在）
        self.assertEqual(state["priceDrift:lastRun"], 1000.0)

    def test_due_drift_alerts_and_stores_hash(self):
        with tempfile.TemporaryDirectory() as d:
            state = {}
            with mock.patch.object(ap.time, "time", return_value=10_000_000.0), \
                 mock.patch.object(ap, "PRICING_JSON_PATH", self._pricing_file(d)), \
                 mock.patch.object(ap, "_load_pricing_sync", return_value=_FakeSync(DRIFT_A)), \
                 mock.patch.object(ap, "send_feishu", return_value=True) as send:
                ap.price_drift_check(state)
            send.assert_called_once()
            self.assertIn("gpt-5.6-sol", send.call_args[0][0])
            self.assertEqual(state["priceDrift:hash"], ap.price_drift_hash(DRIFT_A))
            self.assertEqual(state["priceDrift:lastRun"], 10_000_000.0)
            self.assertFalse(state["priceDrift:failed"])

    def test_clean_run_silent(self):
        with tempfile.TemporaryDirectory() as d:
            state = {}
            with mock.patch.object(ap.time, "time", return_value=10_000_000.0), \
                 mock.patch.object(ap, "PRICING_JSON_PATH", self._pricing_file(d)), \
                 mock.patch.object(ap, "_load_pricing_sync", return_value=_FakeSync([])), \
                 mock.patch.object(ap, "send_feishu", return_value=True) as send:
                ap.price_drift_check(state)
            send.assert_not_called()
            self.assertNotIn("priceDrift:hash", state)

    def test_fetch_failure_marks_retry(self):
        with tempfile.TemporaryDirectory() as d:
            state = {}
            with mock.patch.object(ap.time, "time", return_value=10_000_000.0), \
                 mock.patch.object(ap, "PRICING_JSON_PATH", self._pricing_file(d)), \
                 mock.patch.object(ap, "_load_pricing_sync", return_value=_FakeSync([], boom=True)), \
                 mock.patch.object(ap, "send_feishu") as send:
                ap.price_drift_check(state)
            send.assert_not_called()
            self.assertTrue(state["priceDrift:failed"])
            # 失败后按重试间隔门控：RETRY 秒内不重复跑
            with mock.patch.object(ap.time, "time", return_value=10_000_000.0 + ap.PRICE_DRIFT_RETRY - 10):
                ap.price_drift_check(state)
            self.assertEqual(state["priceDrift:lastRun"], 10_000_000.0)

    def test_send_failure_retries_without_storing_hash(self):
        with tempfile.TemporaryDirectory() as d:
            state = {}
            with mock.patch.object(ap.time, "time", return_value=10_000_000.0), \
                 mock.patch.object(ap, "PRICING_JSON_PATH", self._pricing_file(d)), \
                 mock.patch.object(ap, "_load_pricing_sync", return_value=_FakeSync(DRIFT_A)), \
                 mock.patch.object(ap, "send_feishu", return_value=False):
                ap.price_drift_check(state)
            self.assertTrue(state["priceDrift:failed"])  # 下轮按重试间隔补发
            self.assertNotIn("priceDrift:hash", state)

    def test_official_aliases_passed_to_compute_drifts(self):
        aliases = {"deepseek/deepseek-v4.1-flash": "deepseek:deepseek-v4-flash"}
        fake = _FakeSync([])
        with tempfile.TemporaryDirectory() as d:
            state = {}
            with mock.patch.object(ap.time, "time", return_value=10_000_000.0), \
                 mock.patch.object(ap, "PRICING_JSON_PATH", self._pricing_file(d, aliases)), \
                 mock.patch.object(ap, "_load_pricing_sync", return_value=fake), \
                 mock.patch.object(ap, "send_feishu", return_value=True):
                ap.price_drift_check(state)
        self.assertEqual(fake.seen_aliases, aliases)

    def test_manuals_alert_and_store_manual_hash(self):
        with tempfile.TemporaryDirectory() as d:
            state = {}
            with mock.patch.object(ap.time, "time", return_value=10_000_000.0), \
                 mock.patch.object(ap, "PRICING_JSON_PATH", self._pricing_file(d)), \
                 mock.patch.object(ap, "_load_pricing_sync", return_value=_FakeSync([], MANUALS_A)), \
                 mock.patch.object(ap, "send_feishu", return_value=True) as send:
                ap.price_drift_check(state)
            send.assert_called_once()
            self.assertIn("待人工维护", send.call_args[0][0])
            self.assertEqual(state["priceDrift:manualHash"], ap.price_manual_hash(MANUALS_A))

    def test_manuals_recover_clears_manual_hash(self):
        with tempfile.TemporaryDirectory() as d:
            state = {"priceDrift:manualHash": ap.price_manual_hash(MANUALS_A)}
            with mock.patch.object(ap.time, "time", return_value=10_000_000.0), \
                 mock.patch.object(ap, "PRICING_JSON_PATH", self._pricing_file(d)), \
                 mock.patch.object(ap, "_load_pricing_sync", return_value=_FakeSync([])), \
                 mock.patch.object(ap, "send_feishu", return_value=True) as send:
                ap.price_drift_check(state)
            send.assert_called_once()
            self.assertIn("全部纳入官方目录同步", send.call_args[0][0])
            self.assertIsNone(state["priceDrift:manualHash"])

    def test_drift_and_manual_cards_sent_separately(self):
        # 漂移 + 手工锚同轮：两张卡各自防抖，互不影响
        with tempfile.TemporaryDirectory() as d:
            state = {}
            with mock.patch.object(ap.time, "time", return_value=10_000_000.0), \
                 mock.patch.object(ap, "PRICING_JSON_PATH", self._pricing_file(d)), \
                 mock.patch.object(ap, "_load_pricing_sync", return_value=_FakeSync(DRIFT_A, MANUALS_A)), \
                 mock.patch.object(ap, "send_feishu", return_value=True) as send:
                ap.price_drift_check(state)
            self.assertEqual(send.call_count, 2)
            self.assertEqual(state["priceDrift:hash"], ap.price_drift_hash(DRIFT_A))
            self.assertEqual(state["priceDrift:manualHash"], ap.price_manual_hash(MANUALS_A))


if __name__ == "__main__":
    unittest.main()
