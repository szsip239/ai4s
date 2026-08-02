#!/usr/bin/env bash
# issue #16 验证脚本（测试即票据）：Langfuse 审计旁路 + 留痕收敛
# seam：Langfuse OTLP/REST API + axonhub 管理 API + agentgateway /v1 调用面
# 用法：./scripts/verify-issue-16.sh   —— 全绿退出码 0
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
FAIL=0

check() { if [ "$2" = "0" ]; then echo "  PASS: $1"; else echo "  FAIL: $1"; FAIL=1; fi; }

echo "==> [sliceA] Langfuse 服务与项目密钥"

CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://localhost:3001/api/public/health)
[ "$CODE" = "200" ]
check "langfuse-web 健康（实得 ${CODE}）" $?

# 项目密钥可用（REST API 鉴权通过）
CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -u "$LANGFUSE_PK:$LANGFUSE_SK" http://localhost:3001/api/public/traces?limit=1)
[ "$CODE" = "200" ]
check "项目 pk/sk 鉴权可用（实得 ${CODE}）" $?

echo "==> [sliceB] 打一发请求，OTLP trace 落入 Langfuse"

BEFORE=$(date -u +%s)
curl -s -o /dev/null -w "打桩请求: %{http_code}\n" -X POST http://localhost:3000/v1/chat/completions \
  -H "Authorization: Bearer $(cat .local/test-api-key)" -H "Content-Type: application/json" \
  -d '{"model":"mock-gpt","messages":[{"role":"user","content":"langfuse trace probe"}]}'
sleep 8   # OTLP 批量上报窗口

TRACES=$(curl -s --max-time 10 -u "$LANGFUSE_PK:$LANGFUSE_SK" "http://localhost:3001/api/public/traces?limit=20")
COUNT=$(python3 -c 'import json,sys
d=json.load(sys.stdin)
print(len(d.get("data") or []))' <<< "$TRACES" 2>/dev/null || echo 0)
echo "    Langfuse 现有 trace 数: ${COUNT}"
[ "${COUNT:-0}" -ge 1 ]
check "OTLP trace 已落入 Langfuse（≥1 条）" $?

echo "==> [sliceC] trace 不含 prompt/completion 原文（契约红线）"

python3 - <<'EOF'
import json, os, urllib.request, base64
pk, sk = os.environ["LANGFUSE_PK"], os.environ["LANGFUSE_SK"]
req = urllib.request.Request("http://localhost:3001/api/public/traces?limit=10")
req.add_header("Authorization", "Basic " + base64.b64encode(f"{pk}:{sk}".encode()).decode())
d = json.load(urllib.request.urlopen(req, timeout=10))
blob = json.dumps(d, ensure_ascii=False).lower()
banned = ["langfuse trace probe", "gen_ai.prompt", "gen_ai.completion", "\"prompt\":", "mock upstream reply"]
hits = [b for b in banned if b in blob]
print(f"    红线词扫描命中: {hits}")
import sys
sys.exit(1 if hits else 0)
EOF
check "Langfuse 数据中无 prompt/completion 原文" $?

echo "==> [sliceD] axonhub 留痕收敛生效"

JWT=$(cat .local/admin-jwt)
# 新请求的 requestBody 应为空（storeRequestBody=false 已生效）
curl -s -o /dev/null -X POST http://localhost:3000/v1/chat/completions \
  -H "Authorization: Bearer $(cat .local/test-api-key)" -H "Content-Type: application/json" \
  -d '{"model":"mock-gpt","messages":[{"role":"user","content":"storage off probe"}]}'
sleep 2
python3 - <<'EOF'
import json, urllib.request
req = urllib.request.Request("http://localhost:8090/admin/graphql",
    data=json.dumps({"query":"{ requests(last: 1) { edges { node { requestBody status createdAt } } } }","variables":{}}).encode(),
    headers={"Authorization":"Bearer "+open(".local/admin-jwt").read().strip(),"Content-Type":"application/json"})
d = json.load(urllib.request.urlopen(req))
e = (d.get("data") or {}).get("requests",{}).get("edges",[])
body = e[0]["node"].get("requestBody") if e else "NO_RECORD"
empty = (body == {} or body == {} or body is None or (isinstance(body,dict) and not body.get("messages")))
print(f"    最新请求 requestBody: {json.dumps(body)[:80] if body else body}")
import sys
sys.exit(0 if empty else 1)
EOF
check "新请求 requestBody 已为空（留痕收敛生效）" $?

# storagePolicy 断言
python3 - <<'EOF'
import json, urllib.request
req = urllib.request.Request("http://localhost:8090/admin/graphql",
    data=json.dumps({"query":"{ storagePolicy { storeRequestBody storeResponseBody cleanupOptions { resourceType enabled cleanupDays } } }","variables":{}}).encode(),
    headers={"Authorization":"Bearer "+open(".local/admin-jwt").read().strip(),"Content-Type":"application/json"})
p = (json.load(urllib.request.urlopen(req)).get("data") or {}).get("storagePolicy") or {}
co = {(o["resourceType"], o["cleanupDays"], o["enabled"]) for o in (p.get("cleanupOptions") or [])}
ok = (p.get("storeRequestBody") is False and p.get("storeResponseBody") is False
      and ("requests",7,True) in co and ("usage_logs",90,True) in co)
print(f"    storagePolicy: body off={p.get('storeRequestBody')},{p.get('storeResponseBody')} cleanup={sorted(co)}")
import sys
sys.exit(0 if ok else 1)
EOF
check "storagePolicy = 双体关闭 + requests:7d + usage_logs:90d" $?

echo ""
if [ "$FAIL" = "0" ]; then echo "issue #16 验证：全部通过"; else echo "issue #16 验证：存在失败项"; exit 1; fi
