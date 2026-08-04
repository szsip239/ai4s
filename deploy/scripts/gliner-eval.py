#!/usr/bin/env python3
"""ai4s GLiNER 商密 NER 评估（issue #26）。

直测 gliner 服务 /analyze，样本同 #21（semantic-vectors + #20 残余 bypass），
按阈值 0.3/0.5/0.7 扫描检出率与误报率，回答"GLiNER 能否替代 LLM judge"。

用法：cd deploy && docker compose --profile gliner up -d --build gliner && python3 scripts/gliner-eval.py
"""
import json
import os
import time
import urllib.request

DEPLOY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GLINER = os.environ.get("GLINER_URL", "http://localhost:18090")
LABELS = ["internal project codename", "unreleased product", "internal system or domain"]
THRESHOLDS = [0.3, 0.5, 0.7]


def analyze(text, threshold):
    body = json.dumps({"text": text, "labels": LABELS, "threshold": threshold}).encode()
    req = urllib.request.Request(GLINER + "/analyze", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    return d.get("entities", []), round((time.time() - t0) * 1000)


def main():
    sem = json.load(open(os.path.join(DEPLOY_DIR, "tests", "semantic-vectors.json"), encoding="utf-8"))["vectors"]
    dlp = json.load(open(os.path.join(DEPLOY_DIR, "tests", "dlp-vectors.json"), encoding="utf-8"))["vectors"]
    bypass = [v for v in dlp if v.get("expect") == "pass" and v.get("note")]

    groups = [("semantic_novel", [v for v in sem if v["expect"] == "confidential"]),
              ("bypass 残余（谐音/拼音/base64）", bypass),
              ("negative_tricky", [v for v in sem if v["expect"] == "clean"])]

    # 每条样本在每个阈值下的命中情况
    results = {}
    lat = []
    for label, vecs in groups:
        print(f"\n===== {label}（{len(vecs)} 条）=====")
        for v in vecs:
            per_th = {}
            ms = 0
            for th in THRESHOLDS:
                ents, ms = analyze(v["content"], th)
                per_th[th] = ents
            lat.append(ms)
            results[v["name"]] = (v["expect"], per_th)
            brief = " | ".join(f"t{th}:{len(per_th[th])}实体" for th in THRESHOLDS)
            top = per_th[0.5][0]["text"] if per_th[0.5] else "-"
            print(f"{v['name']}: expect={v['expect']} | {brief} | top={top} | {ms}ms")

    print("\n===== 阈值扫描汇总 =====")
    for th in THRESHOLDS:
        lines = []
        for label, vecs in groups:
            hit = miss = 0
            for v in vecs:
                ents = results[v["name"]][1][th]
                got = "confidential" if ents else "clean"
                if got == v["expect"]:
                    hit += 1
                else:
                    miss += 1
            tag = "检出" if "negative" not in label else "不误报"
            lines.append(f"{label} {tag} {hit}/{hit + miss}")
        print(f"阈值 {th}: " + " | ".join(lines))

    if lat:
        lat.sort()
        print(f"\n延迟：p50={lat[len(lat) // 2]}ms p95={lat[min(len(lat) - 1, int(len(lat) * 0.95))]}ms（n={len(lat)}）")


if __name__ == "__main__":
    main()
