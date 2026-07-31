#!/usr/bin/env bash
# 端到端验证：本机 curl → agentgateway(:3000) → axonhub → 渠道上游，完成一次 chat completion 往返。
set -euo pipefail
cd "$(dirname "$0")/.."

GATEWAY_BASE="${GATEWAY_BASE:-http://localhost:3000}"
KEY_FILE=".local/test-api-key"
MODEL_FILE=".local/test-model"

[ -s "$KEY_FILE" ] || { echo "ERROR: 未找到 ${KEY_FILE}，先运行 ./scripts/bootstrap.sh" >&2; exit 1; }
API_KEY=$(cat "$KEY_FILE")
MODEL=$(cat "$MODEL_FILE")

echo "==> agentgateway readiness"
curl -fsS http://localhost:15001/healthz/ready && echo || { echo "ERROR: agentgateway 未就绪" >&2; exit 1; }

echo "==> POST $GATEWAY_BASE/v1/chat/completions (model=$MODEL)"
RESP=$(curl -fsS -X POST "$GATEWAY_BASE/v1/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with the word ok\"}],\"max_tokens\":16}")
echo "$RESP" | python3 -m json.tool
echo "$RESP" | python3 -c "
import json,sys
d=json.load(sys.stdin)
content=d['choices'][0]['message']['content']
assert content, 'empty content'
print('==> 链路验证通过，模型回复:', content[:120])"
