#!/usr/bin/env bash
# issue #2（降级）实施脚本（幂等）：假凭据 OAuth 渠道 + 多渠道 key
# TDD green 侧；验证用 ./scripts/verify-issue-2.sh
set -euo pipefail
cd "$(dirname "$0")/.."

AXONHUB_BASE="${AXONHUB_BASE:-http://localhost:8090}"
STATE_DIR=".local"
TOKEN=$(cat "$STATE_DIR/admin-jwt")

gql() {
  local payload
  payload=$(PAYLOAD_QUERY="$1" PAYLOAD_VARS="$2" python3 -c 'import json,os;print(json.dumps({"query":os.environ["PAYLOAD_QUERY"],"variables":json.loads(os.environ["PAYLOAD_VARS"])}))')
  curl -fsS -X POST "$AXONHUB_BASE/admin/graphql" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "$payload"
}

ensure_channel() { # $1=name $2=type $3=models(json array) $4=testmodel -> echoes gid
  local name="$1" type="$2" models="$3" testmodel="$4"
  local gid
  gid=$(gql 'query($input: QueryChannelInput!) { queryChannels(input: $input) { edges { node { id name status } } } }' \
    '{"input":{"first":200,"where":{"statusIn":["enabled","disabled","archived"]}}}' \
    | python3 -c 'import json,sys
name=sys.argv[1]
edges=json.load(sys.stdin)["data"]["queryChannels"]["edges"]
hit=[e["node"]["id"] for e in edges if e["node"]["name"]==name]
print(hit[0] if hit else "")' "$name")
  if [ -z "$gid" ]; then
    local input
    input=$(CH_NAME="$name" CH_TYPE="$type" CH_MODELS="$models" CH_TM="$testmodel" python3 -c 'import json,os;print(json.dumps({"input":{
      "type":os.environ["CH_TYPE"],"name":os.environ["CH_NAME"],
      "credentials":{"apiKey":"{\"access_token\":\"fake-invalid-token-negative-path\"}"},
      "supportedModels":json.loads(os.environ["CH_MODELS"]),
      "defaultTestModel":os.environ["CH_TM"],
      "remark":"issue #2 降级：假凭据负路径渠道，凭据到位后替换或删除",
    }}))')
    local resp
    resp=$(gql 'mutation CreateChannel($input: CreateChannelInput!) { createChannel(input: $input) { id name type status } }' "$input")
    echo "$resp" >&2
    gid=$(echo "$resp" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["createChannel"]["id"])')
    echo "    已创建 ${name}（${gid}）" >&2
  else
    echo "    ${name} 已存在（${gid}），跳过" >&2
  fi
  # 确保 enabled
  gql 'mutation EnableChannel($id: ID!, $status: ChannelStatus!) { updateChannelStatus(id: $id, status: $status) { id status } }' \
    "{\"id\":\"$gid\",\"status\":\"enabled\"}" >/dev/null
  echo "$gid"
}

echo "==> 假凭据 OAuth 渠道（负路径）"
CODEX_GID=$(ensure_channel "codex-fake-negative" "codex" '["gpt-5"]' "gpt-5")
CLAUDE_GID=$(ensure_channel "claudecode-fake-negative" "claudecode" '["claude-sonnet-4-5"]' "claude-sonnet-4-5")
CODEX_NUM=${CODEX_GID##*/}
CLAUDE_NUM=${CLAUDE_GID##*/}
echo "    codex-fake=$CODEX_GID claudecode-fake=$CLAUDE_GID"

echo "==> 假渠道模型列表改为 mock-gpt（让模型选择横跨真假渠道，暴露负路径）"
for GID in "$CODEX_GID" "$CLAUDE_GID"; do
  gql 'mutation UpdateChannel($id: ID!, $input: UpdateChannelInput!) { updateChannel(id: $id, input: $input) { id supportedModels } }' \
    "{\"id\":\"$GID\",\"input\":{\"supportedModels\":[\"mock-gpt\"],\"defaultTestModel\":\"mock-gpt\"}}" >/dev/null
done
echo "    codex-fake / claudecode-fake 均支持 mock-gpt"

echo "==> i2-multi-channel-key（profile 覆盖 mock + 2 假渠道；幂等）"
PROJECT_ID=$(gql 'query { myProjects { id name } }' '{}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["myProjects"][0]["id"])')
KEY_ID=$(gql 'query { apiKeys(first: 100) { edges { node { id name } } } }' '{}' \
  | python3 -c 'import json,sys
ks=json.load(sys.stdin)["data"]["apiKeys"]["edges"]
hit=[e["node"] for e in ks if e["node"]["name"]=="i2-multi-channel-key"]
print(hit[0]["id"] if hit else "")')
if [ -z "$KEY_ID" ]; then
  RESP=$(gql 'mutation CreateAPIKey($input: CreateAPIKeyInput!) { createAPIKey(input: $input) { id key name status } }' \
    "{\"input\":{\"name\":\"i2-multi-channel-key\",\"projectID\":\"$PROJECT_ID\"}}")
  echo "$RESP"
  KEY_ID=$(echo "$RESP" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["createAPIKey"]["id"])')
  echo "$RESP" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["createAPIKey"]["key"])' > "$STATE_DIR/i2-multi-key"
  echo "    已创建（${KEY_ID}）"
else
  echo "    已存在（${KEY_ID}），跳过创建"
fi

PROFILES_JSON=$(KEY_ID="$KEY_ID" C1="$CODEX_NUM" C2="$CLAUDE_NUM" python3 -c 'import json,os;print(json.dumps({
  "id": os.environ["KEY_ID"],
  "input": {
    "activeProfile": "multi-多渠道档",
    "profiles": [{
      "name": "multi-多渠道档",
      "channelIDs": [1, int(os.environ["C1"]), int(os.environ["C2"])],
    }],
  },
}))')
gql 'mutation UpdateProfiles($id: ID!, $input: UpdateAPIKeyProfilesInput!) { updateAPIKeyProfiles(id: $id, input: $input) { id profiles { activeProfile profiles { name channelIDs } } } }' "$PROFILES_JSON"
echo

echo "完成。运行 ./scripts/verify-issue-2.sh 验证"
