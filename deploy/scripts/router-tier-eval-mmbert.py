#!/usr/bin/env python3
"""auto 智能路由选型实测——候选 A：mmBERT32k 本地 CPU 判别头（issue #114 第一阶段）。

模型：llm-semantic-router/mmbert32k-intent-classifier-merged（Apache-2.0，ONNX fp32 变体）。
注意：该 org 无「complexity 简单/复杂」分类头，本脚本用 14 类意图分类头 + 类目→两档映射
（STEM/推理类→complex，日常类→simple），score = softmax 在 complex 类目集上的概率和，
阈值默认 0.5，供 RouteLLM 式阈值扫描复用（结果带每条样本的 score）。

运行（复用 shim/.venv，已含 transformers+onnxruntime，无需额外装包）：
  cd deploy && HF_HOME=../.scratch/issue-114/hf \
    ../shim/.venv/bin/python scripts/router-tier-eval-mmbert.py

结果写 .scratch/issue-114/results/mmbert-<时间戳>.json（含逐样本明细 + 汇总）。
HF 模型缓存默认 .scratch/issue-114/hf（约 1.2GB fp32 ONNX + 分词器）。
"""
import json
import os
import resource
import sys
import time
import platform

import numpy as np

DEPLOY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(DEPLOY_DIR)
VECTORS = os.path.join(DEPLOY_DIR, "tests", "router-tier-vectors.json")
RESULTS_DIR = os.environ.get("ROUTER_EVAL_OUT", os.path.join(ROOT, ".scratch", "issue-114", "results"))
HF_HOME = os.environ.get("HF_HOME", os.path.join(ROOT, ".scratch", "issue-114", "hf"))
os.environ["HF_HOME"] = HF_HOME

REPO_ID = "llm-semantic-router/mmbert32k-intent-classifier-merged"
MAX_LEN = 8192  # 生产上分类输入应截断；样本最长 ~4k 字，8192 token 足够

# 类目→两档映射（启发式，见报告 §候选 A 方法）：STEM/强推理类目→complex
COMPLEX_CATS = {"math", "computer science", "physics", "chemistry", "engineering",
                "biology", "economics", "law", "philosophy"}


def rss_mb():
    """macOS ru_maxrss 单位字节（Linux 为 KB，这里跑在 macOS）。"""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def percentile(xs, q):
    xs = sorted(xs)
    k = (len(xs) - 1) * q / 100
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def main():
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer
    import onnxruntime as ort

    t0 = time.time()
    model_dir = snapshot_download(
        REPO_ID,
        allow_patterns=["onnx/model.onnx", "tokenizer.json", "tokenizer_config.json",
                        "special_tokens_map.json", "config.json"],
    )
    dl_s = time.time() - t0

    rss0 = rss_mb()
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(model_dir)
    sess = ort.InferenceSession(os.path.join(model_dir, "onnx", "model.onnx"),
                                providers=["CPUExecutionProvider"])
    load_s = time.time() - t0
    rss1 = rss_mb()
    onnx_bytes = os.path.getsize(os.path.join(model_dir, "onnx", "model.onnx"))

    id2label = {int(k): v for k, v in json.load(open(os.path.join(model_dir, "config.json")))["id2label"].items()}
    complex_ids = {i for i, name in id2label.items() if name in COMPLEX_CATS}

    vectors = json.load(open(VECTORS, encoding="utf-8"))["vectors"]
    details = []
    for i, vec in enumerate(vectors):
        enc = tok(vec["content"], return_tensors="np", truncation=True, max_length=MAX_LEN)
        n_tok = int(enc["input_ids"].shape[1])
        t0 = time.perf_counter()
        logits = sess.run(None, {"input_ids": enc["input_ids"].astype(np.int64),
                                 "attention_mask": enc["attention_mask"].astype(np.int64)})[0][0]
        lat_ms = (time.perf_counter() - t0) * 1000
        e = np.exp(logits - logits.max())
        probs = e / e.sum()
        top1 = int(probs.argmax())
        p_complex = float(sum(probs[j] for j in complex_ids))
        details.append({
            "id": vec["id"], "category": vec["category"], "lang": vec["lang"],
            "expect": vec["expect"], "n_tokens": n_tok,
            "top1": id2label[top1], "top1_prob": round(float(probs[top1]), 4),
            "p_complex": round(p_complex, 4), "latency_ms": round(lat_ms, 2),
        })
        if (i + 1) % 25 == 0:
            print(f"[{i + 1}/{len(vectors)}]", flush=True)

    lats = [d["latency_ms"] for d in details]
    # 默认阈值 0.5 的混淆矩阵
    tp = sum(1 for d in details if d["expect"] == "complex" and d["p_complex"] >= 0.5)
    fp = sum(1 for d in details if d["expect"] == "simple" and d["p_complex"] >= 0.5)
    fn = sum(1 for d in details if d["expect"] == "complex" and d["p_complex"] < 0.5)
    tn = sum(1 for d in details if d["expect"] == "simple" and d["p_complex"] < 0.5)

    summary = {
        "model": REPO_ID, "onnx_file_bytes": onnx_bytes,
        "machine": {"platform": platform.platform(), "cpu": platform.processor(),
                    "cores": os.cpu_count(), "python": platform.python_version(),
                    "onnxruntime": ort.__version__},
        "download_s": round(dl_s, 1), "load_s": round(load_s, 1),
        "rss_before_mb": round(rss0, 1), "rss_after_load_mb": round(rss1, 1),
        "n_samples": len(details),
        "latency_ms": {"p50": round(percentile(lats, 50), 1),
                       "p95": round(percentile(lats, 95), 1),
                       "max": round(max(lats), 1)},
        "threshold_0.5": {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                          "accuracy": round((tp + tn) / len(details), 4),
                          "precision_complex": round(tp / (tp + fp), 4) if tp + fp else None,
                          "recall_complex": round(tp / (tp + fn), 4) if tp + fn else None},
        "complex_cats": sorted(COMPLEX_CATS),
    }
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = os.path.join(RESULTS_DIR, "mmbert-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    json.dump({"summary": summary, "details": details}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("结果:", out)


if __name__ == "__main__":
    main()
