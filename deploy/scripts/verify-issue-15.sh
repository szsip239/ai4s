#!/usr/bin/env bash
# issue #15 验证脚本（测试即票据）：PII 双层脱敏（mask 为主）
# seam：agentgateway /v1 调用面 + axonhub 留痕观测（requestBody 是否脱敏）
# 用法：./scripts/verify-issue-15.sh   —— 全绿退出码 0
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

# 发一条含 PII 的请求，回显 "HTTP码"
send() {
  python3 -c 'import json,sys;print(json.dumps({"model":"mock-gpt","messages":[{"role":"user","content":sys.argv[1]}]}))' "$1" > .local/i15-body.json
  curl -s -o /dev/null -w "%{http_code}" "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $(cat .local/test-api-key)" -H "Content-Type: application/json" \
    --data-binary @.local/i15-body.json
}

# 查最近一次请求的 requestBody 是否含原文（应已脱敏）
last_body() {
  gql 'query { requests(last: 1) { edges { node { requestBody createdAt } } } }' '{}' \
    | python3 -c 'import json,sys
e=(json.load(sys.stdin).get("data") or {}).get("requests",{}).get("edges",[])
print(json.dumps(e[0]["node"]["requestBody"],ensure_ascii=False) if e else "")'
}

echo "==> [sliceA] L1 regex mask：手机号/身份证 脱敏放行"

CODE=$(send "我手机号是 13800138000，有事打这个")
echo "    [手机号] HTTP ${CODE}"
[ "$CODE" = "200" ]
check "含手机号请求 200 放行（mask 不阻断）" $?
sleep 2
BODY=$(last_body)
echo "    留痕: ${BODY:0:160}"
echo "$BODY" | grep -q "13800138000" && { check "手机号原文已被脱敏" 1; } || check "手机号原文已被脱敏（留痕无原文）" 0

CODE=$(send "身份证号 110101199003071234 帮我登记一下")
echo "    [身份证] HTTP ${CODE}"
[ "$CODE" = "200" ]
check "含身份证请求 200 放行" $?
sleep 2
BODY=$(last_body)
echo "    留痕: ${BODY:0:160}"
echo "$BODY" | grep -q "110101199003071234" && { check "身份证原文已被脱敏" 1; } || check "身份证原文已被脱敏（留痕无原文）" 0

echo "==> [sliceB] shim/Presidio 中文 recognizer：银行卡号 脱敏放行"

CODE=$(send "我的银行卡号 6222021234567890123，转账用这个")
echo "    [银行卡] HTTP ${CODE}"
[ "$CODE" = "200" ]
check "含银行卡号请求 200 放行" $?
sleep 2
BODY=$(last_body)
echo "    留痕: ${BODY:0:160}"
echo "$BODY" | grep -q "6222021234567890123" && { check "银行卡号原文已被脱敏" 1; } || check "银行卡号原文已被脱敏（留痕无原文）" 0

echo "==> [sliceC] 负样例：普通数字不 mask"

CODE=$(send "订单号 20260802 共 4 件，价格 13800 元")
echo "    [负样例] HTTP ${CODE}"
[ "$CODE" = "200" ]
check "负样例 200 放行" $?
sleep 2
BODY=$(last_body)
echo "    留痕: ${BODY:0:160}"
echo "$BODY" | grep -q "13800" && check "普通数字未被误脱敏" 0 || check "普通数字未被误脱敏" 1

echo ""
if [ "$FAIL" = "0" ]; then echo "issue #15 验证：全部通过"; else echo "issue #15 验证：存在失败项"; exit 1; fi
