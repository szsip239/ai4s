#!/usr/bin/env python3
"""shim admin 平面（issue #31 骨架）接口行为 + 原子写工具测试。

seam 纪律：
- HTTP 接口：进程内起真实 ThreadingHTTPServer 跑 app.Handler（含 admin dispatch 挂接）；
  axonhub 内省只在线路边界用本地假 HTTP 服务顶替，不 mock shim 内部函数。
- 原子写：直接测 admin_api.write_json_atomic。
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

# 让测试可 import shim 目录下的 app / admin_api（discover 从 shim/tests 启动）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---- 假 axonhub：只顶替内省线路边界（POST /admin/graphql）----
# token → me 映射驱动行为；未注册 token 一律 401（对齐 axonhub 拒绝语义）。
_FAKE_STATE = {
    "mode": "ok",   # ok=按 token 表应答；drop=无响应断连（模拟内省不可达）
    "tokens": {},   # token -> {"id":..,"isOwner":..,"scopes":[..]}；me 可为 None（内省通过但无身份）
}


class _FakeAxonhub(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默
        pass

    def do_POST(self):
        if self.path != "/admin/graphql":
            self.send_error(404)
            return
        if _FAKE_STATE["mode"] == "drop":
            self.connection.close()  # 无响应断连 → 客户端网络错误
            return
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        auth = self.headers.get("Authorization") or ""
        token = auth.removeprefix("Bearer ")
        me = _FAKE_STATE["tokens"].get(token)
        if me is None and token not in _FAKE_STATE["tokens"]:
            body, code = b'{"errors":[{"message":"unauthorized"}]}', 401
        else:
            body, code = json.dumps({"data": {"me": me}}).encode(), 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_server(handler_cls):
    """127.0.0.1:0 ephemeral 端口起真实 HTTP 服务（daemon 线程，测试进程退出即收）。"""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# 假 axonhub 先于被测模块导入启动：内省 URL 经环境变量注入（与容器默认 http://axonhub:8090 同机制）
_FAKE_AXONHUB = _start_server(_FakeAxonhub)
os.environ["AXONHUB_ADMIN_URL"] = f"http://127.0.0.1:{_FAKE_AXONHUB.server_address[1]}/admin/graphql"

import admin_api  # noqa: E402  须在环境变量注入后导入（模块级读 URL）
import app as shim_app  # noqa: E402  真实 dispatch（含 admin 挂接）

_SHIM = _start_server(shim_app.Handler)
_SHIM_BASE = f"http://127.0.0.1:{_SHIM.server_address[1]}"


def _request(method, path, token=None, payload=None, scheme="Bearer"):
    """对测试 shim 发请求；返回 (status, json body)。非 2xx 不抛异常。scheme 可换大小写变体。"""
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(_SHIM_BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"{scheme} {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _get(path, token=None, scheme="Bearer"):
    return _request("GET", path, token=token, scheme=scheme)


def _request_raw(method, path, token=None, data=b"", content_type="application/octet-stream"):
    """raw bytes 请求体（issue #48 文件直传）；返回 (status, json body)。"""
    req = urllib.request.Request(_SHIM_BASE + path, data=data, method=method)
    req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class AdminApiTest(unittest.TestCase):
    def setUp(self):
        # 每用例复位假 axonhub 行为
        _FAKE_STATE["mode"] = "ok"
        _FAKE_STATE["tokens"] = {}

    def test_healthz_no_auth(self):
        """GET /dlp-admin/healthz 无鉴权 → 200 {"status":"ok"}。"""
        status, body = _get("/dlp-admin/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})

    def test_ping_without_token_401(self):
        """GET /dlp-admin/ping 无 Authorization 头 → 401（不进内省）。"""
        status, _ = _get("/dlp-admin/ping")
        self.assertEqual(status, 401)

    def test_ping_bad_token_401(self):
        """内省拒绝（axonhub 非 200 / me 为空）→ 401。"""
        # token 未在假 axonhub 注册 → 假服务回 401 → shim 401
        status, _ = _get("/dlp-admin/ping", token="bogus-token")
        self.assertEqual(status, 401)
        # token 注册但 me 为 None（内省通过却无身份）→ 401
        _FAKE_STATE["tokens"]["null-me-token"] = None
        status, _ = _get("/dlp-admin/ping", token="null-me-token")
        self.assertEqual(status, 401)

    def test_ping_with_read_scope_200(self):
        """有 read_channels 系统 scope → 200，内省结果透传。"""
        _FAKE_STATE["tokens"]["reader-token"] = {
            "id": "42", "isOwner": False, "scopes": ["read_channels"],
        }
        status, body = _get("/dlp-admin/ping", token="reader-token")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"user_id": "42", "is_owner": False, "scopes": ["read_channels"]})

    def test_ping_missing_scope_403(self):
        """内省通过但缺 read_channels → 403。"""
        _FAKE_STATE["tokens"]["noscope-token"] = {
            "id": "43", "isOwner": False, "scopes": ["read_users"],
        }
        status, _ = _get("/dlp-admin/ping", token="noscope-token")
        self.assertEqual(status, 403)

    def test_ping_owner_pass(self):
        """isOwner=true 直通（无需任何 scope）→ 200。"""
        _FAKE_STATE["tokens"]["owner-token"] = {
            "id": "1", "isOwner": True, "scopes": [],
        }
        status, body = _get("/dlp-admin/ping", token="owner-token")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"user_id": "1", "is_owner": True, "scopes": []})

    def test_ping_introspection_down_503(self):
        """内省不可达（无响应断连）→ 503：admin 平面 fail-closed，不适用检测链 fail-open。"""
        _FAKE_STATE["mode"] = "drop"
        status, _ = _get("/dlp-admin/ping", token="reader-token")
        self.assertEqual(status, 503)

    def test_bearer_scheme_case_insensitive(self):
        """RFC 6750 scheme 大小写不敏感：小写 bearer + 合法 token → 200。"""
        _FAKE_STATE["tokens"]["reader-token"] = {
            "id": "42", "isOwner": False, "scopes": ["read_channels"],
        }
        status, body = _get("/dlp-admin/ping", token="reader-token", scheme="bearer")
        self.assertEqual(status, 200)
        self.assertEqual(body["user_id"], "42")

    def test_unknown_path_gated_by_auth(self):
        """未知 admin 路径（healthz 除外）先过鉴权再 404：无凭据探测不泄露路由存在性。"""
        # 无 token 探未知路径 → 401（而非 404）
        status, _ = _get("/dlp-admin/nope")
        self.assertEqual(status, 401)
        # 内省通过但缺 scope → 403（鉴权判定照旧，先于路由查找）
        _FAKE_STATE["tokens"]["noscope-token"] = {
            "id": "43", "isOwner": False, "scopes": ["read_users"],
        }
        status, _ = _get("/dlp-admin/nope", token="noscope-token")
        self.assertEqual(status, 403)
        # 合法 token（read scope）探未知路径 → 404
        _FAKE_STATE["tokens"]["reader-token"] = {
            "id": "42", "isOwner": False, "scopes": ["read_channels"],
        }
        status, _ = _get("/dlp-admin/nope", token="reader-token")
        self.assertEqual(status, 404)


# CRUD fixture：单个 recognizer 样例（对齐 recognizers/pii-zh.json 形状）
_REC_FIXTURE = {
    "name": "zh_phone",
    "entity": "ZH_PHONE",
    "patterns": [{"name": "zh_phone_v1", "regex": r"(?<!\d)1[3-9]\d{9}(?!\d)", "score": 0.7}],
    "context": ["手机"],
    "replacement": "【PII:手机号】",
}


class AdminCrudTest(unittest.TestCase):
    """配置面 CRUD（issue #32）：wordlist / recognizers 读写。
    fixture：每用例临时词表/recognizer 文件，覆写 admin_api 模块级路径（brief 钦定方式）。"""

    def setUp(self):
        _FAKE_STATE["mode"] = "ok"
        _FAKE_STATE["tokens"] = {
            "reader-token": {"id": "42", "isOwner": False, "scopes": ["read_channels"]},
            "writer-token": {"id": "7", "isOwner": False, "scopes": ["read_channels", "write_channels"]},
        }
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.wordlist_path = os.path.join(d, "confidential-terms.json")
        self.recognizers_path = os.path.join(d, "pii-zh.json")
        self._wordlist_fixture = {
            "version": 1,
            "_comment": "CRUD fixture",
            "terms": [{"value": "凤凰计划", "rule_id": "confidential.codename"}],
        }
        self._recs_fixture = {"version": 1, "_comment": "CRUD fixture", "recognizers": [_REC_FIXTURE]}
        with open(self.wordlist_path, "w", encoding="utf-8") as f:
            json.dump(self._wordlist_fixture, f, ensure_ascii=False)
        with open(self.recognizers_path, "w", encoding="utf-8") as f:
            json.dump(self._recs_fixture, f, ensure_ascii=False)
        self._saved_paths = (admin_api.WORDLIST_PATH, admin_api.PII_RECOGNIZERS_PATH)
        admin_api.WORDLIST_PATH = self.wordlist_path
        admin_api.PII_RECOGNIZERS_PATH = self.recognizers_path

    def tearDown(self):
        admin_api.WORDLIST_PATH, admin_api.PII_RECOGNIZERS_PATH = self._saved_paths
        self._tmp.cleanup()

    def _read_json(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_put_wordlist_dup_error_no_value_echo(self):
        """重复词 400 只报下标与 rule_id，不回显词值本身（词值可能是敏感词，code-review 修复）。"""
        payload = {"terms": [
            {"value": "绝密词值ABC", "rule_id": "confidential.a"},
            {"value": "x", "rule_id": "confidential.b"},
            {"value": "绝密词值ABC", "rule_id": "confidential.c"},
        ]}
        status, body = _request("PUT", "/dlp-admin/wordlist", token="writer-token", payload=payload)
        self.assertEqual(status, 400)
        self.assertIn("terms[2].value 重复", body["error"])
        self.assertIn("rule_id=confidential.c", body["error"])
        self.assertNotIn("绝密词值ABC", body["error"])

    def test_put_wordlist_read_scope_403(self):
        """写端点需 write_channels：仅 read scope 的 token 调 PUT → 403。"""
        status, body = _request("PUT", "/dlp-admin/wordlist", token="reader-token",
                                payload={"terms": []})
        self.assertEqual(status, 403)
        self.assertIn("write_channels", body.get("error", ""))

    def test_get_recognizers(self):
        """GET /dlp-admin/recognizers（读级）→ 200 返回 pii-zh.json 全文。"""
        status, body = _get("/dlp-admin/recognizers", token="reader-token")
        self.assertEqual(status, 200)
        self.assertEqual(body, self._recs_fixture)

    def test_post_recognizer(self):
        """POST /dlp-admin/recognizers（写级）新增一个 recognizer；context 缺省默认 []。"""
        new_rec = {
            "name": "zh_passport",
            "entity": "ZH_PASSPORT",
            "patterns": [{"name": "zh_passport_v1", "regex": r"E\d{8}", "score": 0.5}],
            "replacement": "【PII:护照号】",
        }
        status, _ = _request("POST", "/dlp-admin/recognizers", token="writer-token", payload=new_rec)
        self.assertEqual(status, 200)
        on_disk = self._read_json(self.recognizers_path)
        self.assertEqual(len(on_disk["recognizers"]), 2)
        stored = on_disk["recognizers"][1]
        self.assertEqual(stored["name"], "zh_passport")
        self.assertEqual(stored["context"], [])  # 缺省默认
        self.assertEqual(on_disk["recognizers"][0], _REC_FIXTURE)  # 原有不动

    def test_post_recognizer_name_400(self):
        """POST name 校验：与现有重复 → 400；name 缺失/空 → 400。"""
        dup = dict(_REC_FIXTURE)  # name=zh_phone 已存在
        status, body = _request("POST", "/dlp-admin/recognizers", token="writer-token", payload=dup)
        self.assertEqual(status, 400)
        self.assertIn("zh_phone", body.get("error", ""))
        no_name = {k: v for k, v in _REC_FIXTURE.items() if k != "name"}
        status, _ = _request("POST", "/dlp-admin/recognizers", token="writer-token", payload=no_name)
        self.assertEqual(status, 400)
        # 均未落盘
        self.assertEqual(len(self._read_json(self.recognizers_path)["recognizers"]), 1)

    def test_recognizer_context_validation(self):
        """context 提供时必须是字符串数组，否则 400；不提供仍默认 []（brief 定案保留）。"""
        base = {"name": "ctx_rec", "entity": "CTX",
                "patterns": [{"name": "p", "regex": r"\d", "score": 0.5}], "replacement": "x"}
        for name, ctx in [("非数组", 123), ("含非字符串", ["电话", 1])]:
            with self.subTest(case=name):
                status, body = _request("POST", "/dlp-admin/recognizers", token="writer-token",
                                        payload=dict(base, context=ctx))
                self.assertEqual(status, 400)
                self.assertIn("context 必须为字符串数组", body.get("error", ""))
        # 合法字符串数组 → 200 且原样落盘
        status, _ = _request("POST", "/dlp-admin/recognizers", token="writer-token",
                             payload=dict(base, context=["电话"]))
        self.assertEqual(status, 200)
        stored = self._read_json(self.recognizers_path)["recognizers"][1]
        self.assertEqual(stored["context"], ["电话"])
        # PUT 同校验
        status, _ = _request("PUT", "/dlp-admin/recognizers/zh_phone", token="writer-token",
                             payload=dict(base, context="x"))
        self.assertEqual(status, 400)

    def test_post_recognizer_invalid_fields_400(self):
        """POST 字段校验逐条：regex 必须过 re.compile（400 带 regex 错误信息）、score 0~1、entity/replacement 非空。"""
        base = {
            "name": "zh_new",
            "entity": "ZH_NEW",
            "patterns": [{"name": "p1", "regex": r"\d+", "score": 0.5}],
            "replacement": "【PII:测试】",
        }

        def mutated(**kw):
            import copy
            rec = copy.deepcopy(base)
            for k, v in kw.items():
                if v is ...:  # 哨兵：删除该键
                    rec.pop(k, None)
                else:
                    rec[k] = v
            return rec

        cases = {
            "regex 编译失败": (mutated(patterns=[{"name": "p1", "regex": r"(\d", "score": 0.5}]), "regex"),
            "regex 空": (mutated(patterns=[{"name": "p1", "regex": "", "score": 0.5}]), "regex"),
            "pattern name 空": (mutated(patterns=[{"name": "", "regex": r"\d+", "score": 0.5}]), "name"),
            "score 超界": (mutated(patterns=[{"name": "p1", "regex": r"\d+", "score": 1.5}]), "score"),
            "score 非数值": (mutated(patterns=[{"name": "p1", "regex": r"\d+", "score": "0.5"}]), "score"),
            "patterns 空数组": (mutated(patterns=[]), "patterns"),
            "entity 缺失": (mutated(entity=...), "entity"),
            "replacement 空": (mutated(replacement=""), "replacement"),
        }
        for name, (payload, key) in cases.items():
            with self.subTest(case=name):
                status, body = _request("POST", "/dlp-admin/recognizers", token="writer-token", payload=payload)
                self.assertEqual(status, 400)
                self.assertIn(key, body.get("error", ""))  # 错误信息指向具体字段
        # regex 编译失败的错误信息须带 re 的具体原因
        status, body = _request("POST", "/dlp-admin/recognizers", token="writer-token",
                                payload=cases["regex 编译失败"][0])
        self.assertIn("missing", body["error"])  # re.error: missing ), unterminated subpattern
        # 均未落盘
        self.assertEqual(len(self._read_json(self.recognizers_path)["recognizers"]), 1)

    def test_put_recognizer_item(self):
        """PUT /dlp-admin/recognizers/<name>（写级）替换指定项：同 POST 字段校验，name 以 URL 为准；不存在 → 404。"""
        replacement = {
            "name": "ignored_body_name",  # URL 优先：落盘 name 仍为 zh_phone
            "entity": "ZH_MOBILE",
            "patterns": [{"name": "v2", "regex": r"1[3-9]\d{9}", "score": 0.9}],
            "replacement": "【PII:手机】",
        }
        status, _ = _request("PUT", "/dlp-admin/recognizers/zh_phone", token="writer-token",
                             payload=replacement)
        self.assertEqual(status, 200)
        stored = self._read_json(self.recognizers_path)["recognizers"][0]
        self.assertEqual(stored["name"], "zh_phone")  # name 以 URL 为准
        self.assertEqual(stored["entity"], "ZH_MOBILE")
        self.assertEqual(stored["context"], [])  # 缺省默认
        # 不存在的 name → 404
        status, _ = _request("PUT", "/dlp-admin/recognizers/zh_nope", token="writer-token",
                             payload=replacement)
        self.assertEqual(status, 404)
        # 非法字段 → 400（校验同 POST）
        bad = dict(replacement, patterns=[{"name": "p", "regex": "(", "score": 0.5}])
        status, _ = _request("PUT", "/dlp-admin/recognizers/zh_phone", token="writer-token", payload=bad)
        self.assertEqual(status, 400)

    def test_delete_recognizer_item(self):
        """DELETE /dlp-admin/recognizers/<name>（写级）：删除成功 → 200；删空数组允许；不存在 → 404。"""
        status, _ = _request("DELETE", "/dlp-admin/recognizers/zh_phone", token="writer-token")
        self.assertEqual(status, 200)
        self.assertEqual(self._read_json(self.recognizers_path)["recognizers"], [])  # 删空允许
        # 再删 → 404
        status, _ = _request("DELETE", "/dlp-admin/recognizers/zh_phone", token="writer-token")
        self.assertEqual(status, 404)

    def test_put_wordlist_corrupt_file_500(self):
        """fail-closed（code-review 修复）：文件存在但损坏 → 500 拒绝写入且不落盘；删除后允许从空壳新建。"""
        bad = b'{"version": 1, broken'
        with open(self.wordlist_path, "wb") as f:
            f.write(bad)
        status, body = _request("PUT", "/dlp-admin/wordlist", token="writer-token",
                                payload={"terms": [{"value": "v", "rule_id": "r"}]})
        self.assertEqual(status, 500)
        self.assertIn("拒绝写入", body.get("error", ""))
        with open(self.wordlist_path, "rb") as f:
            self.assertEqual(f.read(), bad)  # 磁盘原文未动
        # 文件不存在 → 允许从空壳新建
        os.unlink(self.wordlist_path)
        status, _ = _request("PUT", "/dlp-admin/wordlist", token="writer-token",
                             payload={"terms": [{"value": "v", "rule_id": "r"}]})
        self.assertEqual(status, 200)
        created = self._read_json(self.wordlist_path)
        self.assertEqual(created["version"], 1)
        self.assertEqual(created["terms"], [{"value": "v", "rule_id": "r"}])

    def test_recognizers_corrupt_file_500(self):
        """recognizers 三端点同样 fail-closed：文件损坏 → 500 不落盘；文件不存在 → POST 允许新建。"""
        bad = b'{"recognizers": [broken'
        with open(self.recognizers_path, "wb") as f:
            f.write(bad)
        rec = {"name": "n", "entity": "E",
               "patterns": [{"name": "p", "regex": r"\d", "score": 0.5}], "replacement": "x"}
        for method, path, payload in [
            ("POST", "/dlp-admin/recognizers", rec),
            ("PUT", "/dlp-admin/recognizers/zh_phone", rec),
            ("DELETE", "/dlp-admin/recognizers/zh_phone", None),
        ]:
            with self.subTest(method=method):
                status, body = _request(method, path, token="writer-token", payload=payload)
                self.assertEqual(status, 500)
                self.assertIn("拒绝写入", body.get("error", ""))
        with open(self.recognizers_path, "rb") as f:
            self.assertEqual(f.read(), bad)  # 三次均未落盘
        os.unlink(self.recognizers_path)
        status, _ = _request("POST", "/dlp-admin/recognizers", token="writer-token", payload=rec)
        self.assertEqual(status, 200)  # 不存在 → 新建
        self.assertEqual(len(self._read_json(self.recognizers_path)["recognizers"]), 1)

    def test_get_wordlist(self):
        """GET /dlp-admin/wordlist（读级）→ 200 返回词表 JSON 全文。"""
        status, body = _get("/dlp-admin/wordlist", token="reader-token")
        self.assertEqual(status, 200)
        self.assertEqual(body, self._wordlist_fixture)

    def test_put_wordlist_replace(self):
        """PUT /dlp-admin/wordlist（写级）整体替换 terms；保留原 version/_comment。"""
        new_terms = [
            {"value": "蓝鲸系统", "rule_id": "confidential.codename"},
            {"value": "新代号X", "rule_id": "confidential.test"},
        ]
        status, body = _request("PUT", "/dlp-admin/wordlist", token="writer-token",
                                payload={"terms": new_terms})
        self.assertEqual(status, 200)
        on_disk = self._read_json(self.wordlist_path)
        self.assertEqual(on_disk["terms"], new_terms)
        self.assertEqual(on_disk["version"], 1)  # 原字段保留
        self.assertEqual(on_disk["_comment"], "CRUD fixture")
        self.assertEqual(body["terms"], new_terms)

    def test_put_wordlist_invalid_400(self):
        """PUT 非法 terms 逐条 400（带具体原因）；空数组合法（允许清空）。"""
        cases = {
            "terms 缺失": {},
            "terms 非数组": {"terms": "x"},
            "项非对象": {"terms": ["x"]},
            "value 空串": {"terms": [{"value": "", "rule_id": "r"}]},
            "value 非字符串": {"terms": [{"value": 1, "rule_id": "r"}]},
            "rule_id 空": {"terms": [{"value": "v", "rule_id": ""}]},
            "rule_id 缺": {"terms": [{"value": "v"}]},
            "value 重复": {"terms": [{"value": "v", "rule_id": "r1"}, {"value": "v", "rule_id": "r2"}]},
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                status, body = _request("PUT", "/dlp-admin/wordlist", token="writer-token", payload=payload)
                self.assertEqual(status, 400)
                self.assertTrue(body.get("error"))  # 带具体原因
        # 非法写不落盘：文件仍是 fixture 原样
        self.assertEqual(self._read_json(self.wordlist_path), self._wordlist_fixture)
        # 空数组合法（允许清空词表）
        status, _ = _request("PUT", "/dlp-admin/wordlist", token="writer-token", payload={"terms": []})
        self.assertEqual(status, 200)
        self.assertEqual(self._read_json(self.wordlist_path)["terms"], [])


# format-rules fixture（issue #33）：一 reject 一 mask 小样本 + 带标记的 config.yaml 样例
_FR_FIXTURE = {
    "version": 1,
    "_comment": "format-rules fixture",
    "rules": [
        {"code": "secrets.test_a", "layer": "L1", "action": "reject", "enabled": True,
         "message": "test A key detected",
         "gateway_patterns": ["TESTA[0-9]{4}"], "shim_patterns": ["TESTA[0-9]{4}"]},
        {"code": "pii.test_b", "layer": "L1.5", "action": "mask", "enabled": True,
         "entity": "ZH_TEST", "replacement": "【PII:测试】",
         "gateway_patterns": ["\\bTESTB[0-9]{4}\\b"], "shim_patterns": ["(?<!\\d)TESTB[0-9]{4}(?!\\d)"]},
    ],
}

_CONFIG_FIXTURE = (
    "routes:\n"
    "  - gateways: [default]\n"
    "    policies:\n"
    "      ai:\n"
    "        promptGuard:\n"
    "          request:\n"
    "            # >>> DLP-FORMAT-RULES BEGIN（format-rules.json 渲染，勿手改） >>>\n"
    "            # <<< DLP-FORMAT-RULES END <<<\n"
    "            - webhook:\n"
    "                target:\n"
    "                  host: shim:8080\n"
)


class AdminFormatRulesTest(unittest.TestCase):
    """L1/L1.5 格式规则统一源（issue #33）：format-rules CRUD + gateway YAML 渲染。
    fixture：每用例临时 format-rules.json / config.yaml，覆写 admin_api 模块级路径。"""

    def setUp(self):
        _FAKE_STATE["mode"] = "ok"
        _FAKE_STATE["tokens"] = {
            "reader-token": {"id": "42", "isOwner": False, "scopes": ["read_channels"]},
            "writer-token": {"id": "7", "isOwner": False, "scopes": ["read_channels", "write_channels"]},
        }
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.rules_path = os.path.join(d, "format-rules.json")
        self.config_path = os.path.join(d, "config.yaml")
        with open(self.rules_path, "w", encoding="utf-8") as f:
            json.dump(_FR_FIXTURE, f, ensure_ascii=False)
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(_CONFIG_FIXTURE)
        self._saved_paths = (admin_api.FORMAT_RULES_PATH, admin_api.AGENTGW_CONFIG_PATH)
        admin_api.FORMAT_RULES_PATH = self.rules_path
        admin_api.AGENTGW_CONFIG_PATH = self.config_path
        # env 隔离（对齐 AppSettingsTest 纪律）：开发机/CI 导出分层总开关 env 会改变渲染行为
        # （本类 SETTINGS_PATH 未覆写，_layer_enabled 落不到文件时读 env），pop 防误失败
        self._saved_env = {k: os.environ.pop(k, None)
                           for k in ("L1_ENABLED", "L2_ENABLED", "RESPONSE_ENABLED")}

    def tearDown(self):
        admin_api.FORMAT_RULES_PATH, admin_api.AGENTGW_CONFIG_PATH = self._saved_paths
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
        self._tmp.cleanup()

    def _read_json(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_get_format_rules(self):
        """GET /dlp-admin/format-rules（读级）→ 200 返回 JSON 全文。"""
        status, body = _get("/dlp-admin/format-rules", token="reader-token")
        self.assertEqual(status, 200)
        self.assertEqual(body, _FR_FIXTURE)

    def test_render_block_matches_current_gateway(self):
        """渲染等价性（issue #33 核心）：首版 JSON 渲染文本含现网每条 pattern 与 rejection body。
        期望值硬编码自 config.yaml 现网 promptGuard request 段（迁移裁判）。"""
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(repo_root, "deploy", "dlp", "format-rules.json"), encoding="utf-8") as f:
            rules = json.load(f)["rules"]
        block = admin_api.render_gateway_block(rules)
        # 现网每条 gateway pattern（逐条核自 config.yaml，不得 drift）
        expected_patterns = [
            r"sk-ant-[A-Za-z0-9_\-]{20,}",
            r"sk-(proj-)?[A-Za-z0-9_\-]{20,}",
            r"gh[pousr]_[A-Za-z0-9]{20,}",
            r"github_pat_[A-Za-z0-9_]{20,}",
            r"AKIA[0-9A-Z]{16}",
            r"ASIA[0-9A-Z]{16}",
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----",
            r"LTAI[A-Za-z0-9]{12,}",
            r"\b1[3-9]\d{9}\b",
            r"\b\d{17}[\dXx]\b",
        ]
        for p in expected_patterns:
            with self.subTest(pattern=p):
                self.assertIn(f"- pattern: '{p}'", block)
        # 现网每条 rejection body（紧凑 JSON，逐条核自 config.yaml）
        expected_bodies = [
            ('secrets.anthropic_sk', "Anthropic secret key detected"),
            ('secrets.openai_sk', "OpenAI secret key detected"),
            ('secrets.github_token', "GitHub token detected"),
            ('secrets.aws_key', "AWS access key detected"),
            ('secrets.private_key', "private key material detected"),
            ('secrets.aliyun_ak', "Aliyun access key detected"),
        ]
        for code, message in expected_bodies:
            with self.subTest(code=code):
                body = ('{"error":{"message":"Blocked by ai4s DLP: ' + message +
                        '","type":"content_policy_violation","code":"' + code + '"}}')
                self.assertIn(body, block)
        # 结构断言：6 条 rejection（mask 无）；顺序敏感（anthropic 在 openai 前）；shim-only 不渲染
        self.assertEqual(block.count("rejection:"), 6)
        self.assertLess(block.index("secrets.anthropic_sk"), block.index("secrets.openai_sk"))
        self.assertNotIn("bank_card", block)

    def test_splice_marker_block(self):
        """splice：替换 BEGIN/END 标记间内容（标记行与区块外保留）；标记缺失 → ValueError（→500）。"""
        block = "            - regex:\n                action: mask\n"
        out = admin_api.splice_rendered(_CONFIG_FIXTURE, block)
        # 标记行保留、渲染内容进入、区块外不动
        self.assertIn("DLP-FORMAT-RULES BEGIN", out)
        self.assertIn("DLP-FORMAT-RULES END", out)
        self.assertIn(block, out)
        self.assertTrue(out.startswith("routes:\n"))
        self.assertTrue(out.endswith("host: shim:8080\n"))
        # 标记缺失 → ValueError（端点转 500）
        with self.assertRaises(ValueError):
            admin_api.splice_rendered("routes: []\n", block)
        # END 在 BEGIN 前 → ValueError
        bad = "            # <<< DLP-FORMAT-RULES END <<<\n            # >>> DLP-FORMAT-RULES BEGIN >>>\n"
        with self.assertRaises(ValueError):
            admin_api.splice_rendered(bad, block)

    def test_put_format_rules_full_chain(self):
        """PUT 全链路：校验过 → JSON 落盘 → config.yaml 标记区块渲染替换 → 200。"""
        import copy
        new_doc = copy.deepcopy(_FR_FIXTURE)
        new_doc["rules"].append({
            "code": "secrets.new_c", "layer": "L1", "action": "reject", "enabled": True,
            "message": "new C key", "gateway_patterns": ["NEWC[0-9]{4}"],
            "shim_patterns": ["NEWC[0-9]{4}"]})
        status, _ = _request("PUT", "/dlp-admin/format-rules", token="writer-token", payload=new_doc)
        self.assertEqual(status, 200)
        self.assertEqual(self._read_json(self.rules_path), new_doc)  # JSON 整体替换
        with open(self.config_path, encoding="utf-8") as f:
            cfg = f.read()
        # 区块内渲染了新规则（含 rejection body），原有规则也在；标记与区块外不动
        self.assertIn("- pattern: 'NEWC[0-9]{4}'", cfg)
        self.assertIn('"code":"secrets.new_c"', cfg)
        self.assertIn("- pattern: 'TESTA[0-9]{4}'", cfg)
        self.assertIn("DLP-FORMAT-RULES BEGIN", cfg)
        self.assertIn("DLP-FORMAT-RULES END", cfg)
        self.assertIn("host: shim:8080", cfg)

    def test_put_format_rules_missing_markers_500(self):
        """config.yaml 缺标记 → 500（提示标记）且 JSON/config 均未落盘。"""
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write("routes: []\n")
        status, body = _request("PUT", "/dlp-admin/format-rules", token="writer-token", payload=_FR_FIXTURE)
        self.assertEqual(status, 500)
        self.assertIn("标记", body.get("error", ""))
        self.assertEqual(self._read_json(self.rules_path), _FR_FIXTURE)  # JSON 未动
        with open(self.config_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "routes: []\n")

    def test_put_format_rules_yaml_write_failure_rollback(self):
        """config.yaml 写失败 → 500 且 JSON 回滚（.bak 恢复，防 JSON/YAML 双份漂移）。"""
        # config 所在目录只读：文件可读但 tmp 创建必失败（PermissionError），读路径不受影响
        import copy
        ro_dir = os.path.join(self._tmp.name, "ro")
        os.mkdir(ro_dir)
        admin_api.AGENTGW_CONFIG_PATH = os.path.join(ro_dir, "config.yaml")
        with open(admin_api.AGENTGW_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(_CONFIG_FIXTURE)
        os.chmod(ro_dir, 0o555)
        try:
            new_doc = copy.deepcopy(_FR_FIXTURE)
            new_doc["rules"] = new_doc["rules"][:1]
            status, body = _request("PUT", "/dlp-admin/format-rules", token="writer-token", payload=new_doc)
        finally:
            os.chmod(ro_dir, 0o755)  # 恢复权限，否则 tearDown 清理不掉
        self.assertEqual(status, 500)
        self.assertIn("已回滚", body.get("error", ""))
        self.assertEqual(self._read_json(self.rules_path), _FR_FIXTURE)  # JSON 回滚为写前内容

    def test_post_render_restores_drift_and_idempotent(self):
        """POST render（issue #33）：按 JSON 重渲染 config.yaml 标记区块——漂移修复 + 幂等。"""
        import copy
        new_doc = copy.deepcopy(_FR_FIXTURE)
        new_doc["rules"].append({
            "code": "secrets.new_c", "layer": "L1", "action": "reject", "enabled": True,
            "message": "new C key", "gateway_patterns": ["NEWC[0-9]{4}"],
            "shim_patterns": ["NEWC[0-9]{4}"]})
        status, _ = _request("PUT", "/dlp-admin/format-rules", token="writer-token", payload=new_doc)
        self.assertEqual(status, 200)
        with open(self.config_path, encoding="utf-8") as f:
            baseline = f.read()
        # 模拟手改漂移：删掉区块里一条渲染条目行
        drifted = baseline.replace("                  - pattern: 'NEWC[0-9]{4}'\n", "")
        self.assertNotEqual(drifted, baseline)
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(drifted)
        # POST render → 漂移修复（返回渲染进网关的条目数）
        status, body = _request("POST", "/dlp-admin/format-rules/render", token="writer-token")
        self.assertEqual(status, 200)
        self.assertEqual(body.get("rendered"), 3)
        with open(self.config_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), baseline)
        # 再调一次：字节相同（幂等）
        status, _ = _request("POST", "/dlp-admin/format-rules/render", token="writer-token")
        self.assertEqual(status, 200)
        with open(self.config_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), baseline)

    def test_post_render_bad_json_500(self):
        """POST render：format-rules.json 损坏（非法 JSON）→ 500 拒绝渲染，config 不动。"""
        with open(self.rules_path, "w", encoding="utf-8") as f:
            f.write("{not json")
        with open(self.config_path, encoding="utf-8") as f:
            before = f.read()
        status, _ = _request("POST", "/dlp-admin/format-rules/render", token="writer-token")
        self.assertEqual(status, 500)
        with open(self.config_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_put_format_rules_invalid_400(self):
        """PUT 校验逐条 400：schema 必填、action/layer 枚举、regex 编译、gateway_patterns 禁 Rust 不支持构造。"""
        import copy
        base_rule = {"code": "secrets.ok", "layer": "L1", "action": "reject", "enabled": True,
                     "message": "m", "gateway_patterns": [r"OK[0-9]{4}"], "shim_patterns": []}

        def doc(mutate):
            rule = copy.deepcopy(base_rule)
            mutate(rule)
            return {"version": 1, "rules": [rule]}

        cases = {
            "非对象": [1],
            "rules 缺失": {"version": 1},
            "rule 非对象": {"rules": ["x"]},
            "code 缺失": doc(lambda r: r.pop("code")),
            "code 重复": {"rules": [copy.deepcopy(base_rule), copy.deepcopy(base_rule)]},
            "layer 非法": doc(lambda r: r.update(layer="L3")),
            "action 非法": doc(lambda r: r.update(action="block")),
            "enabled 缺失": doc(lambda r: r.pop("enabled")),
            "enabled 非布尔": doc(lambda r: r.update(enabled=1)),
            "reject 缺 message": doc(lambda r: r.pop("message")),
            "gateway_patterns 非数组": doc(lambda r: r.update(gateway_patterns="x")),
            "gateway regex 编译失败": doc(lambda r: r.update(gateway_patterns=["("])),
            "gateway lookbehind": doc(lambda r: r.update(gateway_patterns=[r"(?<=OK)[0-9]+"])),
            "gateway lookahead": doc(lambda r: r.update(gateway_patterns=[r"OK(?=X)"])),
            "gateway backreference": doc(lambda r: r.update(gateway_patterns=[r"(OK)\1"])),
            "shim regex 编译失败": doc(lambda r: r.update(shim_patterns=["("])),
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                status, body = _request("PUT", "/dlp-admin/format-rules", token="writer-token", payload=payload)
                self.assertEqual(status, 400)
                self.assertTrue(body.get("error"))  # 带具体原因
        # 非法写双文件均未动
        self.assertEqual(self._read_json(self.rules_path), _FR_FIXTURE)
        with open(self.config_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), _CONFIG_FIXTURE)

    def test_put_format_rules_json_write_failure_500(self):
        """JSON 写失败（review #2）→ 500 带 error 且 config.yaml 未被触碰（与 YAML 写失败处理对称）。"""
        with mock.patch.object(admin_api, "write_json_atomic", side_effect=OSError("disk full")):
            status, body = _request("PUT", "/dlp-admin/format-rules", token="writer-token", payload=_FR_FIXTURE)
        self.assertEqual(status, 500)
        self.assertTrue(body.get("error"))
        with open(self.config_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), _CONFIG_FIXTURE)  # config.yaml 未被触碰

    def test_put_format_rules_single_quote_pattern_200(self):
        """pattern 含单引号（review #3）：YAML 渲染单引号翻倍，渲染后校验不得误判未落文本。"""
        import copy
        new_doc = copy.deepcopy(_FR_FIXTURE)
        new_doc["rules"].append({
            "code": "secrets.quote", "layer": "L1", "action": "reject", "enabled": True,
            "message": "quote key", "gateway_patterns": ["foo'bar[0-9]{3}"],
            "shim_patterns": ["foo'bar[0-9]{3}"]})
        status, body = _request("PUT", "/dlp-admin/format-rules", token="writer-token", payload=new_doc)
        self.assertEqual(status, 200, body)
        with open(self.config_path, encoding="utf-8") as f:
            cfg = f.read()
        self.assertIn("- pattern: 'foo''bar[0-9]{3}'", cfg)  # YAML 单引号标量内 ' 翻倍
        self.assertIn('"code":"secrets.quote"', cfg)


class WriteJsonAtomicTest(unittest.TestCase):
    """原子写工具（seam 2）：写 JSON 配置的落盘完整性。"""

    def test_roundtrip(self):
        """写后读回一致；风格对齐词表文件（ensure_ascii=False、indent=2、尾部换行）。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "confidential-terms.json")
            obj = {"version": 2, "terms": [{"value": "凤凰计划", "rule_id": "confidential.codename"}]}
            admin_api.write_json_atomic(path, obj)
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            self.assertEqual(json.loads(raw), obj)
            self.assertIn("凤凰计划", raw)  # ensure_ascii=False：中文不转义
            self.assertTrue(raw.startswith("{\n  "))  # indent=2
            self.assertTrue(raw.endswith("}\n"))  # 尾部换行

    def test_replace_failure_keeps_old_file(self):
        """os.replace 抛错时旧文件完整、tmp 无残留。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fingerprints.json")
            old = {"version": 1, "docs": {"旧文档": ["aa"]}}
            admin_api.write_json_atomic(path, old)
            with mock.patch("os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    admin_api.write_json_atomic(path, {"version": 2, "docs": {}})
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), old)  # 旧文件未被破坏
            # 备份在 replace 前已落盘：.bak 与旧文件同值（可人工回滚），tmp 已清理
            with open(path + ".bak", encoding="utf-8") as f:
                self.assertEqual(json.load(f), old)
            self.assertEqual(sorted(os.listdir(d)), ["fingerprints.json", "fingerprints.json.bak"])

    def test_second_write_rolls_backup(self):
        """.bak 滚动备份（issue #31 spec）：连续写两次后，<path>.bak 是第一次的值。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "confidential-terms.json")
            v1 = {"version": 1, "terms": [{"value": "凤凰计划", "rule_id": "r1"}]}
            v2 = {"version": 2, "terms": [{"value": "蓝鲸系统", "rule_id": "r2"}]}
            admin_api.write_json_atomic(path, v1)
            admin_api.write_json_atomic(path, v2)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), v2)  # 新值落盘
            with open(path + ".bak", encoding="utf-8") as f:
                self.assertEqual(json.load(f), v1)  # .bak 保留上一版

    def test_no_backup_when_target_absent(self):
        """目标文件不存在时跳过备份：无 .bak 产生且不报错。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "new.json")
            admin_api.write_json_atomic(path, {"version": 1})
            self.assertEqual(os.listdir(d), ["new.json"])

    def test_concurrent_writes_valid_json(self):
        """多线程并发写同一文件：结果总是完整合法 JSON，无半写/截断，tmp 无残留。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "concurrent.json")
            payloads = [
                {"writer": i, "seq": j, "pad": "凤凰" * 200}
                for i in range(8)
                for j in range(25)
            ]
            errors = []

            def writer(own):
                try:
                    for p in own:
                        admin_api.write_json_atomic(path, p)
                except Exception as e:  # 收集线程内异常，主线程断言
                    errors.append(e)

            threads = [
                threading.Thread(target=writer, args=(payloads[i * 25:(i + 1) * 25],))
                for i in range(8)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            with open(path, encoding="utf-8") as f:
                final = json.load(f)  # 合法 JSON 即证明无半写（截断会解析失败）
            self.assertIn(final, payloads)
            self.assertFalse([f for f in os.listdir(d) if f.endswith(".tmp")])  # tmp 无残留


class FormatRulesLoaderTest(unittest.TestCase):
    """app.load_format_rules / 归一化检测规则装载（issue #33 review）。"""

    def setUp(self):
        self._saved = shim_app.FORMAT_RULES_PATH
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        shim_app.FORMAT_RULES_PATH = self._saved
        self._tmp.cleanup()

    def test_missing_file_warns_and_returns_empty(self):
        """文件缺失（review #1）：fail-open 返回 [] 且 stdout 留 warn（路径 + 错误类型，不含敏感值）。"""
        import contextlib
        import io
        shim_app.FORMAT_RULES_PATH = os.path.join(self._tmp.name, "nope.json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rules = shim_app.load_format_rules()
        self.assertEqual(rules, [])
        out = buf.getvalue()
        self.assertIn("format-rules", out)
        self.assertIn("FileNotFoundError", out)

    def test_broken_json_warns_and_returns_empty(self):
        """JSON 损坏（review #1）：fail-open 返回 [] 且 stdout 留 warn。"""
        import contextlib
        import io
        p = os.path.join(self._tmp.name, "format-rules.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        shim_app.FORMAT_RULES_PATH = p
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rules = shim_app.load_format_rules()
        self.assertEqual(rules, [])
        out = buf.getvalue()
        self.assertIn("format-rules", out)
        self.assertIn("JSONDecodeError", out)

    def test_rule_without_shim_patterns_not_matched(self):
        """无 shim_patterns 的规则不参与归一化检测（review #5：删 gateway_patterns 静默回退，漏检面显式可见）。"""
        gw_only = [{"code": "secrets.gw_only", "enabled": True, "action": "reject",
                    "gateway_patterns": ["GWONLY[0-9]{4}"]}]  # 无 shim_patterns 键
        norm, _ = shim_app.normalize_hard("token GWONLY1234 here")
        self.assertEqual(shim_app.norm_secret_hits(norm, gw_only), [])
        gw_mask = [{"code": "pii.gw_only", "enabled": True, "action": "mask",
                    "entity": "ZH_X", "gateway_patterns": ["GWONLY[0-9]{4}"]}]
        self.assertEqual(shim_app.norm_pii_mask_in_text("GWONLY1234", gw_mask), ("GWONLY1234", []))
        # 显式空数组同样不参与（与缺省同语义）
        norm2, _ = shim_app.normalize_hard("TESTA1234")
        self.assertEqual(shim_app.norm_secret_hits(norm2, [{**gw_only[0], "shim_patterns": []}]), [])


# EDM fixture（issue #34）：三条目覆盖三种形态——带 added_at、无 added_at（旧文档）、旧格式纯 shingle 数组
_EDM_FP_FIXTURE = {
    "version": 1,
    "docs": {
        "alpha": {"shingles": ["aa", "bb"], "lines": ["cc"], "added_at": "2026-08-01T00:00:00Z"},
        "beta": {"shingles": ["dd"], "lines": []},
        "legacy": ["ee", "ff"],
    },
    "updated_at": "2026-08-01T00:00:00Z",
}


class AdminEdmCorpusTest(unittest.TestCase):
    """EDM 语料管理（issue #34）：corpus CRUD + 指纹库维护。
    fixture：每用例临时 fingerprints.json / corpus 目录，覆写 admin_api 模块级路径；不动真实库。"""

    def setUp(self):
        _FAKE_STATE["mode"] = "ok"
        _FAKE_STATE["tokens"] = {
            "reader-token": {"id": "42", "isOwner": False, "scopes": ["read_channels"]},
            "writer-token": {"id": "7", "isOwner": False, "scopes": ["read_channels", "write_channels"]},
        }
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.fp_path = os.path.join(d, "fingerprints.json")
        self.corpus_dir = os.path.join(d, "corpus")
        os.makedirs(self.corpus_dir)
        with open(self.fp_path, "w", encoding="utf-8") as f:
            json.dump(_EDM_FP_FIXTURE, f, ensure_ascii=False)
        self._saved = (admin_api.EDM_FP_PATH, admin_api.EDM_CORPUS_DIR)
        admin_api.EDM_FP_PATH = self.fp_path
        admin_api.EDM_CORPUS_DIR = self.corpus_dir

    def tearDown(self):
        admin_api.EDM_FP_PATH, admin_api.EDM_CORPUS_DIR = self._saved
        self._tmp.cleanup()

    def _read_fp(self):
        with open(self.fp_path, encoding="utf-8") as f:
            return json.load(f)

    def test_get_corpus_list(self):
        """GET /dlp-admin/edm/corpus（读级）→ 文档列表（名称/指纹数/入库时间；旧文档 added_at 为 null）。"""
        status, body = _get("/dlp-admin/edm/corpus", token="reader-token")
        self.assertEqual(status, 200)
        by_name = {d["name"]: d for d in body}
        self.assertEqual(set(by_name), {"alpha", "beta", "legacy"})
        self.assertEqual(by_name["alpha"], {"name": "alpha", "shingle_count": 2, "line_count": 1,
                                            "added_at": "2026-08-01T00:00:00Z"})
        self.assertEqual(by_name["beta"]["shingle_count"], 1)
        self.assertEqual(by_name["beta"]["line_count"], 0)
        self.assertIsNone(by_name["beta"]["added_at"])  # 旧文档缺字段 → null
        self.assertEqual(by_name["legacy"]["shingle_count"], 2)  # 旧格式纯数组兼容
        self.assertEqual(by_name["legacy"]["line_count"], 0)

    def test_post_corpus_invalid_400(self):
        """POST 校验逐条 400（issue #34）：name 空/非法字符/超长/重复；text 空/过短（归一化 <12 字符）。"""
        good = {"name": "newdoc", "text": "这是一段足够长的测试文档内容 abcdefghijklmnop"}
        cases = {
            "name 缺失": {"text": good["text"]},
            "name 空": {**good, "name": ""},
            "name 非法字符": {**good, "name": "a/b"},
            "name 中文": {**good, "name": "采购协议"},
            "name 超长": {**good, "name": "a" * 65},
            "name 重复": {**good, "name": "alpha"},
            "text 缺失": {"name": "newdoc"},
            "text 空": {**good, "text": "   "},
            "text 过短": {**good, "text": "short"},  # 归一化后 5 < 12，双通道均无有效指纹
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                status, body = _request("POST", "/dlp-admin/edm/corpus", token="writer-token", payload=payload)
                self.assertEqual(status, 400)
                self.assertTrue(body.get("error"))
        # 非法写：指纹库与 corpus 目录均未动
        self.assertEqual(self._read_fp(), _EDM_FP_FIXTURE)
        self.assertEqual(os.listdir(self.corpus_dir), [])
        # review #1：过短消息如实描述（行级通道无有效指纹 + 达不到命中阈值），不谎称"双通道均无"
        status, body = _request("POST", "/dlp-admin/edm/corpus", token="writer-token",
                                payload={"name": "shorty", "text": "short"})
        self.assertEqual(status, 400)
        self.assertIn("行级指纹", body.get("error", ""))
        self.assertNotIn("双通道均无", body.get("error", ""))

    def test_post_corpus_fingerprints_correct(self):
        """POST 指纹入库正确性（issue #34，tautology 警戒）：期望值=独立手工算出的 hash 字面量，不经 edm_lib。
        锚定归一化（大小写/空白折叠）、短文档单 shingle 分支、双通道、added_at 与 corpus 落盘。"""
        # 独立手工计算（python3 -c 'import hashlib; ...' 算出后硬编码）：
        h1 = "f39dac6cbaba535e2c207cd0cd8f154974223c848f727f98b3564cea569b41cf"  # sha256("abcdefghijklmnop")
        h2 = "7efe4fb36a6f29488d7c1f3aad313ea73358776a5b626c4266d74aaa5add9c6b"  # sha256("abc def ghijklmnop")
        status, body = _request("POST", "/dlp-admin/edm/corpus", token="writer-token",
                                payload={"name": "doc1", "text": "abcdefghijklmnop"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["shingle_count"], 1)
        self.assertEqual(body["line_count"], 1)
        self.assertTrue(body["added_at"])
        store = self._read_fp()
        self.assertEqual(store["docs"]["doc1"]["shingles"], [h1])
        self.assertEqual(store["docs"]["doc1"]["lines"], [h1])  # 短文档两通道同 hash
        self.assertTrue(store["docs"]["doc1"]["added_at"])
        with open(os.path.join(self.corpus_dir, "doc1.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "abcdefghijklmnop")  # corpus 原文落盘
        # 归一化锚：大小写折叠 + 连续空白折叠为单空格
        status, _ = _request("POST", "/dlp-admin/edm/corpus", token="writer-token",
                             payload={"name": "doc2", "text": "  ABC def  GHIjklmnop  "})
        self.assertEqual(status, 200)
        store = self._read_fp()
        self.assertEqual(store["docs"]["doc2"]["shingles"], [h2])
        self.assertEqual(store["docs"]["doc2"]["lines"], [h2])
        # 其他文档条目未动
        self.assertEqual(store["docs"]["alpha"], _EDM_FP_FIXTURE["docs"]["alpha"])

    def test_delete_corpus_item(self):
        """DELETE /dlp-admin/edm/corpus/<name>（issue #34）：删指纹条目 + 语料文件；
        不存在 → 404；corpus 文件缺失容忍（指纹库为权威列表），其他条目不动。"""
        with open(os.path.join(self.corpus_dir, "alpha.txt"), "w", encoding="utf-8") as f:
            f.write("corpus 原文")
        status, _ = _request("DELETE", "/dlp-admin/edm/corpus/alpha", token="writer-token")
        self.assertEqual(status, 200)
        store = self._read_fp()
        self.assertNotIn("alpha", store["docs"])
        self.assertIn("beta", store["docs"])  # 其他条目未动
        self.assertFalse(os.path.exists(os.path.join(self.corpus_dir, "alpha.txt")))
        # 再删 → 404
        status, body = _request("DELETE", "/dlp-admin/edm/corpus/alpha", token="writer-token")
        self.assertEqual(status, 404)
        self.assertTrue(body.get("error"))
        # beta 无 corpus 文件（缺失容忍）也删除成功
        status, _ = _request("DELETE", "/dlp-admin/edm/corpus/beta", token="writer-token")
        self.assertEqual(status, 200)
        self.assertNotIn("beta", self._read_fp()["docs"])

    def test_delete_corpus_invalid_name_400(self):
        """DELETE name 正则校验（review #4 双保险）：非法字符 → 400（与 POST 同款），指纹库不动。"""
        for bad in ("bad!name", "bad%20name", "a" * 65):
            with self.subTest(name=bad):
                status, body = _request("DELETE", f"/dlp-admin/edm/corpus/{bad}", token="writer-token")
                self.assertEqual(status, 400)
                self.assertTrue(body.get("error"))
        self.assertEqual(self._read_fp(), _EDM_FP_FIXTURE)

    def test_delete_corpus_fp_write_failure_500(self):
        """DELETE 指纹库写失败（review #6）→ 干净 500（与 POST 对称），条目未删、corpus 文件未删。"""
        with open(os.path.join(self.corpus_dir, "alpha.txt"), "w", encoding="utf-8") as f:
            f.write("corpus 原文")
        with mock.patch.object(admin_api, "write_json_atomic", side_effect=OSError("disk full")):
            status, body = _request("DELETE", "/dlp-admin/edm/corpus/alpha", token="writer-token")
        self.assertEqual(status, 500)
        self.assertTrue(body.get("error"))
        self.assertIn("alpha", self._read_fp()["docs"])  # 条目未删
        self.assertTrue(os.path.exists(os.path.join(self.corpus_dir, "alpha.txt")))  # 文件未删

    def test_edm_post_large_document_200(self):
        """EDM corpus POST 体上限放宽 16MB（review #5）：~1.5MB 真实规模商密文档 → 200
        （旧 1MB 上限会截断误杀：报 invalid JSON 而非真实原因）。"""
        line = ("内部结算备忘录 ZX-77：codex 渠道结算比例为 0.831，tokenhub 渠道为 0.917，"
                "尾差计入损益调整科目 6650；月度对账报告由计费引擎自动生成并经合规复核。\n")
        text = line * 12000  # UTF-8 约 1.5MB
        self.assertGreater(len(text.encode("utf-8")), 1024 * 1024)  # 超旧 1MB 上限
        status, body = _request("POST", "/dlp-admin/edm/corpus", token="writer-token",
                                payload={"name": "bigdoc", "text": text})
        self.assertEqual(status, 200, body)
        self.assertGreater(body["shingle_count"], 0)
        status, _ = _request("DELETE", "/dlp-admin/edm/corpus/bigdoc", token="writer-token")  # 清理
        self.assertEqual(status, 200)

    def test_body_oversize_413(self):
        """体超限统一 413（review #5）：超限如实报"超限"，不再截断后误报 invalid JSON。
        EDM 上限 16MB；其余端点维持默认 1MB。"""
        status, body = _request("POST", "/dlp-admin/edm/corpus", token="writer-token",
                                payload={"name": "huge", "text": "x" * (16 * 1024 * 1024 + 1)})
        self.assertEqual(status, 413)
        self.assertIn("超限", body.get("error", ""))
        status, body = _request("PUT", "/dlp-admin/wordlist", token="writer-token",
                                payload={"version": 2, "terms": [{"value": "x" * (1024 * 1024 + 100), "rule_id": "r1"}]})
        self.assertEqual(status, 413)
        self.assertIn("超限", body.get("error", ""))


# 文件直传 fixture 复用提取器单测的现场造档函数（tests 目录已在 discover 路径上）
from test_doc_extract import (  # noqa: E402
    _chi_sim_available,
    _tesseract_available,
    make_docx_bytes,
    make_pdf_bytes,
    make_png_bytes,
    make_scanned_pdf_bytes,
)

# e2e 语料内容：单行 > 50 字符（shingle 多窗口）且 ≥12（行级通道），粘贴片段双通道可命中
_UPLOAD_DOCX_PARAGRAPH = "q3 采购框架协议机密条款 供应商甲 违约金百分之二十 交付期四十五天 验收标准按附件三执行 争议提交上海仲裁委员会"
_UPLOAD_PDF_LINE = "q3 confidential settlement memo ZX-77 ratio 0.831 tokenhub 0.917 internal"


def _corpus_prefix(corpus_text, n=60):
    """corpus 实存文本的归一化前缀（≥ shingle 窗口 50 字符）。OCR 输出以实存为准取片段，
    与引擎对空格/标点的具体判定解耦，自洽保证 shingle 通道命中。"""
    fragment = " ".join(corpus_text.split())[:n]
    if len(fragment) < 50:
        raise AssertionError(f"corpus 文本仅 {len(fragment)} 字符（< 50），OCR 提取量不足以构造 shingle 片段")
    return fragment


class AdminEdmCorpusUploadTest(unittest.TestCase):
    """EDM 文件直传（issue #48/#50）：POST /dlp-admin/edm/corpus/upload?name=&filename=（raw bytes）。
    .docx/.pdf 提取文本、图片/扫描 PDF 走 OCR 建指纹 + 粘贴片段命中（检测侧同法断言）；
    .doc/GIF/WebP/未知扩展名明确拒绝。"""

    def setUp(self):
        _FAKE_STATE["mode"] = "ok"
        _FAKE_STATE["tokens"] = {
            "reader-token": {"id": "42", "isOwner": False, "scopes": ["read_channels"]},
            "writer-token": {"id": "7", "isOwner": False, "scopes": ["read_channels", "write_channels"]},
        }
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.fp_path = os.path.join(d, "fingerprints.json")
        self.corpus_dir = os.path.join(d, "corpus")
        os.makedirs(self.corpus_dir)
        with open(self.fp_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "docs": {}}, f)
        self._saved = (admin_api.EDM_FP_PATH, admin_api.EDM_CORPUS_DIR)
        admin_api.EDM_FP_PATH = self.fp_path
        admin_api.EDM_CORPUS_DIR = self.corpus_dir

    def tearDown(self):
        admin_api.EDM_FP_PATH, admin_api.EDM_CORPUS_DIR = self._saved
        self._tmp.cleanup()

    def _upload(self, name, filename, data, token="writer-token"):
        import urllib.parse
        qs = urllib.parse.urlencode({"name": name, "filename": filename})
        return _request_raw("POST", f"/dlp-admin/edm/corpus/upload?{qs}", token=token, data=data)

    def _stored_doc(self, name):
        with open(self.fp_path, encoding="utf-8") as f:
            return json.load(f)["docs"][name]

    def _assert_fragment_hit(self, name, fragment):
        """粘贴 fragment（文档真实片段）对库中指纹的命中数 ≥ 2（检测侧 app.edm_hit_count 同法）。"""
        doc = self._stored_doc(name)
        hits = shim_app.edm_hit_count(fragment, (set(doc["shingles"]), set(doc["lines"])))
        self.assertGreaterEqual(hits, 2)

    def test_upload_docx_e2e(self):
        """直传 .docx → 200 建指纹 + corpus 落提取文本；粘贴文中片段命中（双通道达阈 2）。"""
        data = make_docx_bytes([_UPLOAD_DOCX_PARAGRAPH, "第二条 保密义务与审计配合"])
        status, body = self._upload("q3contract", "采购协议.docx", data)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["name"], "q3contract")
        self.assertGreater(body["shingle_count"], 0)
        self.assertGreater(body["line_count"], 0)
        with open(os.path.join(self.corpus_dir, "q3contract.txt"), encoding="utf-8") as f:
            stored_text = f.read()
        self.assertIn(_UPLOAD_DOCX_PARAGRAPH, stored_text)
        self._assert_fragment_hit("q3contract", _UPLOAD_DOCX_PARAGRAPH)

    def test_upload_pdf_e2e(self):
        """直传文字版 .pdf → 200 建指纹；粘贴 PDF 行片段命中。"""
        data = make_pdf_bytes([_UPLOAD_PDF_LINE])
        status, body = self._upload("memopdf", "memo.pdf", data)
        self.assertEqual(status, 200, body)
        self.assertGreater(body["shingle_count"], 0)
        self._assert_fragment_hit("memopdf", _UPLOAD_PDF_LINE)

    def test_upload_rejections_400(self):
        """明确拒绝（issue #48/#50）：老式 .doc → 提示另存 .docx；GIF/WebP → 提示转 PNG/JPG；未知扩展名 → 支持清单。"""
        cases = {
            "old.doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 ole", ".docx"),
            "pic.gif": (b"GIF89a" + b"\x00" * 32, "PNG/JPG"),
            "a.zip": (b"PK\x03\x04" + b"\x00" * 32, ".pdf"),
        }
        for filename, (data, expect) in cases.items():
            with self.subTest(filename=filename):
                status, body = self._upload("rejectdoc", filename, data)
                self.assertEqual(status, 400, body)
                self.assertIn(expect, body.get("error", ""))
        self.assertEqual(os.listdir(self.corpus_dir), [])  # 拒绝路径不落盘

    def test_upload_blank_scanned_pdf_400(self):
        """空白扫描页 PDF（issue #50 OCR 路径）：引擎缺失 → 400 OCR 不可用；
        引擎可用 → OCR 全空 → 400 未提取到文本。两种环境都须干净 400。"""
        status, body = self._upload("scanpdf", "scan.pdf", make_pdf_bytes([""]))
        self.assertEqual(status, 400, body)
        self.assertTrue("OCR" in body.get("error", "") or "未提取到文本" in body.get("error", ""))

    def test_upload_bad_params_400(self):
        """name 非法 / 缺 filename → 400；body 空 → 400（空文件）。"""
        data = make_docx_bytes([_UPLOAD_DOCX_PARAGRAPH])
        status, body = self._upload("bad/name", "a.docx", data)
        self.assertEqual(status, 400)
        status, body = _request_raw("POST", "/dlp-admin/edm/corpus/upload?name=nofn",
                                    token="writer-token", data=data)
        self.assertEqual(status, 400)
        self.assertIn("filename", body.get("error", ""))
        status, body = self._upload("emptydoc", "a.docx", b"")
        self.assertEqual(status, 400)

    def test_upload_auth(self):
        """直传走写级鉴权：无 token → 401；只读 token → 403。"""
        data = make_docx_bytes([_UPLOAD_DOCX_PARAGRAPH])
        status, _ = self._upload("authdoc", "a.docx", data, token=None)
        self.assertEqual(status, 401)
        status, _ = self._upload("authdoc", "a.docx", data, token="reader-token")
        self.assertEqual(status, 403)

    def test_upload_oversized_extracted_text_400(self):
        """提取文本超 8M 字符上限（issue #49 P1-1）→ 400 明确报错，shim 不崩（后续请求正常）。"""
        data = ("x" * (8 * 1000 * 1000 + 1)).encode("utf-8")  # ~8MB body（16MB 线路限内），提取文本超限
        status, body = self._upload("hugedoc", "huge.txt", data)
        self.assertEqual(status, 400, body)
        self.assertIn("上限", body.get("error", ""))
        self.assertEqual(os.listdir(self.corpus_dir), [])  # 未落盘
        # shim 未崩：后续正常上传仍 200
        status, body = self._upload("afterhuge", "a.docx", make_docx_bytes([_UPLOAD_DOCX_PARAGRAPH]))
        self.assertEqual(status, 200, body)

    @unittest.skipUnless(_tesseract_available(), "本机无 tesseract 二进制")
    def test_upload_scanned_pdf_e2e(self):
        """扫描 PDF 直传（issue #50）：无文本层 PDF → OCR 提取 → 200 建指纹；取 corpus 实存文本片段命中。"""
        data = make_scanned_pdf_bytes(["Q3 SCAN settlement memo ZX-99 ratio 0.777 tokenhub 0.888 scanonly"])
        status, body = self._upload("scanmemo", "scan-memo.pdf", data)
        self.assertEqual(status, 200, body)
        self.assertGreater(body["shingle_count"], 0)
        with open(os.path.join(self.corpus_dir, "scanmemo.txt"), encoding="utf-8") as f:
            corpus_text = f.read()
        self._assert_fragment_hit("scanmemo", _corpus_prefix(corpus_text))

    @unittest.skipUnless(_chi_sim_available(), "本机无 tesseract chi_sim 语言包")
    def test_upload_chinese_image_e2e(self):
        """中文图片直传（issue #50）：chi_sim OCR → 200 建指纹；取 corpus 实存文本片段命中。"""
        # 56 个中文字符无空格（避开 chi_sim 空格→引号误判），归一化后超 shingle 窗口 50 字符
        data = make_png_bytes("机密采购合同供应商甲违约金百分之二十交付期四十五天验收标准按附件三执行争议提交上海仲裁委员会仲裁分期付款三期结清",
                              fontname="china-s")
        status, body = self._upload("cnimg", "采购.png", data)
        self.assertEqual(status, 200, body)
        with open(os.path.join(self.corpus_dir, "cnimg.txt"), encoding="utf-8") as f:
            corpus_text = f.read()
        self._assert_fragment_hit("cnimg", _corpus_prefix(corpus_text))


class ShimLazyImportTest(unittest.TestCase):
    """无解析库环境 import 健壮性（issue #49 P2-7）：第三方解析库（fitz/docx/openpyxl/pptx）
    被屏蔽时 app/admin_api/doc_extract 仍可 import（懒加载链），检测路径主函数可用。"""

    def test_import_app_without_parser_deps(self):
        shim_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = (
            "import sys, importlib.abc\n"
            "BLOCKED = {'fitz', 'pymupdf', 'docx', 'openpyxl', 'pptx', 'pytesseract', 'PIL'}\n"
            "class Block(importlib.abc.MetaPathFinder):\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] in BLOCKED:\n"
            "            raise ImportError('blocked: ' + name)\n"
            "sys.meta_path.insert(0, Block())\n"
            f"sys.path.insert(0, {shim_dir!r})\n"
            "import app, admin_api, doc_extract\n"
            "assert app.edm_hit_count('hello world', (set(), set())) == 0\n"  # 检测路径可用
            "print('lazy-import-ok')\n"
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("lazy-import-ok", r.stdout)


class EdmLibParityTest(unittest.TestCase):
    """edm_lib 收编等价裁判（issue #34）：入库与检测同法（契约铁律）。
    现 fingerprints.json 对现 corpus 逐文件重算并集，须与磁盘条目一致（真实库只读，不写）。"""

    def test_recompute_matches_disk_store(self):
        import edm_lib
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        fp_path = os.path.join(repo_root, "deploy", "edm", "fingerprints.json")
        corpus_dir = os.path.join(repo_root, "deploy", "edm", "corpus")
        if not os.path.exists(fp_path):
            self.skipTest("真实指纹库不存在")
        with open(fp_path, encoding="utf-8") as f:
            store = json.load(f)
        # 语料侧：corpus/ 现存全部文档逐文件重算的分通道并集（过滤隐藏文件与原子写副产物）
        hashes, lhashes = set(), set()
        for fn in sorted(os.listdir(corpus_dir)):
            if fn.startswith(".") or fn.endswith((".bak", ".tmp")):
                continue
            fp = os.path.join(corpus_dir, fn)
            if not os.path.isfile(fp):
                continue  # 子目录（如试点演练 pilot/）跳过：只逐文件重算
            with open(fp, encoding="utf-8", errors="ignore") as f:
                fps = edm_lib.doc_fingerprints(f.read())
            hashes |= set(fps["shingles"])
            lhashes |= set(fps["lines"])
        # 库侧：全部条目分通道并集（review #7：与条目数无关——单条目聚合库/多条目分篇/零篇均成立）
        lib_shingles, lib_lines = set(), set()
        for doc in (store.get("docs") or {}).values():
            if isinstance(doc, list):  # 旧格式纯 shingle 数组
                lib_shingles |= set(doc)
            else:
                lib_shingles |= set(doc.get("shingles") or [])
                lib_lines |= set(doc.get("lines") or [])
        self.assertEqual(sorted(hashes), sorted(lib_shingles))
        self.assertEqual(sorted(lhashes), sorted(lib_lines))

    def test_edm_lib_shingles_behavior_and_hit_count(self):
        """edm_lib.shingles 行为锚（review #3：app 主路径直调 edm_lib，包装函数已删，改为直测算法本身）+
        app 检测主路径 edm_hit_count 命中数与 edm_lib 直算一致。"""
        import edm_lib
        # shingles 算法锚：短文档整段单 shingle；空文本空；归一化折叠；窗口 50 步长 1（60 字符 → 11 窗）
        self.assertEqual(edm_lib.shingles("abc"), ["abc"])
        self.assertEqual(edm_lib.shingles("   "), [])
        self.assertEqual(edm_lib.shingles("  AB  cd "), ["ab cd"])
        self.assertEqual(edm_lib.shingles("x" * 60), ["x" * 50] * 11)
        text = "  Mixed CASE 文本\nwith 多余   空白 and lines  \n短行\n" + "x" * 60
        shingle_fps = {edm_lib.fp_of(s) for s in edm_lib.shingles(text)}
        line_fps = edm_lib.line_hashes(text)
        self.assertEqual(shim_app.edm_hit_count(text, (shingle_fps, line_fps)),
                         max(len(shingle_fps), len(line_fps)))
        self.assertEqual(shim_app.edm_hit_count(text, (set(), set())), 0)
        self.assertEqual(shim_app.edm_hit_count("无关文本", (shingle_fps, line_fps)), 0)


# 合法 settings fixture（issue #35）：结构对齐 deploy/dlp/settings.json 首版
_SETTINGS_FIXTURE = {
    "version": 1,
    "_comment": "测试 fixture",
    "judge": {
        "enabled": True,
        "model": "deepseek-v4-flash",
        "base_url": "http://axonhub:8090/v1",
        "timeout": 8,
        "prompt_system": "系统提示 {terms} {{json}}",
        "prompt_fewshot": "示例",
    },
    "edm": {"enabled": True, "min_hits": 2},
    "pg": {"enabled": True, "threshold": 0.7, "normalize": False},
    "l1": {"enabled": True},
    "l2": {"enabled": True},
    "response": {"enabled": True},
}


class AdminSettingsTest(unittest.TestCase):
    """统一 settings（issue #35）：GET 读级整返 / PUT 写级校验+原子写。
    fixture：每用例临时 settings.json，覆写 admin_api.SETTINGS_PATH。"""

    def setUp(self):
        _FAKE_STATE["mode"] = "ok"
        _FAKE_STATE["tokens"] = {
            "reader-token": {"id": "42", "isOwner": False, "scopes": ["read_channels"]},
            "writer-token": {"id": "7", "isOwner": False, "scopes": ["read_channels", "write_channels"]},
        }
        self._tmp = tempfile.TemporaryDirectory()
        self.settings_path = os.path.join(self._tmp.name, "settings.json")
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(_SETTINGS_FIXTURE, f, ensure_ascii=False)
        self._saved = admin_api.SETTINGS_PATH
        admin_api.SETTINGS_PATH = self.settings_path

    def tearDown(self):
        admin_api.SETTINGS_PATH = self._saved
        self._tmp.cleanup()

    def _put(self, payload, token="writer-token"):
        return _request("PUT", "/dlp-admin/settings", token=token, payload=payload)

    def test_get_settings(self):
        """GET /dlp-admin/settings（读级）→ 200 返回 JSON 全文。"""
        status, body = _get("/dlp-admin/settings", token="reader-token")
        self.assertEqual(status, 200)
        self.assertEqual(body, _SETTINGS_FIXTURE)

    def test_get_settings_missing_404(self):
        """文件不存在 → 404 env 兜底态（#35 review #3：缺失是合法回退态，非 500 故障）。"""
        os.unlink(self.settings_path)
        status, body = _get("/dlp-admin/settings", token="reader-token")
        self.assertEqual(status, 404)
        self.assertEqual(body, {"error": "settings.json 不存在，当前为 env 兜底态"})

    def test_get_settings_corrupt_500(self):
        """文件存在但损坏 → 500（故障态，与缺失区分开）。"""
        with open(self.settings_path, "w") as f:
            f.write("{not json")
        status, body = _get("/dlp-admin/settings", token="reader-token")
        self.assertEqual(status, 500)
        self.assertIn("error", body)

    def test_put_settings_valid_200(self):
        """PUT 合法整体替换（写级）→ 200，响应与落盘均为新内容（整体替换语义）。"""
        new = json.loads(json.dumps(_SETTINGS_FIXTURE))
        new["pg"]["threshold"] = 0.9
        new["judge"]["enabled"] = False
        status, body = self._put(new)
        self.assertEqual(status, 200)
        self.assertEqual(body, new)
        with open(self.settings_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), new)

    def test_put_settings_write_scope_required(self):
        """PUT 仅读级 token → 403（写端点门槛）。"""
        status, _ = self._put(_SETTINGS_FIXTURE, token="reader-token")
        self.assertEqual(status, 403)

    def test_put_settings_validation_400(self):
        """PUT 校验（issue #35）：逐条非法变体 → 400 带原因，且落盘文件不被污染。"""
        def mutated(fn):
            d = json.loads(json.dumps(_SETTINGS_FIXTURE))
            fn(d)
            return d
        cases = [
            ("非对象", ["not", "a", "dict"]),
            ("顶层未知键", mutated(lambda d: d.update({"unknown": 1}))),
            ("version 非整数", mutated(lambda d: d.update({"version": "1"}))),
            ("_comment 非字符串", mutated(lambda d: d.update({"_comment": 1}))),
            ("judge 非对象", mutated(lambda d: d.update({"judge": 1}))),
            ("judge 缺字段", mutated(lambda d: d["judge"].pop("model"))),
            ("judge 未知字段", mutated(lambda d: d["judge"].update({"typo": 1}))),
            ("judge.enabled 非布尔", mutated(lambda d: d["judge"].update({"enabled": 1}))),
            ("judge.timeout 为零", mutated(lambda d: d["judge"].update({"timeout": 0}))),
            ("judge.timeout 为布尔", mutated(lambda d: d["judge"].update({"timeout": True}))),
            ("judge.model 空串", mutated(lambda d: d["judge"].update({"model": ""}))),
            ("judge.prompt_system 非字符串", mutated(lambda d: d["judge"].update({"prompt_system": 1}))),
            ("judge.prompt_fewshot 空串", mutated(lambda d: d["judge"].update({"prompt_fewshot": ""}))),
            ("edm 缺 enabled", mutated(lambda d: d["edm"].pop("enabled"))),
            ("edm.min_hits 小于 1", mutated(lambda d: d["edm"].update({"min_hits": 0}))),
            ("edm.min_hits 为布尔", mutated(lambda d: d["edm"].update({"min_hits": True}))),
            ("edm.min_hits 为浮点", mutated(lambda d: d["edm"].update({"min_hits": 2.5}))),
            ("pg 缺 threshold", mutated(lambda d: d["pg"].pop("threshold"))),
            ("pg.threshold 超界", mutated(lambda d: d["pg"].update({"threshold": 2}))),
            ("pg.threshold 为布尔", mutated(lambda d: d["pg"].update({"threshold": False}))),
            # pg.normalize（issue #44）：必填布尔
            ("pg 缺 normalize", mutated(lambda d: d["pg"].pop("normalize"))),
            ("pg.normalize 非布尔", mutated(lambda d: d["pg"].update({"normalize": 1}))),
            # 分层总开关三段（issue #40）：必填、仅 enabled 单键、必须布尔
            ("l1 缺 enabled", mutated(lambda d: d["l1"].pop("enabled"))),
            ("l1.enabled 非布尔", mutated(lambda d: d["l1"].update({"enabled": 1}))),
            ("l2 未知字段", mutated(lambda d: d["l2"].update({"typo": 1}))),
            ("response 非对象", mutated(lambda d: d.update({"response": 1}))),
            ("response 整段缺失", mutated(lambda d: d.pop("response"))),
        ]
        for label, payload in cases:
            with self.subTest(case=label):
                status, body = self._put(payload)
                self.assertEqual(status, 400, f"{label}: 期望 400，实际 {status} {body}")
                self.assertIn("error", body)
        # 全部 400：落盘文件保持 fixture 原样（未被任何非法写污染）
        with open(self.settings_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), _SETTINGS_FIXTURE)


class AppSettingsTest(unittest.TestCase):
    """app 侧统一 settings（issue #35）：load_settings 每请求重读 + setting_value 三级取值。
    fixture：覆写 shim_app.SETTINGS_PATH 指向临时文件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings_path = os.path.join(self._tmp.name, "settings.json")
        self._saved = shim_app.SETTINGS_PATH
        shim_app.SETTINGS_PATH = self.settings_path

    def tearDown(self):
        shim_app.SETTINGS_PATH = self._saved
        self._tmp.cleanup()

    def test_load_settings_missing_returns_empty(self):
        """文件缺失 → {}（settings.json 是可选覆盖层，缺失即全量回退 env/默认）。"""
        self.assertEqual(shim_app.load_settings(), {})

    def test_load_settings_corrupt_or_non_dict_returns_empty(self):
        """损坏 JSON / 合法 JSON 但非对象 → {}（回退 env/默认）。"""
        with open(self.settings_path, "w") as f:
            f.write("{not json")
        self.assertEqual(shim_app.load_settings(), {})
        with open(self.settings_path, "w") as f:
            json.dump(["not", "a", "dict"], f)
        self.assertEqual(shim_app.load_settings(), {})

    def test_load_settings_reads_file(self):
        """正常文件 → 原样返回 dict。"""
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(_SETTINGS_FIXTURE, f)
        self.assertEqual(shim_app.load_settings(), _SETTINGS_FIXTURE)

    def test_setting_value_three_level_priority(self):
        """三级取值（issue #35）：settings.json > env > 内置默认。"""
        # JSON 命中 → 直接用（即使 env 也设了别的值）
        with mock.patch.dict(os.environ, {"PG_THRESHOLD": "0.55"}):
            self.assertEqual(
                shim_app.setting_value({"pg": {"threshold": 0.9}}, "pg", "threshold", "PG_THRESHOLD", 0.7), 0.9)
        # JSON 缺区段/缺键 → env（按 default 类型转换）
        env_cases = [
            ("judge", "enabled", "JUDGE_ENABLED", False, "1", True),
            ("judge", "enabled", "JUDGE_ENABLED", True, "0", False),
            ("judge", "timeout", "JUDGE_TIMEOUT", 8, "12", 12),
            ("edm", "min_hits", "EDM_MIN_HITS", 2, "3", 3),
            ("pg", "threshold", "PG_THRESHOLD", 0.7, "0.55", 0.55),
            ("judge", "model", "JUDGE_MODEL", "m0", "m1", "m1"),
            # 非法 env 回退默认（比旧模块级 int()/float() 启动即崩更宽容）
            ("judge", "timeout", "JUDGE_TIMEOUT", 8, "garbage", 8),
            ("pg", "threshold", "PG_THRESHOLD", 0.7, "garbage", 0.7),
            # 分层总开关（issue #40）：内置默认 True（保现网行为），env "0" 才关
            ("l1", "enabled", "L1_ENABLED", True, "0", False),
            ("l2", "enabled", "L2_ENABLED", True, "0", False),
            ("response", "enabled", "RESPONSE_ENABLED", True, "0", False),
        ]
        for section, key, env_name, default, env_val, expected in env_cases:
            with self.subTest(key=key, env_val=env_val):
                with mock.patch.dict(os.environ, {env_name: env_val}):
                    self.assertEqual(
                        shim_app.setting_value({}, section, key, env_name, default), expected)
                    # 区段存在但键缺失同样落 env 级
                    self.assertEqual(
                        shim_app.setting_value({section: {}}, section, key, env_name, default), expected)
        # env 也缺 → 内置默认
        os.environ.pop("JUDGE_TIMEOUT", None)
        self.assertEqual(shim_app.setting_value({}, "judge", "timeout", "JUDGE_TIMEOUT", 8), 8)
        # 分层总开关（issue #40）：settings 缺段 + env 缺 → 内置默认 True（缺失键回归：旧 settings.json 不降级）
        for section, env_name in (("l1", "L1_ENABLED"), ("l2", "L2_ENABLED"), ("response", "RESPONSE_ENABLED")):
            os.environ.pop(env_name, None)
            with self.subTest(section=section):
                self.assertIs(shim_app.setting_value({}, section, "enabled", env_name, True), True)
        # env_name=None（judge prompt 无 env 级，原为代码常量）→ 直接默认
        self.assertEqual(shim_app.setting_value({}, "judge", "prompt_system", None, "D"), "D")

    def test_setting_value_type_guardrail(self):
        """读取路径逐键类型护栏（#35 review #1）：手工把 JSON 值改错类型 → 该键回退 env/默认 + warn
        （不含值本身），同文件其余可用键仍生效。schema 与 admin 写侧同款：
        enabled→bool、threshold/timeout→数值、min_hits→int、字符串字段→str。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            # 数值字段拒绝 str / bool
            self.assertEqual(
                shim_app.setting_value({"pg": {"threshold": "0.7"}}, "pg", "threshold", "PG_THRESHOLD", 0.7), 0.7)
            self.assertEqual(
                shim_app.setting_value({"judge": {"timeout": True}}, "judge", "timeout", "JUDGE_TIMEOUT", 8), 8)
            # bool 字段拒绝 int 1（Python bool 是 int 子类，须显式排）
            self.assertEqual(
                shim_app.setting_value({"judge": {"enabled": 1}}, "judge", "enabled", "JUDGE_ENABLED", False), False)
            # int 字段拒绝 float
            self.assertEqual(
                shim_app.setting_value({"edm": {"min_hits": 2.5}}, "edm", "min_hits", "EDM_MIN_HITS", 2), 2)
            # 字符串字段拒绝非 str
            self.assertEqual(
                shim_app.setting_value({"judge": {"model": 123}}, "judge", "model", "JUDGE_MODEL", "m0"), "m0")
        out = buf.getvalue()
        self.assertEqual(out.count("类型不符"), 5)
        self.assertIn("pg.threshold", out)
        self.assertNotIn('"0.7"', out)  # warn 不带回显值本身
        # 合法类型原样通过（number 兼收 int/float；bool/str/int 各归各型）
        self.assertEqual(shim_app.setting_value({"pg": {"threshold": 1}}, "pg", "threshold", "PG_THRESHOLD", 0.7), 1)
        self.assertEqual(shim_app.setting_value({"judge": {"timeout": 2.5}}, "judge", "timeout", "JUDGE_TIMEOUT", 8), 2.5)
        self.assertEqual(shim_app.setting_value({"edm": {"min_hits": 3}}, "edm", "min_hits", "EDM_MIN_HITS", 2), 3)
        self.assertEqual(shim_app.setting_value({"judge": {"enabled": True}}, "judge", "enabled", "JUDGE_ENABLED", False), True)
        # 坏键不牵连同文件好键
        s = {"pg": {"threshold": "bad", "enabled": True}}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(shim_app.setting_value(s, "pg", "enabled", "PG_ENABLED", False), True)
            self.assertEqual(shim_app.setting_value(s, "pg", "threshold", "PG_THRESHOLD", 0.7), 0.7)


class _FakeJudge(BaseHTTPRequestHandler):
    """假 judge（OpenAI 兼容 /chat/completions）：记录请求体，回固定 confidential verdict。"""

    captured = {}

    def log_message(self, *args):  # 静默
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        _FakeJudge.captured = json.loads(self.rfile.read(length) or b"{}")
        body = json.dumps({"choices": [{"message": {"content":
            '{"confidential": true, "entities": ["项目代号"], "confidence": 0.9}'}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class JudgeSettingsTest(unittest.TestCase):
    """judge 走统一 settings（issue #35 核心行为）：prompt/model/base_url 从 JSON 生效；env 层兜底。
    fixture：临时 settings.json（指向假 judge）+ 词表；覆写 shim_app 模块级路径与 JUDGE_API_KEY。"""

    @classmethod
    def setUpClass(cls):
        cls._judge_srv = _start_server(_FakeJudge)

    @classmethod
    def tearDownClass(cls):
        cls._judge_srv.shutdown()

    def setUp(self):
        _FakeJudge.captured = {}
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.settings_path = os.path.join(d, "settings.json")
        self.wordlist_path = os.path.join(d, "wordlist.json")
        with open(self.wordlist_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1,
                       "terms": [{"value": "凤皇计划", "rule_id": "confidential.fenghuang"}]},
                      f, ensure_ascii=False)
        self._fixture = json.loads(json.dumps(_SETTINGS_FIXTURE))
        j = self._fixture["judge"]
        j["enabled"] = True
        j["base_url"] = f"http://127.0.0.1:{self._judge_srv.server_address[1]}"
        j["model"] = "json-model"
        j["prompt_system"] = "自定义系统提示：{terms}"
        j["prompt_fewshot"] = "自定义示例"
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(self._fixture, f, ensure_ascii=False)
        self._saved = (shim_app.SETTINGS_PATH, shim_app.WORDLIST_PATH, shim_app.JUDGE_API_KEY)
        shim_app.SETTINGS_PATH = self.settings_path
        shim_app.WORDLIST_PATH = self.wordlist_path
        shim_app.JUDGE_API_KEY = "test-key"

    def tearDown(self):
        shim_app.SETTINGS_PATH, shim_app.WORDLIST_PATH, shim_app.JUDGE_API_KEY = self._saved
        self._tmp.cleanup()

    def test_judge_uses_settings_values(self):
        """prompt/model/base_url 来自 settings.json；{terms} 被词表替换；verdict 正常解析。"""
        v = shim_app.judge_text("凤皇计划的排期发我")
        self.assertEqual(v, {"confidential": True, "entities": ["项目代号"], "confidence": 0.9})
        req = _FakeJudge.captured
        self.assertEqual(req["model"], "json-model")
        msgs = req["messages"]
        self.assertEqual(msgs[0], {"role": "system", "content": "自定义系统提示：凤皇计划"})
        self.assertEqual(msgs[1], {"role": "user", "content": "自定义示例"})
        self.assertEqual(msgs[2], {"role": "user", "content": "凤皇计划的排期发我"})

    def test_judge_disabled_in_settings_wins_over_env(self):
        """JSON enabled=false 优先于 env JUDGE_ENABLED=1（三级取值 JSON 级最高）：不发请求，返回 None。"""
        self._fixture["judge"]["enabled"] = False
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(self._fixture, f)
        with mock.patch.dict(os.environ, {"JUDGE_ENABLED": "1"}):
            self.assertIsNone(shim_app.judge_text("任意文本"))
        self.assertEqual(_FakeJudge.captured, {})

    def test_judge_unavailable_when_settings_missing(self):
        """settings.json 缺失 → prompt 无源（#35 review #2：prompt 单一源=JSON，删代码内默认）→
        judge 不可用返回 None + warn，即使 env 把开关/地址/模型配齐也不发请求。"""
        os.unlink(self.settings_path)
        env = {"JUDGE_ENABLED": "1", "JUDGE_MODEL": "env-model",
               "JUDGE_BASE_URL": f"http://127.0.0.1:{self._judge_srv.server_address[1]}"}
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(buf):
            self.assertIsNone(shim_app.judge_text("帮我写本周工作总结"))
        self.assertEqual(_FakeJudge.captured, {})
        self.assertIn("judge prompt", buf.getvalue())

    def test_judge_prompt_key_missing_fails_open(self):
        """JSON 缺 prompt_fewshot 键 → judge 不可用返回 None + warn（其余配置齐全也不发请求）。"""
        del self._fixture["judge"]["prompt_fewshot"]
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(self._fixture, f, ensure_ascii=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertIsNone(shim_app.judge_text("任意文本"))
        self.assertEqual(_FakeJudge.captured, {})
        self.assertIn("judge prompt", buf.getvalue())

    def test_judge_broken_prompt_placeholder_fails_open(self):
        """settings 里 prompt 含非法单花括号占位 → .format 失败 → None（fail-open，不炸请求链）。"""
        self._fixture["judge"]["prompt_system"] = "坏 {oops"
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(self._fixture, f)
        self.assertIsNone(shim_app.judge_text("任意文本"))


class _FakePG(BaseHTTPRequestHandler):
    """假 PromptGuard（POST /guard）：固定 malicious=0.785（对齐现网角色劫持样本实测分）。"""

    def log_message(self, *args):  # 静默
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = b'{"malicious": 0.785}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class InjectionShadowSettingsTest(unittest.TestCase):
    """注入 shadow 读取路径类型护栏（#35 review #1 集成）：threshold 被手改成字符串时，
    /request 检测链不炸（旧版在响应已发后 score>=str 抛 TypeError）、该键回退默认 0.7 + warn，
    同文件其余键（pg.enabled）仍从 JSON 生效。
    fixture：临时 settings.json + 假 PG；覆写 shim_app.SETTINGS_PATH/PG_URL。"""

    @classmethod
    def setUpClass(cls):
        cls._pg_srv = _start_server(_FakePG)

    @classmethod
    def tearDownClass(cls):
        cls._pg_srv.shutdown()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings_path = os.path.join(self._tmp.name, "settings.json")
        fixture = json.loads(json.dumps(_SETTINGS_FIXTURE))
        fixture["judge"]["enabled"] = False  # 隔离 judge 路径，只验注入 shadow
        fixture["pg"]["threshold"] = "0.7"   # review 场景：手改成字符串
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(fixture, f, ensure_ascii=False)
        self._saved = (shim_app.SETTINGS_PATH, shim_app.PG_URL)
        shim_app.SETTINGS_PATH = self.settings_path
        shim_app.PG_URL = f"http://127.0.0.1:{self._pg_srv.server_address[1]}/guard"

    def tearDown(self):
        shim_app.SETTINGS_PATH, shim_app.PG_URL = self._saved
        self._tmp.cleanup()

    def test_string_threshold_falls_back_no_crash(self):
        """threshold="0.7"（字符串）→ 回退默认 0.7 不抛异常 + warn；0.785≥0.7 shadow 仍记录。"""
        with mock.patch("builtins.print") as m:
            status, body = _request(
                "POST", "/request",
                payload={"body": {"messages": [{"role": "user", "content": "普通业务咨询，请帮我写周报"}]}})
            time.sleep(0.5)  # shadow 段在响应后异步执行，等其落完
        self.assertEqual(status, 200)
        self.assertEqual(body["action"].get("reason"), "pass")
        printed = "\n".join(str(c.args[0]) for c in m.call_args_list if c.args)
        self.assertIn("[settings] pg.threshold 类型不符", printed)
        self.assertIn("[injection.shadow] malicious=0.785 >= 0.7", printed)


class _FakePGCapture(BaseHTTPRequestHandler):
    """假 PromptGuard：捕获最近一个请求体，固定回 malicious=0.123。"""

    last_body = None

    def log_message(self, *args):  # 静默
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        type(self).last_body = json.loads(self.rfile.read(length) or b"{}")
        body = b'{"malicious": 0.123}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class PgNormalizeFlagTest(unittest.TestCase):
    """pg_guard 归一化开关透传（issue #44）：settings pg.normalize 三级取值 → /guard 请求体
    normalize 字段；缺键/显式 false → false（现网行为），显式 true → true（只改打分输入）。"""

    @classmethod
    def setUpClass(cls):
        cls._pg_srv = _start_server(_FakePGCapture)

    @classmethod
    def tearDownClass(cls):
        cls._pg_srv.shutdown()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings_path = os.path.join(self._tmp.name, "settings.json")
        self._saved = (shim_app.SETTINGS_PATH, shim_app.PG_URL)
        shim_app.SETTINGS_PATH = self.settings_path
        shim_app.PG_URL = f"http://127.0.0.1:{self._pg_srv.server_address[1]}/guard"
        # env 隔离（对齐 AppSettingsTest 纪律）：开发机/CI 导出 PG_NORMALIZE 会在缺键用例中
        # 顶替内置默认 false（setting_value 缺键落 env 级），pop 防误失败
        self._saved_env = {k: os.environ.pop(k, None) for k in ("PG_NORMALIZE",)}

    def tearDown(self):
        shim_app.SETTINGS_PATH, shim_app.PG_URL = self._saved
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
        self._tmp.cleanup()

    def _write_settings(self, pg_section):
        fixture = json.loads(json.dumps(_SETTINGS_FIXTURE))
        fixture["judge"]["enabled"] = False
        fixture["pg"] = pg_section
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(fixture, f, ensure_ascii=False)

    def test_normalize_true_passed_through(self):
        self._write_settings({"enabled": True, "threshold": 0.7, "normalize": True})
        self.assertEqual(shim_app.pg_guard("任意文本"), 0.123)
        self.assertIs(_FakePGCapture.last_body["normalize"], True)
        self.assertEqual(_FakePGCapture.last_body["text"], "任意文本")

    def test_normalize_false_explicit(self):
        self._write_settings({"enabled": True, "threshold": 0.7, "normalize": False})
        shim_app.pg_guard("任意文本")
        self.assertIs(_FakePGCapture.last_body["normalize"], False)

    def test_normalize_missing_key_defaults_false(self):
        """旧 settings.json 无 normalize 键（升级前）→ 默认 false 现网行为，不降级。"""
        self._write_settings({"enabled": True, "threshold": 0.7})
        shim_app.pg_guard("任意文本")
        self.assertIs(_FakePGCapture.last_body["normalize"], False)

    def test_normalize_disabled_no_request(self):
        """pg.enabled=false → 不发请求返回 None（fail-open 既有语义不受新键影响）。"""
        _FakePGCapture.last_body = None
        self._write_settings({"enabled": False, "threshold": 0.7, "normalize": True})
        self.assertIsNone(shim_app.pg_guard("任意文本"))
        self.assertIsNone(_FakePGCapture.last_body)


class LayerSwitchTest(unittest.TestCase):
    """分层总开关（issue #40）：l1/l2/response.enabled=false 的三条关闭路径 + l1 渲染联动。
    fixture：临时 settings/wordlist/format-rules/config.yaml；judge/pg/edm 关掉隔离。
    检测路径覆写 shim_app.*，admin 写路径覆写 admin_api.*（两个模块各自持有路径属性）。"""

    def setUp(self):
        _FAKE_STATE["mode"] = "ok"
        _FAKE_STATE["tokens"] = {
            "writer-token": {"id": "7", "isOwner": False, "scopes": ["read_channels", "write_channels"]},
        }
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.settings_path = os.path.join(d, "settings.json")
        self.wordlist_path = os.path.join(d, "wordlist.json")
        self.rules_path = os.path.join(d, "format-rules.json")
        self.config_path = os.path.join(d, "config.yaml")
        self._fixture = json.loads(json.dumps(_SETTINGS_FIXTURE))
        # 隔离其余层：只验 l1/l2/response 开关语义
        self._fixture["judge"]["enabled"] = False
        self._fixture["pg"]["enabled"] = False
        self._fixture["edm"]["enabled"] = False
        self._write_settings()
        with open(self.wordlist_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1,
                       "terms": [{"value": "凤凰计划", "rule_id": "confidential.fenghuang"}]},
                      f, ensure_ascii=False)
        with open(self.rules_path, "w", encoding="utf-8") as f:
            json.dump(_FR_FIXTURE, f, ensure_ascii=False)
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(_CONFIG_FIXTURE)
        self._saved_app = (shim_app.SETTINGS_PATH, shim_app.WORDLIST_PATH,
                           shim_app.FORMAT_RULES_PATH, shim_app.PII_RECOGNIZERS_PATH)
        shim_app.SETTINGS_PATH = self.settings_path
        shim_app.WORDLIST_PATH = self.wordlist_path
        shim_app.FORMAT_RULES_PATH = self.rules_path
        # 指向不存在文件 → load_pii_recognizers 空（隔离 Presidio 路径，不依赖本机文件）
        shim_app.PII_RECOGNIZERS_PATH = os.path.join(d, "pii-zh.json")
        self._saved_admin = (admin_api.SETTINGS_PATH, admin_api.FORMAT_RULES_PATH,
                             admin_api.AGENTGW_CONFIG_PATH)
        admin_api.SETTINGS_PATH = self.settings_path
        admin_api.FORMAT_RULES_PATH = self.rules_path
        admin_api.AGENTGW_CONFIG_PATH = self.config_path
        # env 隔离（对齐 AppSettingsTest 纪律）：开发机/CI 导出分层总开关 env 会在缺段用例中
        # 顶替内置默认（setting_value 缺段落 env 级），pop 防误失败
        self._saved_env = {k: os.environ.pop(k, None)
                           for k in ("L1_ENABLED", "L2_ENABLED", "RESPONSE_ENABLED")}
        # 开关可观测状态记忆复位（issue #40 review）：跨用例不带上次状态
        shim_app._LAYER_SWITCH_STATE.clear()

    def tearDown(self):
        (shim_app.SETTINGS_PATH, shim_app.WORDLIST_PATH,
         shim_app.FORMAT_RULES_PATH, shim_app.PII_RECOGNIZERS_PATH) = self._saved_app
        (admin_api.SETTINGS_PATH, admin_api.FORMAT_RULES_PATH,
         admin_api.AGENTGW_CONFIG_PATH) = self._saved_admin
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
        self._tmp.cleanup()

    def _write_settings(self):
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(self._fixture, f, ensure_ascii=False)

    def _post_request(self, content):
        return _request("POST", "/request",
                        payload={"body": {"messages": [{"role": "user", "content": content}]}})

    def _post_response(self, content):
        return _request("POST", "/response",
                        payload={"body": {"choices": [
                            {"message": {"role": "assistant", "content": content}}]}})

    def _read_config(self):
        with open(self.config_path, encoding="utf-8") as f:
            return f.read()

    def test_l2_off_wordlist_term_passes(self):
        """l2.enabled=false → 词表词放行（先验基线 451，关后 200 pass）。"""
        status, body = self._post_request("凤凰计划的排期发我")
        self.assertEqual(status, 200)
        self.assertEqual(body["action"].get("status_code"), 451)  # 基线：l2 开 → 拦截
        self._fixture["l2"]["enabled"] = False
        self._write_settings()
        status, body = self._post_request("凤凰计划的排期发我")
        self.assertEqual(status, 200)
        self.assertEqual(body["action"].get("reason"), "pass")

    def test_response_off_hit_passes(self):
        """response.enabled=false → 响应侧命中也 pass（先验基线 451）。"""
        _, body = self._post_response("好的，凤凰计划的排期如下")
        self.assertEqual(body["action"].get("status_code"), 451)  # 基线：命中词表
        self._fixture["response"]["enabled"] = False
        self._write_settings()
        status, body = self._post_response("好的，凤凰计划的排期如下")
        self.assertEqual(status, 200)
        self.assertEqual(body["action"].get("reason"), "pass")

    def test_l1_off_secret_passes_and_render_empty(self):
        """l1.enabled=false → shim 侧 L1 secrets 跳过（先验基线 451）；
        渲染层 include_l1=False → 格式规则全族不渲染（空串，不含任何 pattern）。"""
        _, body = self._post_request("我的 key 是 TESTA1234 请收好")
        self.assertEqual(body["action"].get("status_code"), 451)  # 基线：secrets.test_a
        self._fixture["l1"]["enabled"] = False
        self._write_settings()
        status, body = self._post_request("我的 key 是 TESTA1234 请收好")
        self.assertEqual(status, 200)
        self.assertEqual(body["action"].get("reason"), "pass")
        block = admin_api.render_gateway_block(_FR_FIXTURE["rules"], include_l1=False)
        self.assertEqual(block, "")
        self.assertNotIn("TESTA", block)
        self.assertNotIn("TESTB", block)

    def test_missing_sections_default_on(self):
        """settings.json 缺 l1/l2/response 段（旧文件）→ 内置默认 True，三条路径行为不变。"""
        for section in ("l1", "l2", "response"):
            self._fixture.pop(section)
        self._write_settings()
        _, body = self._post_request("凤凰计划的排期发我")
        self.assertEqual(body["action"].get("status_code"), 451)   # l2 默认开
        _, body = self._post_request("我的 key 是 TESTA1234 请收好")
        self.assertEqual(body["action"].get("status_code"), 451)   # l1 默认开
        _, body = self._post_response("好的，凤凰计划的排期如下")
        self.assertEqual(body["action"].get("status_code"), 451)   # response 默认开

    def test_mask_response_body_layer_flags(self):
        """mask_response_body 分层参数（issue #40）：l1 关跳过 secrets/格式 PII；l2 关跳过词表。"""
        payload = {"choices": [{"message": {"role": "assistant",
                                            "content": "TESTA1234 与 凤凰计划"}}]}
        _, hits = shim_app.mask_response_body(payload)
        self.assertIn("secrets.test_a", hits)
        self.assertIn("confidential.fenghuang", hits)
        _, hits = shim_app.mask_response_body(payload, l1_enabled=False)
        self.assertNotIn("secrets.test_a", hits)
        self.assertIn("confidential.fenghuang", hits)
        _, hits = shim_app.mask_response_body(payload, l2_enabled=False)
        self.assertIn("secrets.test_a", hits)
        self.assertNotIn("confidential.fenghuang", hits)

    def test_layer_switch_warn_on_state_change(self):
        """开关可观测（review）：状态变化打一行 [settings] warn，稳态请求不重复刷。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self._post_request("帮我写周报")                     # 首次观测：全开 → 不打
            self._fixture["l1"]["enabled"] = False
            self._write_settings()
            self._post_request("帮我写周报")                     # true→false：打一行撤防 warn
            self._post_request("帮我写周报")                     # 稳态：不再重复打
            self._fixture["l1"]["enabled"] = True
            self._write_settings()
            self._post_request("帮我写周报")                     # false→true：打一行恢复
        out = buf.getvalue()
        self.assertEqual(out.count("[settings] l1.enabled=false，格式规则层已撤防（密钥拦截敞口）"), 1)
        self.assertEqual(out.count("[settings] l1.enabled=true，格式规则层恢复生效"), 1)

    def test_settings_put_l1_flip_triggers_render(self):
        """l1 翻转联动渲染（issue #40）：PUT settings 关 l1 → config.yaml 标记区块渲染为空；
        再开 → 区块恢复渲染；settings 落盘值随之翻转。"""
        doc = json.loads(json.dumps(self._fixture))
        doc["l1"]["enabled"] = False
        status, body = _request("PUT", "/dlp-admin/settings", token="writer-token", payload=doc)
        self.assertEqual(status, 200, body)
        cfg = self._read_config()
        self.assertIn("DLP-FORMAT-RULES BEGIN", cfg)
        self.assertIn("DLP-FORMAT-RULES END", cfg)
        self.assertNotIn("TESTA", cfg)              # 标记区块渲染为空
        self.assertIn("host: shim:8080", cfg)       # 区块外不动
        with open(self.settings_path, encoding="utf-8") as f:
            self.assertFalse(json.load(f)["l1"]["enabled"])
        # 再开 → 恢复渲染
        doc["l1"]["enabled"] = True
        status, body = _request("PUT", "/dlp-admin/settings", token="writer-token", payload=doc)
        self.assertEqual(status, 200, body)
        self.assertIn("- pattern: 'TESTA[0-9]{4}'", self._read_config())

    def test_settings_put_l1_flip_render_failure_rollback(self):
        """l1 翻转但 config.yaml 缺标记 → 500 且 settings 回滚（两侧不留半更新）。"""
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write("routes: []\n")
        doc = json.loads(json.dumps(self._fixture))
        doc["l1"]["enabled"] = False
        status, body = _request("PUT", "/dlp-admin/settings", token="writer-token", payload=doc)
        self.assertEqual(status, 500)
        self.assertIn("已回滚", body.get("error", ""))
        with open(self.settings_path, encoding="utf-8") as f:
            self.assertTrue(json.load(f)["l1"]["enabled"])  # 回滚为写前 true

    def test_render_endpoints_respect_l1_off(self):
        """render 端点 + 格式规则保存路径（issue #40 AC）：l1 关时两者渲染均排除整族规则。"""
        self._fixture["l1"]["enabled"] = False
        self._write_settings()
        status, body = _request("POST", "/dlp-admin/format-rules/render", token="writer-token")
        self.assertEqual(status, 200, body)
        self.assertEqual(body.get("rendered"), 0)
        self.assertNotIn("TESTA", self._read_config())
        # 格式规则保存路径（PUT format-rules）同样排除
        status, body = _request("PUT", "/dlp-admin/format-rules",
                                token="writer-token", payload=_FR_FIXTURE)
        self.assertEqual(status, 200, body)
        self.assertNotIn("TESTA", self._read_config())


if __name__ == "__main__":
    unittest.main()
