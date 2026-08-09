#!/usr/bin/env python3
"""ai4s DLP 活栈测试公共套件（issue #42 提取自 dlp-regression.py，行为不变）。

供 dlp-regression.py / dlp-capability.py 等活栈脚本复用：
  常量    DEPLOY_DIR / GATEWAY / AXONHUB_BASE / ECHO_MODEL / ADMIN_URL
  环境    load_env / get_token（axonhub 登录或 .local/admin-jwt）
  渠道    gql / ensure_echo_channel / prepare（起 mock-upstream + 建 dlp-echo 渠道）
  请求    send / classify（reject=上游 451，mask=200 且 echo 无敏感原文，pass=200 含原文）
  管理面  _admin_api / resolve_admin_token（env DLP_ADMIN_TOKEN > deploy/.local/admin-jwt）

回声原理：mock-upstream 把收到的 user 消息原文放进应答（"echo: ..."），
断言"上游实际收到的内容"——脱敏断言的唯一可靠锚点。
"""
import json
import os
import subprocess
import time
import urllib.request
import urllib.error

DEPLOY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATEWAY = os.environ.get("GATEWAY_BASE", "http://localhost:3000")
AXONHUB_BASE = os.environ.get("AXONHUB_BASE", "http://localhost:8090")
ECHO_MODEL = "echo-test"
ADMIN_URL = os.environ.get("DLP_ADMIN_URL", "http://localhost:18080").rstrip("/")


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


def prepare():
    """自动准备（幂等）：起 mock-upstream（profile mock）+ 建 dlp-echo 渠道（model=echo-test）。
    返回 axonhub token；api_key 由调用方自读 deploy/.local/test-api-key。"""
    print("==> 起 mock-upstream（profile mock）")
    subprocess.run(["docker", "compose", "--profile", "mock", "up", "-d", "--wait", "mock-upstream"],
                   cwd=DEPLOY_DIR, check=True, capture_output=True)
    token = get_token(load_env())
    ensure_echo_channel(token)
    time.sleep(2)  # 渠道生效缓冲
    return token


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


def _admin_api(method, path, token, payload=None):
    """admin API 调用（issue #37）：返回 (status, body)；网络异常归一为 status=0。"""
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(ADMIN_URL + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def resolve_admin_token():
    """admin 段凭据（issue #37）：env DLP_ADMIN_TOKEN 优先，缺省读 deploy/.local/admin-jwt；
    两者均不可得返回 None（调用方打印 SKIP，不 fail）。"""
    t = os.environ.get("DLP_ADMIN_TOKEN")
    if t and t.strip():
        return t.strip()
    p = os.path.join(DEPLOY_DIR, ".local", "admin-jwt")
    if os.path.exists(p):
        tok = open(p, encoding="utf-8").read().strip()
        if tok:
            return tok
    return None
