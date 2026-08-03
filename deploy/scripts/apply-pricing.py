#!/usr/bin/env python3
"""ai4s credit 价格表应用（issue #18，幂等）。

读 deploy/pricing.json：官方原价 × 渠道倍率 = credit 单价（usagePerUnit，单位：/token），
经 saveChannelModelPrices 写入各渠道。倍率无默认值——渠道缺 multiplier 直接报错退出。

用法：cd deploy && python3 scripts/apply-pricing.py [--check]
  --check 只打印将写入的价格，不落库
依赖仅标准库；管理 JWT 复用 .local/admin-jwt（失效则用 .env 凭据重登录）。
"""
import json
import os
import sys
import urllib.request

DEPLOY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AXONHUB_BASE = os.environ.get("AXONHUB_BASE", "http://localhost:8090")


def load_env():
    env = {}
    with open(os.path.join(DEPLOY_DIR, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env


def get_token(env):
    jwt_path = os.path.join(DEPLOY_DIR, ".local", "admin-jwt")
    token = ""
    if os.path.exists(jwt_path):
        token = open(jwt_path).read().strip()
    if token:
        try:
            gql("query { myProjects { id } }", token=token)
            return token
        except Exception:
            pass
    body = json.dumps({"email": env["AXONHUB_ADMIN_EMAIL"], "password": env["AXONHUB_ADMIN_PASSWORD"]}).encode()
    req = urllib.request.Request(AXONHUB_BASE + "/admin/auth/signin", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        token = json.load(r)["token"]
    os.makedirs(os.path.dirname(jwt_path), exist_ok=True)
    open(jwt_path, "w").write(token)
    return token


def gql(query, variables=None, token=None):
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(AXONHUB_BASE + "/admin/graphql", data=payload,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    if data.get("errors"):
        raise RuntimeError(str(data["errors"])[:300])
    return data["data"]


def per_million(per_million_usd: float, multiplier: float) -> str:
    """官方 $/M × 倍率 → credit/百万 token（axonhub usagePerUnit 单位即"每百万 token"，cost_calc.go: units/1e6 × UsagePerUnit）。"""
    v = per_million_usd * multiplier
    return f"{v:.12f}".rstrip("0").rstrip(".") if "." in f"{v:.12f}" else f"{v:.12f}"


def main():
    check_only = "--check" in sys.argv
    cfg = json.load(open(os.path.join(DEPLOY_DIR, "pricing.json"), encoding="utf-8"))
    official = cfg["official_prices_per_million_usd"]

    # 校验：所有渠道的模型锚必须存在于官方价表；倍率必须显式存在
    for ch in cfg["channels"]:
        if "multiplier" not in ch:
            sys.exit(f"ERROR: 渠道 {ch['name']} 未显式配置 multiplier（倍率无默认值，issue #18 约定）")
        for alias, canonical in ch["models"].items():
            if canonical not in official:
                sys.exit(f"ERROR: 渠道 {ch['name']} 模型 {alias} 锚定的官方价 {canonical} 不存在")

    env = load_env()
    token = get_token(env)
    data = gql("query { queryChannels(input: {first: 100}) { edges { node { id name status } } } }", token=token)
    by_name = {e["node"]["name"]: e["node"] for e in data["queryChannels"]["edges"]}

    for ch in cfg["channels"]:
        node = by_name.get(ch["name"])
        if not node:
            sys.exit(f"ERROR: axonhub 中不存在渠道 {ch['name']}")
        m = ch["multiplier"]
        prices = []
        for alias, canonical in ch["models"].items():
            o = official[canonical]
            items = [
                {"itemCode": "prompt_tokens", "pricing": {"mode": "usage_per_unit", "usagePerUnit": per_million(o["prompt"], m)}},
                {"itemCode": "completion_tokens", "pricing": {"mode": "usage_per_unit", "usagePerUnit": per_million(o["completion"], m)}},
                {"itemCode": "prompt_cached_tokens", "pricing": {"mode": "usage_per_unit", "usagePerUnit": per_million(o["cached"], m)}},
            ]
            prices.append({"modelId": alias, "price": {"items": items}})
            print(f"  {ch['name']}/{alias}（锚 {canonical}，×{m}）: "
                  f"prompt={per_million(o['prompt'], m)} completion={per_million(o['completion'], m)} cached={per_million(o['cached'], m)} credit/M")
        if check_only:
            continue
        gql("""mutation($channelId: ID!, $input: [SaveChannelModelPriceInput!]!) {
              saveChannelModelPrices(channelId: $channelId, input: $input) { id modelID }
            }""", {"channelId": node["id"], "input": prices}, token=token)
        print(f"==> 已写入 {ch['name']}（{node['id']}）{len(prices)} 个模型价格")
    print("完成" + ("（--check 未落库）" if check_only else ""))


if __name__ == "__main__":
    main()
