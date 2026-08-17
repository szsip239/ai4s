#!/usr/bin/env bash
# issue #2（降级）验证脚本（测试即票据）：假凭据负路径
# seam：管理 API（渠道/测试/错误观测）+ 经 agentgateway 的 /v1 调用面
# 用法：./scripts/verify-issue-2.sh   —— 全绿退出码 0
set -uo pipefail
cd "$(dirname "$0")/.."

JWT=$(cat .local/admin-jwt)
ADMIN=http://localhost:3000/admin/graphql
GW=http://localhost:3000
FAIL=0

gql() {
  local payload
  payload=$(PAYLOAD_QUERY="$1" PAYLOAD_VARS="$2" python3 -c 'import json,os;print(json.dumps({"query":os.environ["PAYLOAD_QUERY"],"variables":json.loads(os.environ["PAYLOAD_VARS"])}))')
  curl -sf "$ADMIN" -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" -d "$payload"
}

check() { if [ "$2" = "0" ]; then echo "  PASS: $1"; else echo "  FAIL: $1"; FAIL=1; fi; }

probe() { # 用多渠道 key 打一发 mock-gpt，输出 HTTP 状态码
  curl -s -o /dev/null -w "%{http_code}" "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $(cat .local/i2-multi-key)" -H "Content-Type: application/json" \
    -d '{"model":"mock-gpt","messages":[{"role":"user","content":"failover probe"}]}'
}

echo "==> [sliceA] 假凭据 OAuth 渠道存在且类型正确"

gql 'query($input: QueryChannelInput!) { queryChannels(input: $input) { edges { node { id name type status errorMessage } } } }' \
  '{"input":{"first":200,"where":{"statusIn":["enabled","disabled","archived"]}}}' > .local/i2-channels.json 2>/dev/null || echo '{"errors":"gql failed"}' > .local/i2-channels.json
python3 - <<'EOF'
import json,sys
d=json.load(open(".local/i2-channels.json"))
edges=(d.get("data") or {}).get("queryChannels",{}).get("edges",[])
chans={e["node"]["name"]:e["node"] for e in edges}
a=chans.get("codex-fake-negative"); b=chans.get("claudecode-fake-negative")
ok=(a and a["type"]=="codex" and a["status"]=="enabled" and
    b and b["type"]=="claudecode" and b["status"]=="enabled")
sys.exit(0 if ok else 1)
EOF
check "codex-fake-negative / claudecode-fake-negative 存在、类型正确、enabled" $?

echo "==> [sliceB] 渠道健康检测识别假凭据（负路径）"

for CH in codex-fake-negative claudecode-fake-negative; do
  CH_ID=$(python3 -c 'import json,sys
d=json.load(open(".local/i2-channels.json"))
edges=(d.get("data") or {}).get("queryChannels",{}).get("edges",[])
hit=[e["node"]["id"] for e in edges if e["node"]["name"]==sys.argv[1]]
print(hit[0] if hit else "")' "$CH")
  if [ -n "$CH_ID" ]; then
    gql 'mutation($input: TestChannelInput!) { testChannel(input: $input) { success error latency } }' \
      "{\"input\":{\"channelID\":\"$CH_ID\",\"modelID\":\"mock-gpt\"}}" > ".local/i2-test-$CH.json" 2>/dev/null || echo '{"errors":"gql failed"}' > ".local/i2-test-$CH.json"
    python3 - "$CH" <<'EOF'
import json,sys
ch=sys.argv[1]
d=json.load(open(f".local/i2-test-{ch}.json"))
r=(d.get("data") or {}).get("testChannel")
if r is None: sys.exit(1)
print(f"    {ch}: success={r.get('success')} err={(r.get('error') or '')[:80]}")
sys.exit(0 if (r.get("success") is False and (r.get("error") or "")) else 1)
EOF
    check "testChannel 检出 $CH 假凭据（success=false 且有 error）" $?
  else
    echo "  FAIL: $CH id 未找到"; FAIL=1
  fi
done

echo "==> [sliceC] 主渠道故障注入：流量落假渠道、错误可观测、恢复回弹"

# 多渠道 key 存在（profile 含 mock + 两个假渠道）
gql 'query { apiKeys(first: 50) { edges { node { id name status profiles { activeProfile profiles { name channelIDs } } } } } }' '{}' > .local/i2-keys.json 2>/dev/null || echo '{"errors":"gql failed"}' > .local/i2-keys.json
python3 - <<'EOF'
import json,sys
d=json.load(open(".local/i2-keys.json"))
edges=(d.get("data") or {}).get("apiKeys",{}).get("edges",[])
key=next((e["node"] for e in edges if e["node"]["name"]=="i2-multi-channel-key"), None)
if not key: sys.exit(1)
plist=(key.get("profiles") or {}).get("profiles") or []
ids=set()
for p in plist: ids |= set(p.get("channelIDs") or [])
sys.exit(0 if len(ids) >= 3 else 1)
EOF
check "i2-multi-channel-key profile 覆盖 ≥3 渠道（mock + 2 假）" $?

if [ ! -s .local/i2-multi-key ]; then
  echo "  FAIL: .local/i2-multi-key 不存在"; FAIL=1
else
  MOCK_GID=$(python3 -c 'import json
d=json.load(open(".local/i2-channels.json"))
edges=(d.get("data") or {}).get("queryChannels",{}).get("edges",[])
hit=[e["node"]["id"] for e in edges if e["node"]["name"]=="mock-upstream-tracer"]
print(hit[0] if hit else "")')
  set_ch() { gql 'mutation($id: ID!, $status: ChannelStatus!) { updateChannelStatus(id: $id, status: $status) { id status } }' "{\"id\":\"$1\",\"status\":\"$2\"}" >/dev/null 2>&1; }
  trap 'set_ch "$MOCK_GID" enabled' EXIT   # 任何退出路径都恢复 mock

  C=$(probe)
  [ "$C" = "200" ]
  check "故障注入前基线 200（实得 ${C}）" $?

  set_ch "$MOCK_GID" disabled
  sleep 1
  C=$(probe)
  [ "$C" != "200" ]
  check "mock 禁用后请求失败（实得 ${C}，流量被迫离开主渠道）" $?

  sleep 2   # 请求记录写入可能是异步
  gql 'query { requests(first: 12) { edges { node { status channelID createdAt } } } }' '{}' > .local/i2-requests.json 2>/dev/null || echo '{"errors":"gql failed"}' > .local/i2-requests.json
  python3 - <<'EOF'
import json,sys
d=json.load(open(".local/i2-requests.json"))
edges=(d.get("data") or {}).get("requests",{}).get("edges",[])
fails=[e["node"] for e in edges if e["node"]["status"]=="failed"]
for f in fails[:3]:
    print(f"    失败请求记录: {f['createdAt']} status={f['status']} channel={f.get('channelID')}")
sys.exit(0 if fails else 1)
EOF
  check "失败请求以 status=failed 记录在案（请求级可观测）" $?

  set_ch "$MOCK_GID" enabled
  trap - EXIT
  sleep 1
  C=$(probe)
  [ "$C" = "200" ]
  check "mock 恢复后回弹 200（实得 ${C}）" $?
fi

echo ""
if [ "$FAIL" = "0" ]; then echo "issue #2（降级）验证：全部通过"; else echo "issue #2（降级）验证：存在失败项"; exit 1; fi
