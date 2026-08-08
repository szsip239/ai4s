#!/usr/bin/env python3
"""shim admin 平面（issue #31 骨架）接口行为 + 原子写工具测试。

seam 纪律：
- HTTP 接口：进程内起真实 ThreadingHTTPServer 跑 app.Handler（含 admin dispatch 挂接）；
  axonhub 内省只在线路边界用本地假 HTTP 服务顶替，不 mock shim 内部函数。
- 原子写：直接测 admin_api.write_json_atomic。
"""
import json
import os
import sys
import tempfile
import threading
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

    def tearDown(self):
        admin_api.FORMAT_RULES_PATH, admin_api.AGENTGW_CONFIG_PATH = self._saved_paths
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


if __name__ == "__main__":
    unittest.main()
