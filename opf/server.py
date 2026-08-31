#!/usr/bin/env python3
"""privacy-filter（OPF）HTTP sidecar（issue #127）：shim L2 第二检测器的线路边界。

  POST /analyze  {"text": "..."} → {"spans": [{"label", "start", "end", "text"}]}
  GET  /healthz                  → {"ok": true}（模型预载完成后才算就绪）

模型 checkpoint：OPF_CHECKPOINT env 指向挂载卷目录；缺省库内路径 ~/.opf/privacy_filter
（首启自动下载）。文本不落日志（对齐 shim 契约）。仅监听 compose 内网，不暴露宿主端口。
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opf._api import OPF

# 防御上限（shim 侧已按 l2.opf.max_chars 截断，此处兜底畸形直连）
_MAX_TEXT = int(os.environ.get("OPF_MAX_TEXT", "65536"))
_PORT = int(os.environ.get("OPF_PORT", "8081"))

_opf = None
_lock = threading.Lock()  # torch CPU 推理串行化（线程安全 + 防并发内存翻倍）


def _engine() -> OPF:
    global _opf
    if _opf is None:
        _opf = OPF(device=os.environ.get("OPF_DEVICE", "cpu"), output_mode="typed")
        _opf.get_runtime()  # 预载模型
    return _opf


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默（文本不落日志，契约）
        pass

    def _json(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._json(200, {"ok": True})
            return
        self.send_error(404)

    def do_POST(self):
        if self.path != "/analyze":
            self.send_error(404)
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 4 * 1024 * 1024)
            payload = json.loads(self.rfile.read(length) or b"{}")
            text = payload.get("text")
            if not isinstance(text, str):
                self._json(400, {"error": "text 必须是字符串"})
                return
            text = text[:_MAX_TEXT]
            if not text:
                self._json(200, {"spans": []})
                return
            with _lock:
                res = _engine().redact(text)
            spans = [{"label": s.label, "start": s.start, "end": s.end, "text": s.text}
                     for s in res.detected_spans]
            self._json(200, {"spans": spans})
        except Exception as e:  # 模型/解码异常 → 500（shim 侧 fail-open 放行）
            self._json(500, {"error": type(e).__name__})


if __name__ == "__main__":
    _engine()  # 启动即预载（失败立崩，进程级就绪语义；healthz 先于流量可用）
    ThreadingHTTPServer(("0.0.0.0", _PORT), Handler).serve_forever()
