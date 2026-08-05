#!/usr/bin/env python3
"""ai4s DLP 对抗回归（issue #20）。

对活网关 :3000 全链路逐样本发请求，断言三类结果：
  reject —— 上游应答 451（L1/L2 阻断）
  mask   —— 200 且 echo 中不含敏感原文（上游只收到掩码）
  pass   —— 200 且 echo 含原文（负例必须 pass；文档化绕过项 note 标注 gap）

回声原理：mock-upstream 把收到的 user 消息原文放进应答（"echo: ..."），
断言"上游实际收到的内容"——脱敏断言的唯一可靠锚点。

自动准备（幂等）：起 mock-upstream（profile mock）+ 建 dlp-echo 渠道（model=echo-test）。
用法：cd deploy && python3 scripts/dlp-regression.py [--json out.json]
退出码：有"应拦未拦/应脱敏未脱敏/负例误伤"即 1；文档化 gap 不 fail。
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

DEPLOY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATEWAY = os.environ.get("GATEWAY_BASE", "http://localhost:3000")
AXONHUB_BASE = os.environ.get("AXONHUB_BASE", "http://localhost:8090")
VECTORS_PATH = os.path.join(DEPLOY_DIR, "tests", "dlp-vectors.json")
ECHO_MODEL = "echo-test"


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
    if os.path.exists(jwt_path):
        token = open(jwt_path).read().strip()
        try:
            gql("query { myProjects { id } }", token=token)
            return token
        except Exception:
            pass
    body = json.dumps({"email": env["AXONHUB_ADMIN_EMAIL"], "password": env["AXONHUB_ADMIN_PASSWORD"]}).encode()
    req = urllib.request.Request(AXONHUB_BASE + "/admin/auth/signin", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)["token"]


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


def ensure_echo_channel(token):
    data = gql("query { queryChannels(input: {first: 100}) { edges { node { id name status } } } }", token=token)
    ch = next((e["node"] for e in data["queryChannels"]["edges"] if e["node"]["name"] == "dlp-echo"), None)
    if not ch:
        r = gql("""mutation($input: CreateChannelInput!) { createChannel(input: $input) { id } }""",
                {"input": {"type": "openai", "name": "dlp-echo",
                           "baseURL": "http://mock-upstream:8080/v1",
                           "credentials": {"apiKey": "mock-key"},
                           "supportedModels": [ECHO_MODEL], "defaultTestModel": ECHO_MODEL}}, token=token)
        ch = r["createChannel"]
    if ch.get("status") != "enabled":
        gql("mutation($id: ID!, $status: ChannelStatus!) { updateChannelStatus(id: $id, status: $status) { id } }",
            {"id": ch["id"], "status": "enabled"}, token=token)
    return ch["id"]


def run_edm_section(api_key):
    """EDM 自包含段（issue #29）：临时合成文档入库→整篇粘贴应 451→负例应放行→清理。"""
    import subprocess
    print("\n==> EDM 段（临时语料自包含）")
    doc_path = os.path.join(DEPLOY_DIR, "edm", "corpus", "__regression_tmp__.txt")
    os.makedirs(os.path.dirname(doc_path), exist_ok=True)
    doc = ("内部结算备忘录 ZX-77：codex 渠道结算比例为 0.831，tokenhub 渠道为 0.917，"
           "尾差计入损益调整科目 6650；月度对账报告由计费引擎自动生成并经合规复核。") * 6
    open(doc_path, "w", encoding="utf-8").write(doc)
    subprocess.run(["python3", "scripts/edm-add.py", "edm/corpus/__regression_tmp__.txt", "--name", "__regression_tmp__"],
                   cwd=DEPLOY_DIR, check=True, capture_output=True)
    results = []
    try:
        for attempt in range(2):
            status, _ = send("把这份备忘录发给模型总结：\n" + doc, api_key)
            got = classify(status, _, None)
            if got == "reject":
                break
            time.sleep(1)
        ok = got == "reject"
        results.append(("EDM: 整篇粘贴应 451", ok, got))
        status, _ = send("帮我写一份对账流程优化建议", api_key)
        got = classify(status, _, None)
        ok = got == "pass"
        results.append(("EDM: 负例应放行", ok, got))
    finally:
        subprocess.run(["python3", "scripts/edm-add.py", "edm/corpus/__regression_tmp__.txt", "--name", "__regression_tmp__", "--remove"],
                       cwd=DEPLOY_DIR, check=False, capture_output=True)
        try:
            os.remove(doc_path)
        except OSError:
            pass
    for name, ok, got in results:
        print(f"[{'OK ' if ok else 'FAIL'}] {name}（got={got}）")
    return [(name, ok, got) for name, ok, got in results]


def send(content, api_key):
    body = json.dumps({"model": ECHO_MODEL, "messages": [{"role": "user", "content": content}]}).encode()
    req = urllib.request.Request(GATEWAY + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        return 200, d["choices"][0]["message"].get("content") or ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def classify(status, reply, sensitive):
    if status == 451:
        return "reject"
    if status == 200:
        if sensitive and sensitive in reply:
            return "pass"
        return "mask" if sensitive else "pass"
    return f"http{status}"


def main():
    out_json = "--json" in sys.argv
    out_path = sys.argv[sys.argv.index("--json") + 1] if out_json else None

    env = load_env()
    api_key = open(os.path.join(DEPLOY_DIR, ".local", "test-api-key")).read().strip()

    print("==> 起 mock-upstream（profile mock）")
    subprocess.run(["docker", "compose", "--profile", "mock", "up", "-d", "--wait", "mock-upstream"],
                   cwd=DEPLOY_DIR, check=True, capture_output=True)
    token = get_token(env)
    ensure_echo_channel(token)
    time.sleep(2)  # 渠道生效缓冲

    vectors = json.load(open(VECTORS_PATH, encoding="utf-8"))["vectors"]
    results = []
    for v in vectors:
        status, reply = send(v["content"], api_key)
        got = classify(status, reply, v.get("sensitive"))
        ok = got == v["expect"]
        # 文档化 gap（expect=pass 且带 note）不算失败；负例/应拦/应脱敏不符才算
        fail = not ok and not (v["expect"] == "pass" and v.get("note") and v["layer"] != "negative")
        results.append({"name": v["name"], "layer": v["layer"], "expect": v["expect"],
                        "got": got, "ok": ok, "fail": fail, "note": v.get("note", "")})
        mark = "OK " if ok else ("GAP" if not fail else "FAIL")
        print(f"[{mark}] {v['name']}: expect={v['expect']} got={got}" + (f"（{v['note']}）" if not ok and v.get("note") else ""))
        time.sleep(0.2)

    # 汇总
    layers = {}
    for r in results:
        L = layers.setdefault(r["layer"], {"total": 0, "ok": 0, "gap": 0, "fail": 0})
        L["total"] += 1
        if r["ok"]:
            L["ok"] += 1
        elif r["fail"]:
            L["fail"] += 1
        else:
            L["gap"] += 1
    print("\n===== 分层水位 =====")
    for layer, s in layers.items():
        should_block = sum(1 for r in results if r["layer"] == layer and r["expect"] in ("reject", "mask"))
        blocked = sum(1 for r in results if r["layer"] == layer and r["expect"] in ("reject", "mask") and r["ok"])
        line = f"{layer}: 应拦/应脱敏 {blocked}/{should_block}"
        if should_block:
            line += f"（检出率 {blocked / should_block:.0%}）"
        line += f" | gap {s['gap']} | fail {s['fail']}"
        print(line)

    edm_fails = run_edm_section(api_key)

    fails = [r for r in results if r["fail"]]
    for name, ok, got in edm_fails:
        if not ok:
            fails.append({"name": name, "expect": "reject", "got": got})
    print(f"\n总计 {len(results)} 样本：通过 {sum(1 for r in results if r['ok'])}，文档化 gap {sum(1 for r in results if not r['ok'] and not r['fail'])}，回归失败 {len(fails)}")
    if fails:
        print("失败明细：")
        for r in fails:
            print(f"  - {r['name']}: expect={r['expect']} got={r['got']}")

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"date": time.strftime("%Y-%m-%d"), "results": results}, f, ensure_ascii=False, indent=1)
        print(f"JSON 已写 {out_path}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
