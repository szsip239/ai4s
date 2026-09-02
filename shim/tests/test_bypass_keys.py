#!/usr/bin/env python3
"""bypass_keys Key 级 DLP 绕行名单测试（issue #129）。

seam 纪律：bypass_keys 为纯模块 seam（load/add/lookup/update/remove 公开函数，path 显式注入
tmp 文件）；断言只走读回的字段语义——id 是 token 的 SHA-256 哈希，文件与返回值绝不落明文 token。
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bypass_keys


class TestAddLookup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "bypass-keys.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_then_lookup_roundtrip(self):
        entry = bypass_keys.add("sk-test-abc123", "CI 管道", "all", None, "admin@corp", path=self.path)
        self.assertEqual(entry["id"], hashlib.sha256(b"sk-test-abc123").hexdigest())
        self.assertEqual(entry["label"], "CI 管道")
        self.assertEqual(entry["scope"], "all")
        self.assertTrue(entry["enabled"])
        self.assertEqual(entry["added_by"], "admin@corp")
        self.assertIn("added_at", entry)
        # lookup 命中
        got = bypass_keys.lookup("sk-test-abc123", path=self.path)
        self.assertIsNotNone(got)
        self.assertEqual(got["id"], entry["id"])

    def test_token_never_persisted_plaintext(self):
        bypass_keys.add("sk-secret-xyz", "n", "all", None, "a", path=self.path)
        raw = open(self.path, encoding="utf-8").read()
        self.assertNotIn("sk-secret-xyz", raw)
        data = json.loads(raw)
        self.assertNotIn("token", json.dumps(data))

    def test_lookup_unknown_token_none(self):
        bypass_keys.add("sk-a", "n", "all", None, "a", path=self.path)
        self.assertIsNone(bypass_keys.lookup("sk-other", path=self.path))

    def test_lookup_disabled_entry_none(self):
        entry = bypass_keys.add("sk-a", "n", "all", None, "a", path=self.path)
        bypass_keys.set_enabled(entry["id"], False, path=self.path)
        self.assertIsNone(bypass_keys.lookup("sk-a", path=self.path))

    def test_duplicate_token_rejected(self):
        bypass_keys.add("sk-a", "n", "all", None, "a", path=self.path)
        with self.assertRaises(ValueError):
            bypass_keys.add("sk-a", "n2", "layers", ["l2"], "a", path=self.path)


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "bypass-keys.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_token_rejected(self):
        with self.assertRaises(ValueError):
            bypass_keys.add("", "n", "all", None, "a", path=self.path)

    def test_empty_label_rejected(self):
        with self.assertRaises(ValueError):
            bypass_keys.add("sk-a", "  ", "all", None, "a", path=self.path)

    def test_bad_scope_rejected(self):
        with self.assertRaises(ValueError):
            bypass_keys.add("sk-a", "n", "everything", None, "a", path=self.path)

    def test_layers_scope_requires_layers(self):
        with self.assertRaises(ValueError):
            bypass_keys.add("sk-a", "n", "layers", [], "a", path=self.path)

    def test_unknown_layer_rejected(self):
        with self.assertRaises(ValueError):
            bypass_keys.add("sk-a", "n", "layers", ["l2", "bogus"], "a", path=self.path)

    def test_all_scope_ignores_layers(self):
        entry = bypass_keys.add("sk-a", "n", "all", ["l2"], "a", path=self.path)
        self.assertEqual(entry["scope"], "all")


class TestCovers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "bypass-keys.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_scope_all_covers_every_layer(self):
        bypass_keys.add("sk-a", "n", "all", None, "a", path=self.path)
        entry = bypass_keys.lookup("sk-a", path=self.path)
        for layer in bypass_keys.BYPASSABLE_LAYERS:
            self.assertTrue(bypass_keys.covers(entry, layer), layer)

    def test_scope_layers_covers_only_listed(self):
        bypass_keys.add("sk-a", "n", "layers", ["l2", "edm"], "a", path=self.path)
        entry = bypass_keys.lookup("sk-a", path=self.path)
        self.assertTrue(bypass_keys.covers(entry, "l2"))
        self.assertTrue(bypass_keys.covers(entry, "edm"))
        self.assertFalse(bypass_keys.covers(entry, "pg"))
        self.assertFalse(bypass_keys.covers(entry, "l1"))


class TestUpdateRemove(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "bypass-keys.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_scope_and_layers(self):
        entry = bypass_keys.add("sk-a", "n", "layers", ["l2"], "a", path=self.path)
        updated = bypass_keys.update(entry["id"], {"scope": "layers", "layers": ["pg"]}, path=self.path)
        self.assertEqual(updated["layers"], ["pg"])
        got = bypass_keys.lookup("sk-a", path=self.path)
        self.assertTrue(bypass_keys.covers(got, "pg"))
        self.assertFalse(bypass_keys.covers(got, "l2"))

    def test_update_validates_layers(self):
        entry = bypass_keys.add("sk-a", "n", "layers", ["l2"], "a", path=self.path)
        with self.assertRaises(ValueError):
            bypass_keys.update(entry["id"], {"layers": ["bogus"]}, path=self.path)

    def test_update_enabled(self):
        entry = bypass_keys.add("sk-a", "n", "all", None, "a", path=self.path)
        updated = bypass_keys.update(entry["id"], {"enabled": False}, path=self.path)
        self.assertFalse(updated["enabled"])
        self.assertIsNone(bypass_keys.lookup("sk-a", path=self.path))  # 停用即不绕行
        updated = bypass_keys.update(entry["id"], {"enabled": True}, path=self.path)
        self.assertTrue(updated["enabled"])
        self.assertIsNotNone(bypass_keys.lookup("sk-a", path=self.path))

    def test_remove(self):
        entry = bypass_keys.add("sk-a", "n", "all", None, "a", path=self.path)
        bypass_keys.remove(entry["id"], path=self.path)
        self.assertIsNone(bypass_keys.lookup("sk-a", path=self.path))
        self.assertEqual(bypass_keys.load(path=self.path)["keys"], [])

    def test_update_unknown_id_keyerror(self):
        with self.assertRaises(KeyError):
            bypass_keys.update("0" * 64, {"label": "x"}, path=self.path)

    def test_load_missing_file_empty(self):
        data = bypass_keys.load(path=self.path)
        self.assertEqual(data["keys"], [])


if __name__ == "__main__":
    unittest.main()
