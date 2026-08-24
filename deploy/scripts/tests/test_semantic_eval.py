#!/usr/bin/env python3
"""semantic-eval 水位门禁纯函数单测（issue #99）。

口径：门禁判定拆成纯函数 evaluate_gate（三组水位数字 + judge 异常数 → 退出码/判定行），
阈值取模块常量（相对断言，不绑死具体数值，便于按实测水位调整）。
退出码：0=达标，1=水位不达标，2=judge 不可用率超线（judge 全挂时门禁必须非零，不许绿）。
运行：cd deploy/scripts && python3 -m unittest discover -s tests
"""
import importlib.util
import os
import sys
import unittest

_spec = importlib.util.spec_from_file_location(
    "semantic_eval", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "semantic-eval.py"))
se = importlib.util.module_from_spec(_spec)
sys.modules["semantic_eval"] = se  # 先注册再 exec：模块尾部的 if __name__ 保护下无副作用，但防御 future import 自引用
_spec.loader.exec_module(se)


def stats(novel_hit=None, bypass_hit=None, neg_fp=None, err=0, calls=None):
    """以模块门禁常量构造一组 stats：默认恰好压线达标，各用例在此基线上单向偏离。"""
    nh = se.GATE_MIN_NOVEL_HIT if novel_hit is None else novel_hit
    bh = se.GATE_MIN_BYPASS_HIT if bypass_hit is None else bypass_hit
    fp = se.GATE_MAX_NEG_FP if neg_fp is None else neg_fp
    novel = (nh, 14)
    bypass = (bh, 4)
    negative = (fp, 14)
    if calls is None:
        calls = novel[1] + bypass[1] + negative[1]
    return novel, bypass, negative, err, calls


class TestEvaluateGate(unittest.TestCase):
    def test_at_threshold_passes(self):
        """三组水位恰好压线、无异常 → exit 0。"""
        code, lines = se.evaluate_gate(*stats())
        self.assertEqual(code, 0, "\n".join(lines))

    def test_novel_below_threshold_fails(self):
        code, lines = se.evaluate_gate(*stats(novel_hit=se.GATE_MIN_NOVEL_HIT - 1))
        self.assertEqual(code, 1, "\n".join(lines))

    def test_bypass_below_threshold_fails(self):
        code, lines = se.evaluate_gate(*stats(bypass_hit=se.GATE_MIN_BYPASS_HIT - 1))
        self.assertEqual(code, 1, "\n".join(lines))

    def test_neg_fp_above_threshold_fails(self):
        code, lines = se.evaluate_gate(*stats(neg_fp=se.GATE_MAX_NEG_FP + 1))
        self.assertEqual(code, 1, "\n".join(lines))

    def test_judge_all_down_exit_2(self):
        """judge 全挂（异常率 100%）→ exit 2，门禁不得因 judge 不可用而绿。"""
        n, b, g, _e, calls = stats()
        code, lines = se.evaluate_gate(n, b, g, err=calls, calls=calls)
        self.assertEqual(code, 2, "\n".join(lines))

    def test_judge_err_rate_over_line_exit_2(self):
        """异常率超线（即便水位数字够）→ exit 2。"""
        calls = 32
        over = int(calls * se.GATE_MAX_ERR_RATE) + 1
        code, lines = se.evaluate_gate(*stats(err=over, calls=calls))
        self.assertEqual(code, 2, "\n".join(lines))

    def test_judge_err_rate_at_line_not_exit_2(self):
        """异常率恰在线上 → 不因异常判 2（水位达标则 exit 0）。"""
        calls = 32
        at = int(calls * se.GATE_MAX_ERR_RATE)
        code, lines = se.evaluate_gate(*stats(err=at, calls=calls))
        self.assertEqual(code, 0, "\n".join(lines))

    def test_zero_calls_exit_2(self):
        """一次都没调成（calls=0）→ exit 2，防除零/空转绿。"""
        code, lines = se.evaluate_gate((0, 14), (0, 4), (0, 14), err=0, calls=0)
        self.assertEqual(code, 2, "\n".join(lines))

    def test_lines_report_each_group(self):
        """判定行要给出三组水位与要求，便于回归日志定位哪组不达标。"""
        code, lines = se.evaluate_gate(*stats(novel_hit=0))
        text = "\n".join(lines)
        self.assertIn("semantic_novel", text)
        self.assertIn("bypass", text)
        self.assertIn("negative", text)


if __name__ == "__main__":
    unittest.main()
