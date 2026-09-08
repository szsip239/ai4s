#!/usr/bin/env python3
"""issue #139 部署耦合：shim 启动渲染 config.yaml 的 /classify extAuthz addRequestHeaders。

背景：/classify 挂共享密钥守卫（X-Shim-Local-Token 头）后，agentgateway extAuthz 子请求
必须注同值头，否则 shim 403 会被网关当 deny 决策直回客户端（failureMode=allow 只兜传输层），
/v1 全流量断流。config.yaml 是 git 入库文件、agentgateway File 模式不支持 env 展开，
故沿用 issue #33「渲染写回标记段」先例：shim 进程启动（HTTP server 之前）把 env
SHIM_LOCAL_TOKEN 渲染进 `>>> SHIM-LOCAL-TOKEN BEGIN/END` 标记段；env 空 → 段内渲染为空
（移除注入，守卫 fail-closed 语义一致，此时 /classify 恒 403）；渲染失败大声报错但不阻启动。

seam 同 #33 测试（test_admin_api.py）：临时 config.yaml + 覆写 admin_api.AGENTGW_CONFIG_PATH；
env 用 mock.patch.dict 隔离（渲染函数读 os.environ）。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402  # 渲染结果结构性断言（缩进错位即 parse 挂/落错位置）

import admin_api  # noqa: E402
import app as shim_app  # noqa: E402

# mini config fixture：只保留 /classify extAuthz protocol.http 段（12 空格基缩进与现网一致）
_CONFIG_FIXTURE = (
    "routes:\n"
    "  - gateways: [default]\n"
    "    policies:\n"
    "      extAuthz:\n"
    "        host: shim:8080\n"
    "        failureMode: allow\n"
    "        protocol:\n"
    "          http:\n"
    "            path: '\"/classify\"'\n"
    "            # >>> SHIM-LOCAL-TOKEN BEGIN（issue #139：shim 启动渲染，勿手改）>>>\n"
    "            # <<< SHIM-LOCAL-TOKEN END <<<\n"
    "            metadata:\n"
    "              resolved_model: 'response.headers[\"x-resolved-model\"]'\n"
    "    backends:\n"
    "      - host: axonhub:8090\n"
)


def _outside_marks(text: str):
    """标记段以外的行（BEGIN 行及之前 + END 行及之后）——段外不动性断言用。"""
    lines = text.splitlines(keepends=True)
    b = next(i for i, l in enumerate(lines) if l.strip().startswith(shim_app.LOCAL_TOKEN_BEGIN_MARK))
    e = next(i for i, l in enumerate(lines) if l.strip().startswith(shim_app.LOCAL_TOKEN_END_MARK))
    return lines[:b + 1] + lines[e:]


class RenderBlockTest(unittest.TestCase):
    """纯函数 render_local_token_block：token → 标记段内容（YAML 块文本）。"""

    def test_token_renders_add_request_headers(self):
        block = shim_app.render_local_token_block("abc123def")
        self.assertIn("addRequestHeaders:", block)
        # CEL 表达式形态：值是双引号字符串字面量，外层 YAML 单引号（上游 example 同款）
        self.assertIn("x-shim-local-token: '\"abc123def\"'", block)

    def test_indent_matches_protocol_http_children(self):
        # 12 空格基缩进（与 path:/metadata: 同层），头键再进 2 空格
        lines = shim_app.render_local_token_block("t0ken").splitlines()
        self.assertEqual(lines, [" " * 12 + "addRequestHeaders:",
                                 " " * 14 + "x-shim-local-token: '\"t0ken\"'"])

    def test_empty_token_renders_empty(self):
        self.assertEqual(shim_app.render_local_token_block(""), "")

    def test_unsafe_chars_rejected(self):
        # 单引号破 YAML 单引号串、双引号/反斜杠破 CEL 字符串、空白/不可打印破两者——
        # 宁可渲染失败大声报错，不静默产出坏配置（.env.example 建议 openssl rand -hex 天然安全）；
        # 可打印非 ASCII（如中文）在 YAML/CEL 双引号串里均合法，不在拒绝之列
        for bad in ['a"b', "a'b", "a\\b", "a b", "a\nb", "a\tb", "a\x00b"]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                shim_app.render_local_token_block(bad)


class SpliceTest(unittest.TestCase):
    """标记段 splice：幂等 / 段外不动 / 空渲染移除注入 / 缺标记报错。"""

    def test_splice_inserts_between_markers(self):
        out = shim_app.splice_local_token(_CONFIG_FIXTURE, "            addRequestHeaders:\n")
        self.assertIn("addRequestHeaders:", out)
        self.assertIn("SHIM-LOCAL-TOKEN BEGIN", out)
        self.assertIn("SHIM-LOCAL-TOKEN END", out)

    def test_idempotent(self):
        once = shim_app.splice_local_token(_CONFIG_FIXTURE, shim_app.render_local_token_block("tok"))
        twice = shim_app.splice_local_token(once, shim_app.render_local_token_block("tok"))
        self.assertEqual(once, twice)

    def test_empty_block_removes_injection(self):
        injected = shim_app.splice_local_token(_CONFIG_FIXTURE, shim_app.render_local_token_block("tok"))
        removed = shim_app.splice_local_token(injected, shim_app.render_local_token_block(""))
        self.assertNotIn("x-shim-local-token", removed)
        # 标记行保留：下次配了 token 启动还能渲回去
        self.assertIn("SHIM-LOCAL-TOKEN BEGIN", removed)
        self.assertIn("SHIM-LOCAL-TOKEN END", removed)

    def test_surrounding_content_untouched(self):
        out = shim_app.splice_local_token(_CONFIG_FIXTURE, shim_app.render_local_token_block("tok"))
        self.assertEqual(_outside_marks(out), _outside_marks(_CONFIG_FIXTURE))

    def test_missing_markers_raises(self):
        with self.assertRaises(ValueError):
            shim_app.splice_local_token("routes: []\n", "x\n")


class RenderToGatewayTest(unittest.TestCase):
    """文件级渲染：临时 config.yaml + 覆写 AGENTGW_CONFIG_PATH（#33 同款 seam）。"""

    def setUp(self):
        d = tempfile.mkdtemp(prefix="ai4s-token-render-")
        self.addCleanup(shutil.rmtree, d, True)
        self.config_path = os.path.join(d, "config.yaml")
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(_CONFIG_FIXTURE)
        self._orig_path = admin_api.AGENTGW_CONFIG_PATH
        admin_api.AGENTGW_CONFIG_PATH = self.config_path
        self.addCleanup(setattr, admin_api, "AGENTGW_CONFIG_PATH", self._orig_path)

    def _read(self) -> str:
        with open(self.config_path, encoding="utf-8") as f:
            return f.read()

    def test_writes_injected_header(self):
        with mock.patch.dict(os.environ, {"SHIM_LOCAL_TOKEN": "0123abcdhex"}):
            err = shim_app.render_local_token_to_gateway()
        self.assertIsNone(err)
        cfg = self._read()
        self.assertIn("x-shim-local-token: '\"0123abcdhex\"'", cfg)
        # 结构性断言：YAML 可解析且 addRequestHeaders 落在 extAuthz protocol.http 下
        # （缩进错位会在这一步挂掉或落到别的层级）
        doc = yaml.safe_load(cfg)
        http = doc["routes"][0]["policies"]["extAuthz"]["protocol"]["http"]
        self.assertEqual(http["addRequestHeaders"]["x-shim-local-token"], '"0123abcdhex"')
        self.assertEqual(http["path"], '"/classify"')  # 既有键不受影响（YAML 值为 CEL 表达式原文）
        self.assertIn("resolved_model", http["metadata"])

    def test_unconfigured_env_removes_injection(self):
        with mock.patch.dict(os.environ, {"SHIM_LOCAL_TOKEN": "tok"}):
            shim_app.render_local_token_to_gateway()
        with mock.patch.dict(os.environ, {"SHIM_LOCAL_TOKEN": ""}):
            err = shim_app.render_local_token_to_gateway()
        self.assertIsNone(err)
        cfg = self._read()
        self.assertNotIn("x-shim-local-token", cfg)
        self.assertIn("SHIM-LOCAL-TOKEN BEGIN", cfg)

    def test_env_unset_same_as_empty(self):
        # env 缺失（非空串）同样渲染为空——守卫 fail-closed 语义一致
        with mock.patch.dict(os.environ, {}, clear=True):
            err = shim_app.render_local_token_to_gateway()
        self.assertIsNone(err)
        self.assertNotIn("x-shim-local-token", self._read())

    def test_missing_marker_loud_no_write(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write("routes: []\n")
        with mock.patch.dict(os.environ, {"SHIM_LOCAL_TOKEN": "tok"}):
            err = shim_app.render_local_token_to_gateway()
        self.assertIsNotNone(err)
        self.assertIn("SHIM-LOCAL-TOKEN", err)
        self.assertEqual(self._read(), "routes: []\n")  # 未落盘

    def test_no_change_no_rewrite(self):
        # 幂等加强：同值重渲染不落盘（避免每次启动滚动 .bak）
        with mock.patch.dict(os.environ, {"SHIM_LOCAL_TOKEN": "tok"}):
            self.assertIsNone(shim_app.render_local_token_to_gateway())
            mtime = os.stat(self.config_path).st_mtime_ns
            bak_mtime = os.stat(self.config_path + ".bak").st_mtime_ns  # 首写按原子写纪律留了 .bak
            self.assertIsNone(shim_app.render_local_token_to_gateway())
        self.assertEqual(os.stat(self.config_path).st_mtime_ns, mtime)
        self.assertEqual(os.stat(self.config_path + ".bak").st_mtime_ns, bak_mtime)


if __name__ == "__main__":
    unittest.main()
