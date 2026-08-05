#!/usr/bin/env python3
"""PromptGuard 2 注入/越狱检测服务（issue #30 实测）。

POST /guard {"text": "..."} → {"malicious": 0.87}
score = MALICIOUS 概率（meta-llama/Llama-Prompt-Guard-2-86M，int8 ONNX，CPU，低内存）。
本地目录离线加载（/models/promptguard）。
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_DIR = os.environ.get("PG_MODEL_DIR", "/models/promptguard")
_sess = None
_tok = None
_mal_idx = 1


def get_model():
    global _sess, _tok, _mal_idx
    if _sess is None:
        import onnxruntime as ort
        from transformers import AutoTokenizer
        cfg = json.load(open(os.path.join(MODEL_DIR, "config.json"), encoding="utf-8"))
        id2label = {int(k): v.upper() for k, v in (cfg.get("id2label") or {}).items()}
        for i, lab in id2label.items():
            if "MALICIOUS" in lab:
                _mal_idx = i
        _tok = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
        _sess = ort.InferenceSession(os.path.join(MODEL_DIR, "model.quant.onnx"),
                                     providers=["CPUExecutionProvider"])
    return _sess, _tok, _mal_idx


def guard(text: str) -> float:
    import numpy as np
    sess, tok, mi = get_model()
    enc = tok(text, truncation=True, max_length=512, return_tensors="np")
    accepted = {i.name for i in sess.get_inputs()}
    inputs = {k: v for k, v in enc.items() if k in accepted}
    logits = sess.run(None, inputs)[0][0]
    exp = np.exp(logits - logits.max())
    return float(exp[mi] / exp.sum())


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
        if self.path != "/guard":
            self._json(404, {})
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 256 * 1024)
            payload = json.loads(self.rfile.read(length) or b"{}")
            self._json(200, {"malicious": round(guard(payload.get("text", "")[:4000]), 4)})
        except Exception as e:
            self._json(500, {"error": str(e)[:200]})


if __name__ == "__main__":
    get_model()
    print("promptguard service ready", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8092), Handler).serve_forever()
