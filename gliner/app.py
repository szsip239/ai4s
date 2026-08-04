#!/usr/bin/env python3
"""GLiNER 商密实体抽取服务（issue #26 实测）。

POST /analyze {"text": "...", "labels": ["internal project codename", ...]}
→ {"entities": [{"text", "label", "score", "start", "end"}]}

模型 urchade/gliner_multi-v2.1（Apache-2.0，209M）：零样本 NER，label 推理时传入。
首次启动从 HF 下载模型（约 800MB）到挂载卷。CPU 运行。
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_NAME = os.environ.get("GLINER_MODEL", "/models/gliner-onnx")  # 本地目录（宿主机预下载挂载），离线加载
ONNX_PATH = os.environ.get("GLINER_ONNX_PATH", "/models/gliner-onnx/onnx/model_q4.onnx")
_model = None


def get_model():
    global _model
    if _model is None:
        from gliner import GLiNER
        # ONNX 量化版：torch 路径加载峰值超本机 Docker VM 余量（实测 OOM 137），onnxruntime 峰值低得多
        _model = GLiNER.from_pretrained(MODEL_NAME, local_files_only=True,
                                        load_onnx_model=True, onnx_model_path=ONNX_PATH)
    return _model


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._json(200 if self.path == "/healthz" else 404, {"ok": self.path == "/healthz"})

    def do_POST(self):
        if self.path != "/analyze":
            self._json(404, {})
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 256 * 1024)
            payload = json.loads(self.rfile.read(length) or b"{}")
            text = payload.get("text", "")
            labels = payload.get("labels") or []
            threshold = float(payload.get("threshold", 0.5))
            if not text or not labels:
                self._json(200, {"entities": []})
                return
            preds = get_model().predict_entities(text, labels, threshold=threshold)
            self._json(200, {"entities": [
                {"text": p["text"], "label": p["label"], "score": round(p["score"], 4),
                 "start": p["start"], "end": p["end"]} for p in preds
            ]})
        except Exception as e:
            self._json(500, {"error": str(e)[:200]})


if __name__ == "__main__":
    get_model()  # 启动即加载（含首次下载），避免首请求阻塞
    print("gliner service ready", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
