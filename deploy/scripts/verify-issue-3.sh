#!/usr/bin/env bash
# issue #3 验证脚本（测试即票据）：员工 key + 成本配额
# seam：管理 API（GraphQL）+ 经 agentgateway 的 /v1 调用面
# 用法：./scripts/verify-issue-3.sh   —— 全绿退出码 0，任一断言失败非零退出
set -uo pipefail
cd "$(dirname "$0")/.."

JWT=$(cat .local/admin-jwt)
ADMIN=http://localhost:3000/admin/graphql
GW=http://localhost:3000
FAIL=0

gql() { # $1=query $2=variables(JSON) —— python 组包，防 bash 3.2 引号地狱
  local payload
  payload=$(PAYLOAD_QUERY="$1" PAYLOAD_VARS="$2" python3 -c 'import json,os;print(json.dumps({"query":os.environ["PAYLOAD_QUERY"],"variables":json.loads(os.environ["PAYLOAD_VARS"])}))')
  curl -sf "$ADMIN" -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" -d "$payload"
}

check() { # name result
  if [ "$2" = "0" ]; then echo "  PASS: $1"; else echo "  FAIL: $1"; FAIL=1; fi
}

echo "==> [slice1] 测试员工与默认档 key"

# 1. 测试员工存在
gql 'query { users(first: 50) { edges { node { email } } } }' '{}' > .local/i3-users.json 2>/dev/null || echo '{"errors":"gql failed"}' > .local/i3-users.json
python3 - <<'EOF'
import json,sys
d=json.load(open(".local/i3-users.json"))
edges=(d.get("data") or {}).get("users",{}).get("edges",[])
ok=any(e["node"]["email"]=="employee-test@ai4s.local" for e in edges)
sys.exit(0 if ok else 1)
EOF
check "测试员工 employee-test@ai4s.local 存在" $?

# 2. 默认档 key 的 profile 字段正确（quota $80/自然月、限 mock 渠道）
gql 'query { apiKeys(first: 50) { edges { node { id name status profiles { activeProfile profiles { name channelIDs quota { requests cost period { type calendarDuration { unit } } } } } } } } }' '{}' > .local/i3-keys.json 2>/dev/null || echo '{"errors":"gql failed"}' > .local/i3-keys.json
python3 - <<'EOF'
import json,sys
d=json.load(open(".local/i3-keys.json"))
edges=(d.get("data") or {}).get("apiKeys",{}).get("edges",[])
key=next((e["node"] for e in edges if e["node"]["name"]=="employee-default-key"), None)
if not key: sys.exit(1)
wrap=key.get("profiles") or {}
plist=wrap.get("profiles") or []
p=next((x for x in plist if x["name"]=="default-标准档"), None)
if not p: sys.exit(1)
q=p.get("quota") or {}
cost=q.get("cost")
cost_ok = float(cost) == 80.0 if cost is not None else False
per=q.get("period") or {}
per_ok = per.get("type")=="calendar_duration" and (per.get("calendarDuration") or {}).get("unit") in ("month","MONTH","Month")
chan_ok = len(p.get("channelIDs") or []) >= 1
active_ok = wrap.get("activeProfile")=="default-标准档"
sys.exit(0 if (cost_ok and per_ok and chan_ok and active_ok and key["status"]=="enabled") else 1)
EOF
check "employee-default-key 挂 default-标准档（\$80/自然月、限渠道、enabled）" $?

# 3. 该 key 经 agentgateway 可调用
if [ -s .local/i3-default-key ]; then
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $(cat .local/i3-default-key)" -H "Content-Type: application/json" \
    -d '{"model":"mock-gpt","messages":[{"role":"user","content":"ping"}]}')
  [ "$CODE" = "200" ]
  check "employee-default-key 调用 /v1/chat/completions 返回 200（实得 ${CODE}）" $?
else
  echo "  FAIL: .local/i3-default-key 不存在"; FAIL=1
fi

echo "==> [slice2] 测试档与超限拒绝"

# 4. 测试档 key 存在且 profile 正确（requests=3、cost=$0.01、自然月）
python3 - <<'EOF'
import json,sys
d=json.load(open(".local/i3-keys.json"))
edges=(d.get("data") or {}).get("apiKeys",{}).get("edges",[])
key=next((e["node"] for e in edges if e["node"]["name"]=="employee-test-key"), None)
if not key: sys.exit(1)
plist=(key.get("profiles") or {}).get("profiles") or []
p=next((x for x in plist if x["name"]=="test-测试档"), None)
if not p: sys.exit(1)
q=p.get("quota") or {}
req_ok = q.get("requests")==3
cost=q.get("cost")
cost_ok = float(cost)==0.01 if cost is not None else False
per_ok = (q.get("period") or {}).get("type")=="calendar_duration"
sys.exit(0 if (req_ok and cost_ok and per_ok) else 1)
EOF
check "employee-test-key 挂 test-测试档（3 次/月 + \$0.01/月）" $?

# 5. 配额强制已开启且为 EXHAUSTED_ONLY
gql 'query { quotaEnforcementSettings { enabled mode } }' '{}' > .local/i3-enf.json 2>/dev/null || echo '{"errors":"gql failed"}' > .local/i3-enf.json
python3 - <<'EOF'
import json,sys
d=json.load(open(".local/i3-enf.json"))
s=(d.get("data") or {}).get("quotaEnforcementSettings") or {}
sys.exit(0 if (s.get("enabled") is True and s.get("mode")=="EXHAUSTED_ONLY") else 1)
EOF
check "quotaEnforcementSettings = enabled + EXHAUSTED_ONLY" $?

# 6. 超限拒绝：最多连打 5 次，3 次配额用完后应被拒（重跑容忍：若此前已耗尽则首击即拒也算过）
if [ -s .local/i3-test-key ]; then
  STATUSES=""
  for i in 1 2 3 4 5; do
    C=$(curl -s -o /dev/null -w "%{http_code}" "$GW/v1/chat/completions" \
      -H "Authorization: Bearer $(cat .local/i3-test-key)" -H "Content-Type: application/json" \
      -d '{"model":"mock-gpt","messages":[{"role":"user","content":"quota probe"}]}')
    STATUSES="$STATUSES $C"
    [ "$C" != "200" ] && break
  done
  STATUSES=$(echo "$STATUSES" | xargs)
  python3 - "$STATUSES" <<'EOF'
import sys
codes=sys.argv[1:]
ok200=[c for c in codes if c=="200"]
rej=[c for c in codes if c!="200"]
if not rej: sys.exit(1)                      # 打满 5 次都没被拒
if len(ok200)>3: sys.exit(1)                 # 成功次数超过配额 3，未生效
sys.exit(0)
EOF
  check "超限后被拒（状态序列：${STATUSES}）" $?
else
  echo "  FAIL: .local/i3-test-key 不存在"; FAIL=1
fi

echo "==> [slice3] 用量统计口径"

# 7. 测试档 key 的配额用量可查（requestCount≥1，三字段齐备）
TEST_KEY_GID=$(python3 -c 'import json
d=json.load(open(".local/i3-keys.json"))
ks=(d.get("data") or {}).get("apiKeys",{}).get("edges",[])
hit=[e["node"]["id"] for e in ks if e["node"]["name"]=="employee-test-key"]
print(hit[0] if hit else "")')
if [ -n "$TEST_KEY_GID" ]; then
  gql 'query($id: ID!) { apiKeyQuotaUsages(apiKeyId: $id) { profileName usage { requestCount totalTokens totalCost } } }' "{\"id\":\"$TEST_KEY_GID\"}" > .local/i3-usage.json 2>/dev/null || echo '{"errors":"gql failed"}' > .local/i3-usage.json
  python3 - <<'EOF'
import json,sys
d=json.load(open(".local/i3-usage.json"))
us=(d.get("data") or {}).get("apiKeyQuotaUsages") or []
hit=next((x for x in us if x.get("profileName")=="test-测试档"), None)
u=(hit or {}).get("usage") or {}
rc=u.get("requestCount"); tt=u.get("totalTokens"); tc=u.get("totalCost")
print(f"    实测用量（test-测试档）：requestCount={rc} totalTokens={tt} totalCost={tc}")
sys.exit(0 if (isinstance(rc,int) and rc>=1 and tt is not None and tc is not None) else 1)
EOF
  check "apiKeyQuotaUsages 返回测试档 key 用量（≥1 次）" $?
else
  echo "  FAIL: 找不到 employee-test-key id"; FAIL=1
fi

# 8. 默认档 key 的成本/请求统计接口可用（结构检查，数值打印留证）
gql 'query { costStatsByAPIKey(timeWindow: "month") { apiKeyId apiKeyName cost } }' '{}' > .local/i3-cost.json 2>/dev/null || echo '{"errors":"gql failed"}' > .local/i3-cost.json
python3 - <<'EOF'
import json,sys
d=json.load(open(".local/i3-cost.json"))
stats=(d.get("data") or {}).get("costStatsByAPIKey")
if not isinstance(stats,list): sys.exit(1)
print(f"    costStatsByAPIKey 返回 {len(stats)} 条：{[(s.get('apiKeyName'), s.get('cost')) for s in stats[:5]]}")
sys.exit(0)
EOF
check "costStatsByAPIKey 接口可用" $?

echo ""
if [ "$FAIL" = "0" ]; then echo "issue #3 验证：全部通过"; else echo "issue #3 验证：存在失败项"; exit 1; fi
