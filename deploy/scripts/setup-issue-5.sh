#!/usr/bin/env bash
# issue #5 实施脚本（幂等）：axonhub 原生 Prompt Protection Rules（纵深层）
# 主层在 agentgateway（deploy/agentgateway/config.yaml），本层为纵深。
# TDD green 侧；验证用 ./scripts/verify-issue-5.sh
set -euo pipefail
cd "$(dirname "$0")/.."

AXONHUB_BASE="${AXONHUB_BASE:-http://localhost:8090}"
TOKEN=$(cat .local/admin-jwt)

gql() {
  local payload
  payload=$(PAYLOAD_QUERY="$1" PAYLOAD_VARS="$2" python3 -c 'import json,os;print(json.dumps({"query":os.environ["PAYLOAD_QUERY"],"variables":json.loads(os.environ["PAYLOAD_VARS"])}))')
  curl -fsS -X POST "$AXONHUB_BASE/admin/graphql" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "$payload"
}

ensure_rule() { # $1=name $2=pattern $3=desc
  local name="$1" pattern="$2" desc="$3"
  local exists
  exists=$(gql 'query { promptProtectionRules(first: 100) { edges { node { name } } } }' '{}' \
    | python3 -c 'import json,sys
name=sys.argv[1]
edges=json.load(sys.stdin)["data"]["promptProtectionRules"]["edges"]
print(1 if any(e["node"]["name"]==name for e in edges) else 0)' "$name")
  if [ "$exists" = "1" ]; then
    echo "    规则 ${name} 已存在，跳过"
    return
  fi
  local input
  input=$(R_NAME="$name" R_PATTERN="$pattern" R_DESC="$desc" python3 -c 'import json,os;print(json.dumps({"input":{
    "name":os.environ["R_NAME"],
    "description":os.environ["R_DESC"],
    "pattern":os.environ["R_PATTERN"],
    "settings":{"action":"reject"},
  }}))')
  gql 'mutation CreateRule($input: CreatePromptProtectionRuleInput!) { createPromptProtectionRule(input: $input) { id name status settings { action } } }' "$input"
  echo
}

echo "==> axonhub Prompt Protection Rules（纵深层，reject 语义）"
ensure_rule "secrets-openai-sk"   'sk-(proj-)?[A-Za-z0-9_\-]{20,}'        'OpenAI API key（含 sk-proj-）外泄阻断（axonhub 纵深层；主层在 agentgateway）'
ensure_rule "secrets-aws-key"     'AKIA[0-9A-Z]{16}'                      'AWS AccessKey ID 外泄阻断（axonhub 纵深层）'
ensure_rule "secrets-private-key" '-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----' '私钥材料外泄阻断（axonhub 纵深层）'

enable_rule() { # $1=name —— 按名查 id 并确保 enabled（可重放；创建默认为 disabled）
  local name="$1" rid
  rid=$(gql 'query { promptProtectionRules(first: 100) { edges { node { id name } } } }' '{}' \
    | python3 -c 'import json,sys
name=sys.argv[1]
edges=json.load(sys.stdin)["data"]["promptProtectionRules"]["edges"]
hit=[e["node"]["id"] for e in edges if e["node"]["name"]==name]
print(hit[0] if hit else "")' "$name")
  if [ -z "$rid" ]; then echo "    ERROR: 规则 ${name} 不存在" >&2; return 1; fi
  gql 'mutation EnableRule($id: ID!, $status: PromptProtectionRuleStatus!) { updatePromptProtectionRuleStatus(id: $id, status: $status) }' \
    "{\"id\":\"$rid\",\"status\":\"enabled\"}" >/dev/null
  echo "    规则 ${name} 已启用（${rid}）"
}

echo "==> 启用规则"
enable_rule "secrets-openai-sk"
enable_rule "secrets-aws-key"
enable_rule "secrets-private-key"

echo "完成。运行 ./scripts/verify-issue-5.sh 验证"
