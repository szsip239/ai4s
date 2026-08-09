#!/usr/bin/env python3
"""PromptGuard 2 注入/越狱检测服务（issue #30 实测）。

POST /guard {"text": "..."} → {"malicious": 0.87}
POST /guard {"text": "...", "normalize": true} → 打分前先归一化（issue #44，默认关=现网行为）
score = MALICIOUS 概率（meta-llama/Llama-Prompt-Guard-2-86M，int8 ONNX，CPU，低内存）。
本地目录离线加载（/models/promptguard）。
"""
import base64
import json
import os
import re
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_DIR = os.environ.get("PG_MODEL_DIR", "/models/promptguard")
_sess = None
_tok = None
_mal_idx = 1

# 归一化（issue #44）：零宽字符清除 + NFKC（全角→半角）+ base64 形 token 可解码为
# 可打印文本时内联替换（append 变体被 512 token 截断稀释，实测 inline 才翻盘）。
# 只改打分输入——本服务只见打分文本，转发原文天然不受影响。
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\ufeff]")  # 显式转义写法，抗格式化工具吞不可见字符
_B64_TOKEN = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def normalize_for_scoring(text: str) -> str:
    """PG 打分前置归一化（纯函数，可单测）。
    已知边界（打分输入级可接受，均按现行为记录不处理）：
      - 嵌套 base64 不解（单趟解码，解码结果不再二次扫描）
      - U+00AD SOFT HYPHEN / U+2060 WORD JOINER 等其余不可见字符不清除
      - NFKC 兼容分解会把日文半角片假名转全角（CJK 全角标点亦转半角）"""
    text = _ZERO_WIDTH.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    for m in _B64_TOKEN.findall(text):
        try:
            s = base64.b64decode(m, validate=True).decode("utf-8")
        except Exception:
            continue
        if s and sum(ch.isprintable() or ch.isspace() for ch in s) / len(s) > 0.9:
            text = text.replace(m, s)
    return text


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
            text = payload.get("text", "")[:4000]
            if payload.get("normalize") is True:  # issue #44：请求级开关，默认关=现网行为
                text = normalize_for_scoring(text)
            self._json(200, {"malicious": round(guard(text), 4)})
        except Exception as e:
            self._json(500, {"error": str(e)[:200]})


if __name__ == "__main__":
    get_model()
    print("promptguard service ready", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8092), Handler).serve_forever()
