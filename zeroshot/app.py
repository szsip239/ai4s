#!/usr/bin/env python3
"""zero-shot 主题分类服务（issue #27 实测）。

POST /classify {"text": "...", "hypothesis": "这段文本涉及……"} → {"score": 0.87}
score = NLI entailment 概率（onnxruntime + int8 量化模型，CPU，低内存峰值）。

模型 MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7（多语 NLI，280M），
本地目录离线加载（/models/zeroshot）。
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_DIR = os.environ.get("ZS_MODEL_DIR", "/models/zeroshot")
_sess = None
_tok = None
_entail_idx = 2  # 默认 contr/neutral/entail；启动时从 config.json 校准


def get_model():
    global _sess, _tok, _entail_idx
    if _sess is None:
        import numpy as np  # noqa: F401
        import onnxruntime as ort
        from transformers import AutoTokenizer
        cfg = json.load(open(os.path.join(MODEL_DIR, "config.json"), encoding="utf-8"))
        id2label = {int(k): v.lower() for k, v in (cfg.get("id2label") or {}).items()}
        for i, lab in id2label.items():
            if "entail" in lab:
                _entail_idx = i
        _tok = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
        _sess = ort.InferenceSession(os.path.join(MODEL_DIR, "model_quantized.onnx"),
                                     providers=["CPUExecutionProvider"])
    return _sess, _tok, _entail_idx


def classify(text: str, hypothesis: str) -> float:
    import numpy as np
    sess, tok, ei = get_model()
    enc = tok(text, hypothesis, truncation=True, max_length=512, return_tensors="np")
    accepted = {i.name for i in sess.get_inputs()}  # 该 ONNX 导出无 token_type_ids，按实际输入名单过滤
    inputs = {k: v for k, v in enc.items() if k in accepted}
    logits = sess.run(None, inputs)[0][0]
    exp = np.exp(logits - logits.max())
    return float(exp[ei] / exp.sum())


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
        if self.path != "/classify":
            self._json(404, {})
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 256 * 1024)
            payload = json.loads(self.rfile.read(length) or b"{}")
            score = classify(payload.get("text", "")[:4000], payload.get("hypothesis", ""))
            self._json(200, {"score": round(score, 4)})
        except Exception as e:
            self._json(500, {"error": str(e)[:200]})


if __name__ == "__main__":
    get_model()
    print("zeroshot service ready", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8091), Handler).serve_forever()
