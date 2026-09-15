#!/usr/bin/env python3
"""sync-pricing 官方价锚同步纯函数单测（2026-09-15，issue #18 机制化）。

口径：compute_drifts/find_official_cost 直接喂假目录测——第一方过滤、官方别名、
cache_read None 落 prompt 全价、cache_write None 省略键、MANUAL 判定，不拉网。
运行：cd deploy/scripts && python3 -m unittest discover -s tests
"""
import importlib.util
import os
import sys
import unittest

_spec = importlib.util.spec_from_file_location(
    "sync_pricing", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sync-pricing.py"))
sp = importlib.util.module_from_spec(_spec)
sys.modules["sync_pricing"] = sp
_spec.loader.exec_module(sp)

CATALOG = {
    "openai": {"models": {
        "gpt-5.6-sol": {"cost": {"input": 4, "output": 20, "cache_read": 0.4, "cache_write": 5}},
        "gpt-5.6-luna": {"cost": {"input": 0.2, "output": 1.2, "cache_read": 0.02, "cache_write": 0.25}},
    }},
    "deepseek": {"models": {
        "deepseek-v4-flash": {"cost": {"input": 0.15, "output": 0.6, "cache_read": 0.003}},
    }},
    "alibaba": {"models": {
        "qwen3.6-27b": {"cost": {"input": 0.6, "output": 3.6}},  # 官方无 cache_read 价
    }},
    "openrouter": {"models": {  # reseller：永不采
        "gpt-5.6-sol": {"cost": {"input": 2, "output": 10}},
    }},
}


class TestFindOfficialCost(unittest.TestCase):
    def test_firstparty_hit(self):
        pid, cost = sp.find_official_cost(CATALOG, "gpt-5.6-sol")
        self.assertEqual(pid, "openai")
        self.assertEqual(cost["input"], 4)

    def test_reseller_never_used(self):
        # openai 第一方有收录时必须取 openai，绝不能落 openrouter 的 2/10
        _, cost = sp.find_official_cost(CATALOG, "gpt-5.6-sol")
        self.assertNotEqual(cost["input"], 2)

    def test_alias_resolves_channel_named_model(self):
        aliases = {"deepseek/deepseek-v4.1-flash": "deepseek:deepseek-v4-flash"}
        pid, cost = sp.find_official_cost(CATALOG, "deepseek/deepseek-v4.1-flash", aliases)
        self.assertEqual(pid, "deepseek")
        self.assertEqual(cost["output"], 0.6)  # 命中官方 deepseek-v4-flash

    def test_channel_named_model_without_alias_returns_none(self):
        # 渠道侧命名（前缀不在 PROVIDER_MAP）且未配别名 → 进 manuals，绝不猜 reseller
        self.assertIsNone(sp.find_official_cost(CATALOG, "deepseek/deepseek-v4.1-flash"))
        self.assertIsNone(sp.find_official_cost(CATALOG, "deepseek/deepseek-v4.1-flash", {}))

    def test_malformed_alias_ignored(self):
        # 别名值缺 ":" 属配置错误，按无别名处理 → None（模型进 manuals 提醒人工）
        aliases = {"deepseek/deepseek-v4.1-flash": "deepseek-v4-flash"}
        self.assertIsNone(sp.find_official_cost(CATALOG, "deepseek/deepseek-v4.1-flash", aliases))

    def test_unknown_returns_none(self):
        self.assertIsNone(sp.find_official_cost(CATALOG, "nonexistent-model"))


class TestComputeDrifts(unittest.TestCase):
    def test_no_drift_when_aligned(self):
        official = {"gpt-5.6-sol": {"prompt": 4, "completion": 20, "cached": 0.4, "cache_write": 5}}
        drifts, manuals = sp.compute_drifts(official, CATALOG)
        self.assertEqual(drifts, [])
        self.assertEqual(manuals, [])

    def test_drift_reports_each_changed_field(self):
        official = {"gpt-5.6-sol": {"prompt": 5, "completion": 30, "cached": 0.5}}
        drifts, _ = sp.compute_drifts(official, CATALOG)
        self.assertEqual(len(drifts), 1)
        _, _, new, changed = drifts[0]
        self.assertEqual(new["prompt"], 4)
        self.assertEqual(new["cache_write"], 5)  # 缺失的 cache_write 也补齐
        self.assertTrue(any("prompt" in c for c in changed))
        self.assertTrue(any("cache_write" in c for c in changed))

    def test_cache_read_none_falls_back_to_prompt(self):
        official = {"qwen3.6-27b": {"prompt": 0.6, "completion": 3.6, "cached": 0.04}}
        drifts, _ = sp.compute_drifts(official, CATALOG)
        new = drifts[0][2]
        self.assertEqual(new["cached"], 0.6)  # 保守取 prompt 全价，防缓存命中白送
        self.assertNotIn("cache_write", new)  # 官方无价则键省略（apply-pricing 不写该项）

    def test_alias_in_compute_drifts_clears_manual(self):
        # 同一模型：无别名 → manuals；pricing.json 配了 official_aliases → 正常比对锚
        official = {"deepseek/deepseek-v4.1-flash": {"prompt": 0.15, "completion": 0.6, "cached": 0.003}}
        _, manuals = sp.compute_drifts(official, CATALOG)
        self.assertEqual(manuals, ["deepseek/deepseek-v4.1-flash"])
        aliases = {"deepseek/deepseek-v4.1-flash": "deepseek:deepseek-v4-flash"}
        drifts, manuals = sp.compute_drifts(official, CATALOG, aliases)
        self.assertEqual(drifts, [])
        self.assertEqual(manuals, [])

    def test_unknown_model_goes_manual(self):
        official = {"mystery-model": {"prompt": 1, "completion": 2, "cached": 0.1}}
        drifts, manuals = sp.compute_drifts(official, CATALOG)
        self.assertEqual(drifts, [])
        self.assertEqual(manuals, ["mystery-model"])


if __name__ == "__main__":
    unittest.main()
