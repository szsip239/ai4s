#!/usr/bin/env python3
"""PromptGuard 2 注入/越狱检测引擎（issue #67：promptguard 服务并入 shim 进程内）。

平移自原 promptguard/app.py（issue #30 实测 + issue #44 归一化），打分语义不变：
score(text) → MALICIOUS 概率（meta-llama/Llama-Prompt-Guard-2-86M，int8 ONNX，CPU，低内存）。
模型本地目录离线加载（PG_MODEL_DIR，默认 /models/promptguard，compose 挂卷 + HF_HUB_OFFLINE=1）。

issue #49 纪律：onnxruntime/transformers/numpy 全部函数级懒加载——
pg.enabled=false 时 shim 不 import 本模块，检测路径零额外依赖/零启动开销；
模型首次打分时懒加载（shim 启动时间不因 PG 开关变化）。

__main__ 为 jsonl REPL（评估/调试直测用，injection-eval 经 `docker exec -i ai4s-shim
python3 pg_engine.py` 调用）：stdin 每行 {"text": ..., "normalize": true?} →
stdout 每行 {"malicious": 0.87}；normalize 字段透传（issue #95）：true 时先归一化
再打分，对齐链路 pg.normalize 口径，缺省/false 保持 raw 行为。
"""
import base64
import json
import os
import re
import threading
import unicodedata

MODEL_DIR = os.environ.get("PG_MODEL_DIR", "/models/promptguard")
_sess = None
_tok = None
_mal_idx = 1
_model_lock = threading.Lock()  # 首载互斥（issue #103）：锁非线程，合规 import 不起线程纪律

# 归一化（issue #44）：零宽字符清除 + NFKC（全角→半角）+ base64 形 token 可解码为
# 可打印文本时内联替换（append 变体被 512 token 截断稀释，实测 inline 才翻盘）。
# 只改打分输入——引擎只见打分文本，转发原文天然不受影响。
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


def _get_model():
    """模型懒加载（函数级 import 重依赖）：首次打分加载，进程内单例。
    双重检查锁（issue #103）：阻断模式开时同步打分跑在请求线程，
    无锁则并发首批可双载模型（~1.66GiB/份，2.8GB Docker VM OOM 风险）。"""
    global _sess, _tok, _mal_idx
    if _sess is None:
        with _model_lock:
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


def score(text: str) -> float:
    """MALICIOUS 概率（0-1，round 4 位对齐原 /guard 响应语义）。
    加载/推理异常抛给调用方（shim pg_guard fail-open 兜底）。"""
    import numpy as np
    sess, tok, mi = _get_model()
    enc = tok(text, truncation=True, max_length=512, return_tensors="np")
    accepted = {i.name for i in sess.get_inputs()}
    inputs = {k: v for k, v in enc.items() if k in accepted}
    logits = sess.run(None, inputs)[0][0]
    exp = np.exp(logits - logits.max())
    return round(float(exp[mi] / exp.sum()), 4)


def repl_main():
    """jsonl REPL 主循环（独立函数便于打桩单测，issue #95）。"""
    import sys

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            # 与生产路径（shim/app.py pg_guard 检测调用点）对齐：先截 4000 字符再打分，长样本口径不分叉
            text = req.get("text", "")[:4000]
            # normalize 字段透传（issue #95）：true 时先归一化再打分（对齐链路 pg.normalize=true）
            if req.get("normalize") is True:
                text = normalize_for_scoring(text)
            print(json.dumps({"malicious": score(text)}), flush=True)
        except Exception as e:
            print(json.dumps({"error": f"{type(e).__name__}: {str(e)[:200]}"}), flush=True)


if __name__ == "__main__":
    repl_main()
