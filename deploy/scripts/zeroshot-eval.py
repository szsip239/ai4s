#!/usr/bin/env python3
"""ai4s zero-shot 主题分类评估（issue #27）。

直测 zeroshot 服务 /classify，样本同 #21/#26（语义指代 8 + 变形残余 4 + 负例 7），
阈值扫描 0.5/0.7/0.9，与 deepseek-flash judge（#21）、GLiNER（#26）三方对照。

用法：cd deploy && docker compose --profile zeroshot up -d --build zeroshot && python3 scripts/zeroshot-eval.py
"""
import json
import os
import time
import urllib.request

DEPLOY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZS = os.environ.get("ZEROSHOT_URL", "http://localhost:18091")
HYPOTHESIS = "这段文本涉及公司内部敏感信息（内部项目代号、未发布产品、内部系统或域名）"
THRESHOLDS = [0.5, 0.7, 0.9]


def classify(text):
    body = json.dumps({"text": text, "hypothesis": HYPOTHESIS}).encode()
    req = urllib.request.Request(ZS + "/classify", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    return d["score"], round((time.time() - t0) * 1000)


def main():
    sem = json.load(open(os.path.join(DEPLOY_DIR, "tests", "semantic-vectors.json"), encoding="utf-8"))["vectors"]
    dlp = json.load(open(os.path.join(DEPLOY_DIR, "tests", "dlp-vectors.json"), encoding="utf-8"))["vectors"]
    bypass = [v for v in dlp if v.get("expect") == "pass" and v.get("note")]

    groups = [("semantic_novel", [v for v in sem if v["expect"] == "confidential"], True),
              ("bypass 残余", bypass, True),
              ("negative_tricky", [v for v in sem if v["expect"] == "clean"], False)]

    results = {}
    lat = []
    for label, vecs, _ in groups:
        print(f"\n===== {label}（{len(vecs)} 条）=====")
        for v in vecs:
            score, ms = classify(v["content"])
            lat.append(ms)
            results[v["name"]] = (v["expect"], score)
            print(f"{v['name']}: expect={v['expect']} score={score:.3f} {ms}ms")

    print("\n===== 阈值扫描汇总 =====")
    for th in THRESHOLDS:
        lines = []
        for label, vecs, positive in groups:
            hit = miss = 0
            for v in vecs:
                got_conf = results[v["name"]][1] >= th
                if got_conf == positive:
                    hit += 1
                else:
                    miss += 1
            tag = "检出" if positive else "不误报"
            lines.append(f"{label} {tag} {hit}/{hit + miss}")
        print(f"阈值 {th}: " + " | ".join(lines))

    if lat:
        lat.sort()
        print(f"\n延迟：p50={lat[len(lat) // 2]}ms p95={lat[min(len(lat) - 1, int(len(lat) * 0.95))]}ms（n={len(lat)}）")


if __name__ == "__main__":
    main()
