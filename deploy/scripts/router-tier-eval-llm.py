#!/usr/bin/env python3
"""auto 智能路由选型实测——候选 B：外部 LLM 分类器走 judge 既有通道（issue #114 第一阶段）。

经 axonhub OpenAI 兼容接口调 judge 模型（模型名以 shim settings 的 judge.model 为准，
当前=gpt-5.6-luna；生产 judge 超时 8s，评测放宽到 60s 以观察真实长尾）。
要求返回 JSON {"tier": "simple"|"complex"}；并发 2（对齐生产 judge.max_concurrency 预算）。

运行（stdlib only，无需 venv）：
  cd deploy && python3 scripts/router-tier-eval-llm.py
校准模式（输出连续分数 p_complex，供 RouteLLM 式阈值扫描）：
  cd deploy && ROUTER_EVAL_MODE=pcomplex python3 scripts/router-tier-eval-llm.py

结果写 .scratch/issue-114/results/llm-<时间戳>.json（逐样本明细 + 汇总 + token/成本估算）。
成本口径：deploy/pricing.json official_prices_per_million_usd[gpt-5.6-luna]
（prompt $0.2/M、completion $1.2/M；1 credit = $1 官方原价，issue #18）。
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dlp_testkit  # noqa: E402

VECTORS = os.path.join(dlp_testkit.DEPLOY_DIR, "tests", "router-tier-vectors.json")
RESULTS_DIR = os.environ.get("ROUTER_EVAL_OUT",
                             os.path.join(os.path.dirname(dlp_testkit.DEPLOY_DIR), ".scratch", "issue-114", "results"))
GATEWAY = dlp_testkit.GATEWAY.rstrip("/")
CONCURRENCY = 2
TIMEOUT_S = 60
MAX_RETRIES = 2

SYSTEM_PROMPT = """你是 LLM 网关的路由分类器。把用户请求分为两档：
- simple：单步、有确定答案、无需长链推理或跨上下文综合——事实问答、短翻译、一句话润色、单行代码修改、简单命令、单工具直取。
- complex：多步推理、设计/权衡、长上下文代码任务、跨文件依赖、专业领域翻译、实验/建模设计、多轮工具编排与错误恢复。
注意：输入文本长短不代表难度；带 [SYSTEM]/[USER]/[TOOL] 标记的是 coding agent 多轮会话形态，按最后待完成的任务定档。
只输出 JSON：{"tier": "simple"} 或 {"tier": "complex"}"""

# 校准模式（ROUTER_EVAL_MODE=pcomplex）：输出连续分数供 RouteLLM 式阈值扫描
SYSTEM_PROMPT_PCOMPLEX = """你是 LLM 网关的路由分类器。把用户请求分为两档：
- simple：单步、有确定答案、无需长链推理或跨上下文综合——事实问答、短翻译、一句话润色、单行代码修改、简单命令、单工具直取。
- complex：多步推理、设计/权衡、长上下文代码任务、跨文件依赖、专业领域翻译、实验/建模设计、多轮工具编排与错误恢复。
注意：输入文本长短不代表难度；带 [SYSTEM]/[USER]/[TOOL] 标记的是 coding agent 多轮会话形态，按最后待完成的任务定档。
只输出 JSON：{"p_complex": 0 到 1 的小数}，表示「该请求需要旗舰模型（complex 档）」的概率。"""

_print_lock = threading.Lock()


def resolve_judge_model():
    tok = dlp_testkit.resolve_admin_token()
    if not tok:
        return "gpt-5.6-luna"
    try:
        req = urllib.request.Request(dlp_testkit.ADMIN_URL + "/dlp-admin/settings",
                                     headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            s = json.load(r)
        judge = s.get("judge") or (s.get("settings") or {}).get("judge") or {}
        return judge.get("model") or "gpt-5.6-luna"
    except Exception as e:
        print(f"[warn] 读取 shim settings 失败（{e}），回退默认模型名", flush=True)
        return "gpt-5.6-luna"


def load_api_key():
    p = os.path.join(dlp_testkit.DEPLOY_DIR, ".local", "test-api-key")
    return open(p, encoding="utf-8").read().strip()


def classify(api_key, model, content, pcomplex=False):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT_PCOMPLEX if pcomplex else SYSTEM_PROMPT},
                     {"role": "user", "content": content}],
        "max_tokens": 64,
        "temperature": 0,
    }).encode()
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(GATEWAY + "/v1/chat/completions", data=body,
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {api_key}"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                data = json.load(r)
            lat_ms = (time.perf_counter() - t0) * 1000
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            out = {"latency_ms": round(lat_ms, 1),
                   "prompt_tokens": usage.get("prompt_tokens"),
                   "completion_tokens": usage.get("completion_tokens"),
                   "raw": text[:200], "retries": attempt}
            if pcomplex:
                m = re.search(r'"p_complex"\s*:\s*([0-9]*\.?[0-9]+)', text)
                if not m:
                    raise ValueError(f"无法解析 p_complex: {text[:120]!r}")
                p = float(m.group(1))
                if not 0 <= p <= 1:
                    raise ValueError(f"p_complex 越界: {p}")
                out["p_complex"] = p
                out["tier"] = "complex" if p >= 0.5 else "simple"
                return out
            m = re.search(r'\{[^{}]*"tier"[^{}]*\}', text, re.S)
            tier = None
            if m:
                tier = json.loads(m.group(0)).get("tier")
            if tier not in ("simple", "complex"):
                raise ValueError(f"无法解析 tier: {text[:120]!r}")
            out["tier"] = tier
            return out
        except Exception as e:
            last_err = str(e)[:200]
            time.sleep(1.5 * (attempt + 1))
    return {"tier": None, "error": last_err, "latency_ms": None,
            "prompt_tokens": None, "completion_tokens": None, "retries": MAX_RETRIES}


def percentile(xs, q):
    xs = sorted(xs)
    k = (len(xs) - 1) * q / 100
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def main():
    pcomplex = os.environ.get("ROUTER_EVAL_MODE") == "pcomplex"
    model = resolve_judge_model()
    api_key = load_api_key()
    pricing = json.load(open(os.path.join(dlp_testkit.DEPLOY_DIR, "pricing.json"), encoding="utf-8"))
    price = pricing["official_prices_per_million_usd"].get(model, {})
    vectors = json.load(open(VECTORS, encoding="utf-8"))["vectors"]
    print(f"judge model={model} price={price} samples={len(vectors)} mode={'pcomplex' if pcomplex else 'tier'}", flush=True)

    results = [None] * len(vectors)

    def work(i, vec):
        r = classify(api_key, model, vec["content"], pcomplex=pcomplex)
        r.update({"id": vec["id"], "category": vec["category"], "lang": vec["lang"],
                  "expect": vec["expect"]})
        results[i] = r
        with _print_lock:
            done = sum(1 for x in results if x is not None)
            if done % 10 == 0:
                print(f"[{done}/{len(vectors)}]", flush=True)

    t_start = time.time()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        for i, vec in enumerate(vectors):
            ex.submit(work, i, vec)
    wall_s = time.time() - t_start

    ok = [r for r in results if r["tier"]]
    errs = [r for r in results if not r["tier"]]
    lats = [r["latency_ms"] for r in ok]
    pt = sum(r["prompt_tokens"] or 0 for r in ok)
    ct = sum(r["completion_tokens"] or 0 for r in ok)
    tp = sum(1 for r in ok if r["expect"] == "complex" and r["tier"] == "complex")
    fp = sum(1 for r in ok if r["expect"] == "simple" and r["tier"] == "complex")
    fn = sum(1 for r in ok if r["expect"] == "complex" and r["tier"] == "simple")
    tn = sum(1 for r in ok if r["expect"] == "simple" and r["tier"] == "simple")
    cost = pt * price.get("prompt", 0) / 1e6 + ct * price.get("completion", 0) / 1e6

    summary = {
        "model": model, "mode": "pcomplex" if pcomplex else "tier",
        "concurrency": CONCURRENCY, "wall_s": round(wall_s, 1),
        "n_samples": len(results), "n_ok": len(ok), "n_error": len(errs),
        "latency_ms": {"p50": round(percentile(lats, 50), 1),
                       "p95": round(percentile(lats, 95), 1),
                       "max": round(max(lats), 1)} if lats else None,
        "tokens": {"prompt": pt, "completion": ct,
                   "avg_prompt_per_req": round(pt / len(ok), 1) if ok else None},
        "cost_usd_total": round(cost, 6),
        "cost_usd_per_req": round(cost / len(ok), 6) if ok else None,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                      "accuracy": round((tp + tn) / len(ok), 4) if ok else None,
                      "precision_complex": round(tp / (tp + fp), 4) if tp + fp else None,
                      "recall_complex": round(tp / (tp + fn), 4) if tp + fn else None},
    }
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = os.path.join(RESULTS_DIR, "llm-%s%s.json" % ("pcomplex-" if pcomplex else "",
                                                       time.strftime("%Y%m%d-%H%M%S")))
    json.dump({"summary": summary, "details": results}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("结果:", out)
    if errs:
        print("失败样本:", [r["id"] for r in errs][:20])


if __name__ == "__main__":
    main()
