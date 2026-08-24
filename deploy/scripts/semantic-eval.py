#!/usr/bin/env python3
"""ai4s 商密语义层评估（issue #21 建，issue #99 门禁化 + 样本真实化 v2）。

经 shim /judge-test 直测 judge（当前=gpt-5.6-luna via axonhub；生产须换内网模型）：
  1. semantic_novel：真实化业务语料（代号自然使用 + 指代暗示，judge 的增量价值）
  2. bypass 挽回：#20 中词表/regex 层绕过的变形样本（自动并自 dlp-vectors.json，expect=confidential）
  3. negative_tricky：同名他物/相近话题但不涉密（误报率）

水位门禁（issue #99，纳入 dlp 回归）：三组水位数字 + judge 异常率由纯函数 evaluate_gate 判定，
门禁线按实测基线（docs/tests/2026-08-24-semantic-baseline.md）略留余量设定，防回归不设不可能标准。
退出码：0=达标，1=水位不达标，2=judge 不可用率超线（judge 全挂时门禁必须非零，不许绿）。
判定逻辑单测：deploy/scripts/tests/test_semantic_eval.py。

用法：cd deploy && python3 scripts/semantic-eval.py [--json out.json]
"""
import json
import os
import sys
import time
import urllib.request

DEPLOY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIM = os.environ.get("SHIM_URL", "http://localhost:18080")

# 水位门禁（issue #99）：基线见 docs/tests/2026-08-24-semantic-baseline.md，取略低于实测水位的保守值
# novel 实测 14/14 → 线 12（留 2 条余量）；bypass 实测 3/9、可检出上限 4（issue #107 前置单趟解码后
# base64 凤凰计划样本 conf 1.00 检出；sk-ant base64 仍 MISS——解码管线已闭合，残余根因在 judge
# prompt 语义，挂账 follow-up；组内 5 条 #106 拼接雷负例是合并口径带入的永久 MISS）→ 线 3；
# 误报实测 0/15 → 线 ≤1（judge 是 LLM，留 1 条抖动余量，稳定后可收紧为 0）；
# judge 异常率 >20%（或零调用）exit 2——门禁不得因 judge 不可用而绿
GATE_MIN_NOVEL_HIT = 12
GATE_MIN_BYPASS_HIT = 3
GATE_MAX_NEG_FP = 1
GATE_MAX_ERR_RATE = 0.2


def judge(text):
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(SHIM + "/judge-test", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        return d.get("verdict"), d.get("latency_ms", 0)
    except Exception:
        return None, 0


def evaluate_gate(novel, bypass, negative, err, calls):
    """水位门禁判定（issue #99，纯函数便于单测）：三组水位数字 + judge 异常数 → (退出码, 判定行)。

    novel/bypass=(hit, total)，negative=(fp, total)。退出码：0=达标，1=水位不达标，
    2=judge 不可用率超线或零调用（judge 挂了门禁必须非零，不许绿）。"""
    if calls <= 0 or err / calls > GATE_MAX_ERR_RATE:
        rate = "n/a" if calls <= 0 else f"{err / calls:.0%}"
        return 2, [f"judge 异常 {err}/{calls}（{rate}，要求 ≤ {GATE_MAX_ERR_RATE:.0%}）"]
    checks = [
        ("semantic_novel 检出", novel[0], novel[1], f"≥ {GATE_MIN_NOVEL_HIT}", novel[0] >= GATE_MIN_NOVEL_HIT),
        ("bypass 挽回检出", bypass[0], bypass[1], f"≥ {GATE_MIN_BYPASS_HIT}", bypass[0] >= GATE_MIN_BYPASS_HIT),
        ("negative 误报", negative[0], negative[1], f"≤ {GATE_MAX_NEG_FP}", negative[0] <= GATE_MAX_NEG_FP),
    ]
    lines = [f"{label} {num}/{den}（要求 {req}）{'OK' if ok else 'FAIL'}"
             for label, num, den, req, ok in checks]
    if err:
        lines.append(f"judge 异常 {err}/{calls}（≤ {GATE_MAX_ERR_RATE:.0%} 线内，仅记录）")
    return (0 if all(c[4] for c in checks) else 1), lines


def main():
    out_json = "--json" in sys.argv
    out_path = sys.argv[sys.argv.index("--json") + 1] if out_json else None

    sem = json.load(open(os.path.join(DEPLOY_DIR, "tests", "semantic-vectors.json"), encoding="utf-8"))["vectors"]
    dlp = json.load(open(os.path.join(DEPLOY_DIR, "tests", "dlp-vectors.json"), encoding="utf-8"))["vectors"]
    bypass = [{"name": v["name"], "content": v["content"], "expect": "confidential"}
              for v in dlp if v.get("expect") == "pass" and v.get("note")]

    groups = [("semantic_novel", [v for v in sem if v["expect"] == "confidential"]),
              ("bypass 挽回（#20 变形样本）", bypass),
              ("negative_tricky", [v for v in sem if v["expect"] == "clean"])]

    lat = []
    records = []
    calls = err = 0
    gstats = {}  # label → (门禁数, 判定总数)：novel/bypass=命中，negative=误报
    for label, vecs in groups:
        hit = miss = gerr = fp = 0
        print(f"\n===== {label}（{len(vecs)} 条）=====")
        for v in vecs:
            verdict, ms = judge(v["content"])
            calls += 1
            if ms:
                lat.append(ms)
            if verdict is None:
                err += 1
                gerr += 1
                print(f"[ERR ] {v['name']}: judge 不可用")
                records.append({"name": v["name"], "group": label, "expect": v["expect"], "got": None})
                continue
            got = "confidential" if verdict["confidential"] else "clean"
            ok = got == v["expect"]
            if ok:
                hit += 1
            else:
                miss += 1
                if v["expect"] == "clean":
                    fp += 1
            print(f"[{'OK ' if ok else 'MISS'}] {v['name']}: expect={v['expect']} got={got}"
                  f" conf={verdict['confidence']:.2f} {ms}ms")
            records.append({"name": v["name"], "group": label, "expect": v["expect"], "got": got,
                            "confidence": verdict["confidence"], "latency_ms": ms})
        total = hit + miss
        rate = hit / total if total else 0
        tag = "检出率" if "negative" not in label else "不误报率"
        print(f"--- {label} {tag}: {hit}/{total} = {rate:.0%}（judge 异常 {gerr}）")
        # 门禁 stats：novel/bypass=(命中, 判定总数)，negative=(误报, 判定总数)
        gstats[label] = (fp if "negative" in label else hit, total)

    if lat:
        lat.sort()
        p50 = lat[len(lat) // 2]
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
        print(f"\n延迟：p50={p50}ms p95={p95}ms max={lat[-1]}ms（n={len(lat)}）")

    print("\n===== 水位门禁（issue #99）=====")
    code, lines = evaluate_gate(gstats["semantic_novel"], gstats["bypass 挽回（#20 变形样本）"],
                                gstats["negative_tricky"], err, calls)
    for line in lines:
        print(line)
    print(f"门禁 → {'PASS' if code == 0 else f'FAIL（exit {code}）'}")

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"date": time.strftime("%Y-%m-%d"), "gate_exit": code, "records": records},
                      f, ensure_ascii=False, indent=1)
        print(f"JSON 已写 {out_path}")
    sys.exit(code)


if __name__ == "__main__":
    main()
