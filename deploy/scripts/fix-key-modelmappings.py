#!/usr/bin/env python3
"""存量 Key profile modelMappings null 修复（issue #81，一次性 + 可复查）。

背景：审批流换档的 load_tier_profile 曾丢弃模板 modelMappings，落库 null；前端 zod
schema 要求必填数组，管理员点「配置」即解析报错。本脚本枚举受影响 key 并经 GraphQL
updateAPIKeyProfiles 补 []（禁 SQL 直改——走 mutation 保证缓存刷新）。

用法：cd deploy && python3 scripts/fix-key-modelmappings.py [--apply]
  默认 dry-run 只列受影响清单；--apply 实际修复并逐个打印结果
依赖仅标准库；管理 JWT 复用 .local/admin-jwt（失效则用 .env 凭据重登录）。
"""
import json
import os
import sys
import urllib.request

DEPLOY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AXONHUB_BASE = os.environ.get("AXONHUB_BASE", "http://localhost:3000")

KEYS_QUERY = (
    # first:100 硬上限：PoC 规模 key 总数量级为几十个；若未来超过需改 after 分页枚举
    "query { apiKeys(first: 100) { edges { node { id name status "
    "profiles { activeProfile profiles { name modelMappings { from to } "
    "quota { requests totalTokens cost period { type calendarDuration { unit } } } } } } } } }"
)
UPDATE_MUTATION = (
    "mutation($id: ID!, $input: UpdateAPIKeyProfilesInput!) "
    "{ updateAPIKeyProfiles(id: $id, input: $input) { id } }"
)


def load_env():
    env = {}
    with open(os.path.join(DEPLOY_DIR, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env


def get_token():
    """先试缓存 JWT（有效则不依赖 .env）；失效/不存在才读 .env 凭据重登录并回写缓存。"""
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
    env = load_env()
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


def fixed_profile(p: dict) -> dict:
    """存量 profile → 修复输入形状（与 shim alert_poller.load_tier_profile 输出同构）：
    只把 modelMappings null 补 []，quota/period 原样带全（缺省兜底与 load_tier_profile 同）。"""
    q = p.get("quota") or {}
    period = q.get("period") or {}
    return {
        "name": p.get("name"),
        "modelMappings": p.get("modelMappings") or [],
        "quota": {
            "requests": q.get("requests"),
            "totalTokens": q.get("totalTokens"),
            "cost": str(q["cost"]) if q.get("cost") is not None else None,
            "period": {"type": period.get("type", "calendar_duration"),
                       "calendarDuration": period.get("calendarDuration") or {"unit": "month"}},
        },
    }


def main():
    apply = "--apply" in sys.argv
    token = get_token()  # 缓存 JWT 优先，失效才读 .env 重登
    edges = gql(KEYS_QUERY, token=token)["apiKeys"]["edges"]

    affected = []
    for e in edges:
        node = e["node"]
        profs = ((node.get("profiles") or {}).get("profiles")) or []
        null_names = [p.get("name") for p in profs if p.get("modelMappings") is None]
        if null_names:
            affected.append((node, null_names))

    if not affected:
        print("无受影响 key（所有 profile 的 modelMappings 均非 null）")
        return
    print(f"受影响 key {len(affected)} 个：")
    for node, null_names in affected:
        print(f"  {node['id']}  {node['name']}  status={node['status']}  null档={null_names}")

    if not apply:
        print("\ndry-run：未落库。确认后加 --apply 执行修复。")
        return
    failed = []
    for node, _ in affected:
        profs = ((node.get("profiles") or {}).get("profiles")) or []
        try:
            gql(UPDATE_MUTATION, {"id": node["id"], "input": {
                "activeProfile": (node.get("profiles") or {}).get("activeProfile"),
                "profiles": [fixed_profile(p) for p in profs],
            }}, token=token)
            print(f"==> 已修复 {node['id']}  {node['name']}（{len(profs)} 个 profile modelMappings 补 []）")
        except Exception as e:  # 单 key 失败不中断后续，末尾汇总失败清单
            failed.append(node)
            print(f"!! 修复失败 {node['id']}  {node['name']}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n失败 {len(failed)} 个（可重跑——已修复的 key 不再受影响）：")
        for node in failed:
            print(f"  {node['id']}  {node['name']}")
        sys.exit(1)
    print("完成")


if __name__ == "__main__":
    main()
