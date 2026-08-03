#!/usr/bin/env python3
"""ai4s 商密语义层评估（issue #21，shadow 期）。

经 shim /judge-test 直测 judge（当前=deepseek-v4-flash，合成样本；生产须换内网模型）：
  1. semantic_novel：不含词表原词的语义涉密样本（judge 的增量价值）
  2. bypass 挽回：#20 中词表/regex 层 100% 绕过的 12 条变形样本（expect=confidential）
  3. negative_tricky：含词表部分字/相关话题但不涉密（误报率）

输出各组检出率、负例误报率、延迟 p50/p95。测量工具，退出码恒 0。
用法：cd deploy && python3 scripts/semantic-eval.py
"""
import json
import os
import sys
import time
import urllib.request

DEPLOY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIM = os.environ.get("SHIM_URL", "http://localhost:18080")


def judge(text):
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(SHIM + "/judge-test", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        return d.get("verdict"), d.get("latency_ms", 0)
    except Exception as e:
        return None, 0


def main():
    sem = json.load(open(os.path.join(DEPLOY_DIR, "tests", "semantic-vectors.json"), encoding="utf-8"))["vectors"]
    dlp = json.load(open(os.path.join(DEPLOY_DIR, "tests", "dlp-vectors.json"), encoding="utf-8"))["vectors"]
    bypass = [{"name": v["name"], "content": v["content"], "expect": "confidential"}
              for v in dlp if v.get("expect") == "pass" and v.get("note")]

    groups = [("semantic_novel", [v for v in sem if v["expect"] == "confidential"]),
              ("bypass 挽回（#20 变形样本）", bypass),
              ("negative_tricky", [v for v in sem if v["expect"] == "clean"])]

    lat = []
    for label, vecs in groups:
        hit = miss = err = 0
        print(f"\n===== {label}（{len(vecs)} 条）=====")
        for v in vecs:
            verdict, ms = judge(v["content"])
            if ms:
                lat.append(ms)
            if verdict is None:
                err += 1
                print(f"[ERR ] {v['name']}: judge 不可用")
                continue
            got = "confidential" if verdict["confidential"] else "clean"
            ok = got == v["expect"]
            if ok:
                hit += 1
            else:
                miss += 1
            print(f"[{'OK ' if ok else 'MISS'}] {v['name']}: expect={v['expect']} got={got}"
                  f" conf={verdict['confidence']:.2f} {ms}ms")
        total = hit + miss
        rate = hit / total if total else 0
        tag = "检出率" if "negative" not in label else "不误报率"
        print(f"--- {label} {tag}: {hit}/{total} = {rate:.0%}（judge 异常 {err}）")

    if lat:
        lat.sort()
        p50 = lat[len(lat) // 2]
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
        print(f"\n延迟：p50={p50}ms p95={p95}ms max={lat[-1]}ms（n={len(lat)}）")
    sys.exit(0)


if __name__ == "__main__":
    main()
