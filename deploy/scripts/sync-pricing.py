#!/usr/bin/env python3
"""ai4s 官方价锚同步（issue #18 机制化）：models.dev 第一方 provider → deploy/pricing.json。

数据源 https://models.dev/api.json（axonhub 上游 providers.json 同源目录生态）。
只取**第一方**（厂商官方）provider 的 cost 作为「官方原价」锚；reseller（openrouter/
nano-gpt 等聚合商）价一律不采。同步只动 official_prices_per_million_usd 锚，渠道
multiplier 是本地策略，永不触碰。

用法：cd deploy && python3 scripts/sync-pricing.py [--check|--apply] [--offline]
  --check   只打印漂移报告（默认）；有漂移退出码 1
  --apply   把漂移写回 pricing.json（version  bump 为当天），随后须跑 apply-pricing.py 落库
  --offline 不拉网，用 .local/modelsdev-cache.json（上次成功抓取的缓存）
"""
import json
import os
import sys
import time
import urllib.request

DEPLOY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICING_PATH = os.path.join(DEPLOY_DIR, "pricing.json")
CACHE_PATH = os.environ.get(
    "MODELSDEV_CACHE", os.path.join(DEPLOY_DIR, ".local", "modelsdev-cache.json")
)
SOURCE_URL = "https://models.dev/api.json"

# canonical 模型 → models.dev 第一方 provider id（只信官方，reseller 不采）
PROVIDER_MAP = {
    "gpt-": "openai",
    "claude": "anthropic",
    "glm": "zai",
    "kimi": "moonshotai",
    "qwen": "alibaba",
    "gemini": "google",
    "google/gemini": "google",
    "deepseek-v4-pro": "deepseek",
}
EPSILON = 1e-9


def firstparty_provider(canonical: str):
    for prefix, pid in PROVIDER_MAP.items():
        if canonical.startswith(prefix):
            return pid
    return None


def fetch_catalog(offline: bool):
    if offline:
        return json.load(open(CACHE_PATH, encoding="utf-8"))
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "curl/8 ai4s-pricing-sync"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception:
        if os.path.exists(CACHE_PATH):
            print("WARN: models.dev 拉取失败，回退上次缓存", file=sys.stderr)
            return json.load(open(CACHE_PATH, encoding="utf-8"))
        raise
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    json.dump(data, open(CACHE_PATH, "w", encoding="utf-8"))
    return data


def find_official_cost(catalog, canonical: str, aliases=None):
    """在第一方 provider 下按裸模型名查找 cost；找不到返回 None。
    aliases：pricing.json 的 official_aliases 节，值格式 "provider:官方模型id"，
    用于 canonical 前缀不在 PROVIDER_MAP 的渠道侧命名（如 zenmux 的 deepseek/*）。"""
    pid, base = None, canonical.split("/")[-1]
    alias = (aliases or {}).get(canonical)
    if alias and ":" in alias:
        pid, base = alias.split(":", 1)
    else:
        pid = firstparty_provider(canonical)
    if not pid or pid not in catalog:
        return None
    for mid, m in (catalog[pid].get("models") or {}).items():
        if mid.split("/")[-1] == base and m.get("cost"):
            return pid, m["cost"]
    return None


def close(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= EPSILON * max(1.0, abs(a), abs(b))


def compute_drifts(official: dict, catalog, aliases=None) -> tuple:
    """比对官方锚与第一方目录 → (drifts, manuals)。
    drifts: [(canonical, pid, 新锚dict, 变更行list)]；manuals: 第一方无收录/缺价的模型描述。
    官方无 cache_read 价（None）→ cached 锚取 prompt 全价（保守：防上游报 cached
    tokens 时按 0 白送——axonhub 未配 cached 价格项的缓存命中不计费）。"""
    drifts, manuals = [], []
    for canonical, anchor in official.items():
        hit = find_official_cost(catalog, canonical, aliases)
        if not hit:
            manuals.append(canonical)
            continue
        pid, cost = hit
        new = {
            "prompt": cost.get("input"),
            "completion": cost.get("output"),
            "cached": cost.get("cache_read") if cost.get("cache_read") is not None else cost.get("input"),
            "cache_write": cost.get("cache_write"),
        }
        if new["prompt"] is None or new["completion"] is None:
            manuals.append(f"{canonical}（{pid} 缺 input/output 价）")
            continue
        if new["cache_write"] is None:
            new.pop("cache_write")
        changed = []
        for k in ("prompt", "completion", "cached", "cache_write"):
            if not close(anchor.get(k), new.get(k)):
                changed.append(f"{k}: {anchor.get(k)} → {new.get(k)}")
        if changed:
            drifts.append((canonical, pid, new, changed))
    return drifts, manuals


def main():
    apply_changes = "--apply" in sys.argv
    offline = "--offline" in sys.argv
    cfg = json.load(open(PRICING_PATH, encoding="utf-8"))
    official = cfg["official_prices_per_million_usd"]
    catalog = fetch_catalog(offline)

    drifts, manuals = compute_drifts(official, catalog, cfg.get("official_aliases"))

    for canonical in manuals:
        print(f"MANUAL  {canonical}：第一方目录无收录，保留现锚，请对上游实际价目（如 ZENMUX 账单）")
    for canonical, pid, new, changed in drifts:
        print(f"DRIFT   {canonical} [{pid}]")
        for c in changed:
            print(f"        {c}")
    if not drifts:
        print(f"全部 {len(official) - len(manuals)} 个可同步锚与第一方目录一致，无漂移")

    if apply_changes and drifts:
        for canonical, _pid, new, _changed in drifts:
            official[canonical] = new
        cfg["version"] = time.strftime("%Y-%m-%d")
        json.dump(cfg, open(PRICING_PATH, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"==> 已写回 pricing.json（{len(drifts)} 个锚，version={cfg['version']}）")
        print("==> 下一步：python3 scripts/apply-pricing.py --check 确认后落库")
    sys.exit(1 if drifts and not apply_changes else 0)


if __name__ == "__main__":
    main()
