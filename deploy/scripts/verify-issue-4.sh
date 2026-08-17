#!/usr/bin/env bash
# issue #4 验证脚本（测试即票据）：观测与备份基线
# seam：管理 API（trace/usage 查询）+ 备份/恢复脚本 + compose 重启
# 用法：./scripts/verify-issue-4.sh   —— 全绿退出码 0；含 compose down/up 重启演练（最后执行）
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

echo "==> [sliceA] 请求 trace 与用量可见"

# 打一发留痕
curl -s -o /dev/null "$GW/v1/chat/completions" \
  -H "Authorization: Bearer $(cat .local/test-api-key)" -H "Content-Type: application/json" \
  -d '{"model":"mock-gpt","messages":[{"role":"user","content":"trace probe"}]}'
sleep 2

# 1. 最新请求的 usageLog 含渠道/token/成本字段
gql 'query { usageLogs(first: 1) { edges { node { channelID modelID totalTokens totalCost source } } } }' '{}' > .local/i4-usage.json 2>/dev/null || echo '{"errors":"gql failed"}' > .local/i4-usage.json
python3 - <<'EOF'
import json,sys
d=json.load(open(".local/i4-usage.json"))
edges=(d.get("data") or {}).get("usageLogs",{}).get("edges",[])
if not edges: sys.exit(1)
n=edges[0]["node"]
print(f"    最新 usageLog: channel={n.get('channelID')} model={n.get('modelID')} tokens={n.get('totalTokens')} cost={n.get('totalCost')}")
# totalCost 对无刊例价模型（mock）为 null 属合法；断言字段在响应结构中存在（口径字段就位）
sys.exit(0 if (n.get("channelID") and n.get("modelID") and n.get("totalTokens") is not None and "totalCost" in n) else 1)
EOF
check "usageLog 含渠道/模型/token/成本字段" $?

# 2. 请求追踪全开：请求/响应体完整留痕（调试期基线，阶段 1 将收敛为元数据）
gql 'query { requests(last: 1) { edges { node { status requestBody responseBody metricsLatencyMs } } } }' '{}' > .local/i4-req.json 2>/dev/null || echo '{"errors":"gql failed"}' > .local/i4-req.json
python3 - <<'EOF'
import json,sys
d=json.load(open(".local/i4-req.json"))
edges=(d.get("data") or {}).get("requests",{}).get("edges",[])
if not edges: sys.exit(1)
n=edges[0]["node"]
rb=n.get("requestBody") or {}
sb=n.get("responseBody") or {}
body_ok = bool(rb.get("messages")) and bool(sb)
print(f"    最新 request: status={n.get('status')} 请求体含 messages={bool(rb.get('messages'))} 响应体非空={bool(sb)} latency={n.get('metricsLatencyMs')}ms")
sys.exit(0 if (n.get("status")=="completed" and body_ok) else 1)
EOF
check "请求/响应体完整留痕（追踪全开）" $?

echo "==> [sliceB] 备份 → 隔离实例恢复演练"

# 3. 日备脚本产出新备份
BEFORE=$(ls -t backups/*.sql.gz 2>/dev/null | head -1 || true)
./scripts/pg-backup.sh >/dev/null 2>&1
AFTER=$(ls -t backups/*.sql.gz 2>/dev/null | head -1 || true)
[ -n "$AFTER" ] && [ "$AFTER" != "$BEFORE" -o ! -f .local/i4-backup-done ]
check "pg-backup.sh 产出备份（${AFTER##*/}）" $?
touch .local/i4-backup-done

# 4. 隔离 PG 实例恢复：scratch 容器载入 dump，断言表与关键行
BACKUP=$(ls -t backups/*.sql.gz | head -1)
SCRATCH=ai4s-pg-restore-drill
docker rm -f "$SCRATCH" >/dev/null 2>&1 || true
docker run --rm -d --name "$SCRATCH" -e POSTGRES_PASSWORD=restore-drill postgres:16-alpine >/dev/null 2>&1
for i in $(seq 1 30); do
  docker exec "$SCRATCH" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1
done
set +e
docker exec -i "$SCRATCH" createdb -U postgres axonhub_restore 2>/dev/null
gunzip -c "$BACKUP" | docker exec -i "$SCRATCH" psql -U postgres -d axonhub_restore -q >/dev/null 2>&1
RESTORE_RC=$?
set -e
[ "$RESTORE_RC" = "0" ]
check "dump 载入隔离实例成功（${BACKUP##*/}）" $?

TABLES=$(docker exec -i "$SCRATCH" psql -U postgres -d axonhub_restore -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
CH_ROWS=$(docker exec -i "$SCRATCH" psql -U postgres -d axonhub_restore -tAc "SELECT count(*) FROM channels WHERE name='mock-upstream-tracer'" 2>/dev/null || echo 0)
KEY_ROWS=$(docker exec -i "$SCRATCH" psql -U postgres -d axonhub_restore -tAc "SELECT count(*) FROM api_keys WHERE name='employee-default-key'" 2>/dev/null || echo 0)
echo "    隔离实例：表 ${TABLES} 张，mock 渠道行 ${CH_ROWS}，默认档 key 行 ${KEY_ROWS}"
[ "${TABLES:-0}" -ge 20 ] && [ "${CH_ROWS:-0}" = "1" ] && [ "${KEY_ROWS:-0}" = "1" ]
check "隔离实例数据完整（≥20 表、mock 渠道、默认档 key 可恢复）" $?
docker rm -f "$SCRATCH" >/dev/null 2>&1 || true

echo "==> [sliceC] compose 重启持久化演练（down && up）"

# 5. 重启前实体计数
count_entities() {
  gql 'query($input: QueryChannelInput!) { queryChannels(input: $input) { edges { node { id } } } }' '{"input":{"first":200,"where":{"statusIn":["enabled","disabled","archived"]}}}' > .local/i4-c1.json 2>/dev/null
  gql 'query { users(first: 100) { edges { node { id } } } }' '{}' > .local/i4-u1.json 2>/dev/null
  gql 'query { apiKeys(first: 100) { edges { node { id } } } }' '{}' > .local/i4-k1.json 2>/dev/null
  python3 - <<'EOF'
import json
def n(p):
    d=json.load(open(p)); return len(list((d.get("data") or {}).values())[0]["edges"])
print(n(".local/i4-c1.json"), n(".local/i4-u1.json"), n(".local/i4-k1.json"))
EOF
}
read CH0 U0 K0 <<< "$(count_entities)"
echo "    重启前：渠道 ${CH0} 用户 ${U0} key ${K0}"

docker compose --profile mock down >/dev/null 2>&1
docker compose up -d >/dev/null 2>&1
for i in $(seq 1 45); do curl -sf http://localhost:15001/healthz/ready >/dev/null 2>&1 && break; sleep 2; done
./scripts/bootstrap.sh >/dev/null 2>&1   # 幂等：确保健康 + mock 渠道在

read CH1 U1 K1 <<< "$(count_entities)"
echo "    重启后：渠道 ${CH1} 用户 ${U1} key ${K1}"
[ "$CH0" = "$CH1" ] && [ "$U0" = "$U1" ] && [ "$K0" = "$K1" ]
check "重启后渠道/用户/key 计数一致（持久化无损）" $?

# 6. 重启后链路仍可用（冒烟）
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$GW/v1/chat/completions" \
  -H "Authorization: Bearer $(cat .local/test-api-key)" -H "Content-Type: application/json" \
  -d '{"model":"mock-gpt","messages":[{"role":"user","content":"post-restart probe"}]}')
[ "$CODE" = "200" ]
check "重启后端到端调用 200（实得 ${CODE}）" $?

echo ""
if [ "$FAIL" = "0" ]; then echo "issue #4 验证：全部通过"; else echo "issue #4 验证：存在失败项"; exit 1; fi
