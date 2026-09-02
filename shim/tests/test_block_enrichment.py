#!/usr/bin/env python3
"""block 层阻断记录增强测试（issue #134：拦截日志补身份与命中摘录）。

覆盖：
- shadow_log.record 新键（side/key_hash/excerpts）非 None 才写的纪律；
- app.bearer_token / key_hash_from_headers 从 Authorization 头取 token 算 SHA-256；
- app._mask_excerpt 掩码（短串全掩、长串留头尾）；
- app.block_excerpts：词表命中原样（管理员自配词表）、secrets 命中掩码、条数/长度上限；
- admin_api._enrich_block_records：key_hash → key 名/用户邮箱映射（fetcher 注入，不触网）。
"""
import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import admin_api
import app
import shadow_log


class TestRecordNewKeys(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "shadow.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_side_keyhash_excerpts_roundtrip(self):
        shadow_log.record("block", hit=True, blocked=True, rule_ids=["confidential.x"],
                          side="request", key_hash="ab12", excerpts=[{"rule": "confidential.x", "text": "北极星"}],
                          path=self.path)
        rec = shadow_log.tail(1, path=self.path)[0]
        self.assertEqual(rec["side"], "request")
        self.assertEqual(rec["key_hash"], "ab12")
        self.assertEqual(rec["excerpts"], [{"rule": "confidential.x", "text": "北极星"}])

    def test_new_keys_omitted_when_none(self):
        shadow_log.record("block", hit=True, blocked=True, rule_ids=["secrets.y"], path=self.path)
        rec = shadow_log.tail(1, path=self.path)[0]
        for k in ("side", "key_hash", "excerpts"):
            self.assertNotIn(k, rec)


class TestKeyHash(unittest.TestCase):
    def test_bearer_token_and_hash(self):
        headers = {"Authorization": "Bearer ah-testtoken123"}
        self.assertEqual(app.bearer_token(headers), "ah-testtoken123")
        self.assertEqual(
            app.key_hash_from_headers(headers),
            hashlib.sha256(b"ah-testtoken123").hexdigest(),
        )

    def test_missing_or_malformed_header(self):
        self.assertIsNone(app.key_hash_from_headers({}))
        self.assertIsNone(app.key_hash_from_headers({"Authorization": "Basic xyz"}))
        self.assertIsNone(app.key_hash_from_headers({"Authorization": "Bearer "}))


class TestMaskExcerpt(unittest.TestCase):
    def test_short_fully_masked(self):
        self.assertEqual(app._mask_excerpt("abc"), "****")
        self.assertEqual(app._mask_excerpt("abcd"), "****")

    def test_long_keeps_head_tail(self):
        self.assertEqual(app._mask_excerpt("sk-1234567890abcdef"), "sk***ef")

    def test_cap_length(self):
        s = "x" * 100
        self.assertLessEqual(len(app._mask_excerpt(s)), 30)


class TestBlockExcerpts(unittest.TestCase):
    TERMS = [{"value": "北极星计划", "rule_id": "confidential.codename"}]

    def test_term_hits_verbatim(self):
        ex = app.block_excerpts("提到北极星计划", self.TERMS, [], [], rules=[])
        self.assertIn({"rule": "confidential.codename", "text": "北极星计划"}, ex)

    def test_analyze_hits_verbatim(self):
        hits = [{"rule_id": "confidential.codename", "term": "北极星计划"}]
        ex = app.block_excerpts("", [], hits, [], rules=[])
        self.assertIn({"rule": "confidential.codename", "text": "北极星计划"}, ex)

    def test_secret_hit_masked(self):
        rules = [{
            "code": "secrets.openai_sk", "action": "reject", "enabled": True,
            "shim_patterns": [r"sk-[A-Za-z0-9]{8,}"],
        }]
        norm = "key 是 sk-abcdef123456 谢谢"
        ex = app.block_excerpts(norm, [], [], ["secrets.openai_sk"], rules=rules)
        self.assertEqual(ex, [{"rule": "secrets.openai_sk", "text": "sk***56"}])
        # 不含完整明文
        self.assertNotIn("sk-abcdef123456", str(ex))

    def test_cap_and_dedupe(self):
        terms = [{"value": f"词{i}", "rule_id": f"confidential.t{i}"} for i in range(8)]
        hits = [{"rule_id": f"confidential.t{i}", "term": f"词{i}"} for i in range(8)]
        ex = app.block_excerpts("", terms, hits, [], rules=[])
        self.assertLessEqual(len(ex), 5)


class TestEnrichBlockRecords(unittest.TestCase):
    def test_hash_matched_to_key_and_user(self):
        tok = "ah-real-token"
        kh = hashlib.sha256(tok.encode()).hexdigest()
        keys = [
            {"key": tok, "name": "张三的 key", "user": {"email": "zhangsan@example.com"}},
            {"key": "ah-other", "name": "别的", "user": {"email": "other@example.com"}},
        ]
        recs = [{"layer": "block", "key_hash": kh}, {"layer": "block"}, {"layer": "block", "key_hash": "0" * 64}]
        out = admin_api._enrich_block_records(recs, lambda: keys)
        self.assertEqual(out[0]["key_name"], "张三的 key")
        self.assertEqual(out[0]["user_email"], "zhangsan@example.com")
        self.assertNotIn("key_name", out[1])  # 无 key_hash 不标注
        self.assertNotIn("key_name", out[2])  # 哈希对不上不臆造
        # 原记录不被改写（返回副本）
        self.assertNotIn("key_name", recs[0])

    def test_fetch_failure_fail_open(self):
        recs = [{"layer": "block", "key_hash": "ab"}]
        out = admin_api._enrich_block_records(recs, lambda: (_ for _ in ()).throw(RuntimeError("down")))
        self.assertEqual(out, recs)


if __name__ == "__main__":
    unittest.main()
