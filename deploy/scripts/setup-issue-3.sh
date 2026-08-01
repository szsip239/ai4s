#!/usr/bin/env bash
# issue #3 实施脚本（幂等）：测试员工 + 默认档 key + 默认档 profile
# TDD green 侧；验证用 ./scripts/verify-issue-3.sh
set -euo pipefail
cd "$(dirname "$0")/.."

AXONHUB_BASE="${AXONHUB_BASE:-http://localhost:8090}"
STATE_DIR=".local"
mkdir -p "$STATE_DIR"
TOKEN=$(cat "$STATE_DIR/admin-jwt")

gql() { # $1=query $2=variables(JSON)
  local payload
  payload=$(PAYLOAD_QUERY="$1" PAYLOAD_VARS="$2" python3 -c 'import json,os;print(json.dumps({"query":os.environ["PAYLOAD_QUERY"],"variables":json.loads(os.environ["PAYLOAD_VARS"])}))')
  curl -fsS -X POST "$AXONHUB_BASE/admin/graphql" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "$payload"
}

echo "==> 默认项目与 mock 渠道"
PROJECT_ID=$(gql 'query { myProjects { id name status } }' '{}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["myProjects"][0]["id"])')
MOCK_CH_ID=$(python3 -c 'import json;d=json.load(open(".local/channels.json"));gid=[e["node"]["id"] for e in d["data"]["queryChannels"]["edges"] if e["node"]["name"]=="mock-upstream-tracer"][0];print(gid.rsplit("/",1)[-1])')
echo "    项目 $PROJECT_ID / mock 渠道 $MOCK_CH_ID"

echo "==> 测试员工 employee-test@ai4s.local（幂等）"
EMP_EXISTS=$(gql 'query { users(first: 100) { edges { node { email } } } }' '{}' \
  | python3 -c 'import json,sys;print(1 if any(e["node"]["email"]=="employee-test@ai4s.local" for e in json.load(sys.stdin)["data"]["users"]["edges"]) else 0)')
if [ "$EMP_EXISTS" = "0" ]; then
  EMP_PW=$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')
  echo -n "$EMP_PW" > "$STATE_DIR/i3-employee-password"
  CREATE_USER=$(EMP_PW="$EMP_PW" PROJECT_ID="$PROJECT_ID" python3 -c 'import json,os;print(json.dumps({"input":{
    "email":"employee-test@ai4s.local","password":os.environ["EMP_PW"],
    "firstName":"测试","lastName":"员工","projectIDs":[os.environ["PROJECT_ID"]],
  }}))')
  gql 'mutation CreateUser($input: CreateUserInput!) { createUser(input: $input) { id email status } }' "$CREATE_USER"
  echo
  echo "    已创建（口令存 $STATE_DIR/i3-employee-password）"
else
  echo "    已存在，跳过"
fi

echo "==> employee-default-key（幂等）"
KEY_ID=$(gql 'query { apiKeys(first: 100) { edges { node { id name key } } } }' '{}' \
  | python3 -c 'import json,sys
ks=json.load(sys.stdin)["data"]["apiKeys"]["edges"]
hit=[e["node"] for e in ks if e["node"]["name"]=="employee-default-key"]
print(hit[0]["id"] if hit else "")')
if [ -z "$KEY_ID" ]; then
  RESP=$(gql 'mutation CreateAPIKey($input: CreateAPIKeyInput!) { createAPIKey(input: $input) { id key name status } }' \
    "{\"input\":{\"name\":\"employee-default-key\",\"projectID\":\"$PROJECT_ID\"}}")
  echo "$RESP"
  KEY_ID=$(echo "$RESP" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["createAPIKey"]["id"])')
  echo "$RESP" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["createAPIKey"]["key"])' > "$STATE_DIR/i3-default-key"
  echo "    已创建（${KEY_ID}）"
else
  echo "    已存在（${KEY_ID}），跳过创建"
fi

echo "==> 挂 default-标准档 profile（\$80/自然月、限 mock 渠道；可重放）"
PROFILES_JSON=$(KEY_ID="$KEY_ID" MOCK_CH_ID="$MOCK_CH_ID" python3 -c 'import json,os;print(json.dumps({
  "id": os.environ["KEY_ID"],
  "input": {
    "activeProfile": "default-标准档",
    "profiles": [{
      "name": "default-标准档",
      "channelIDs": [int(os.environ["MOCK_CH_ID"])],
      "quota": {"cost": "80", "period": {"type": "calendar_duration", "calendarDuration": {"unit": "month"}}},
    }],
  },
}))')
gql 'mutation UpdateProfiles($id: ID!, $input: UpdateAPIKeyProfilesInput!) { updateAPIKeyProfiles(id: $id, input: $input) { id profiles { activeProfile profiles { name } } } }' "$PROFILES_JSON"
echo

echo "==> employee-test-key（幂等）"
TEST_KEY_ID=$(gql 'query { apiKeys(first: 100) { edges { node { id name } } } }' '{}' \
  | python3 -c 'import json,sys
ks=json.load(sys.stdin)["data"]["apiKeys"]["edges"]
hit=[e["node"] for e in ks if e["node"]["name"]=="employee-test-key"]
print(hit[0]["id"] if hit else "")')
if [ -z "$TEST_KEY_ID" ]; then
  RESP=$(gql 'mutation CreateAPIKey($input: CreateAPIKeyInput!) { createAPIKey(input: $input) { id key name status } }' \
    "{\"input\":{\"name\":\"employee-test-key\",\"projectID\":\"$PROJECT_ID\"}}")
  echo "$RESP"
  TEST_KEY_ID=$(echo "$RESP" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["createAPIKey"]["id"])')
  echo "$RESP" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["createAPIKey"]["key"])' > "$STATE_DIR/i3-test-key"
  echo "    已创建（${TEST_KEY_ID}）"
else
  echo "    已存在（${TEST_KEY_ID}），跳过创建"
fi

echo "==> 挂 test-测试档 profile（3 次/月 + \$0.01/月、限 mock 渠道；可重放）"
TEST_PROFILES_JSON=$(TEST_KEY_ID="$TEST_KEY_ID" MOCK_CH_ID="$MOCK_CH_ID" python3 -c 'import json,os;print(json.dumps({
  "id": os.environ["TEST_KEY_ID"],
  "input": {
    "activeProfile": "test-测试档",
    "profiles": [{
      "name": "test-测试档",
      "channelIDs": [int(os.environ["MOCK_CH_ID"])],
      "quota": {"requests": 3, "cost": "0.01", "period": {"type": "calendar_duration", "calendarDuration": {"unit": "month"}}},
    }],
  },
}))')
gql 'mutation UpdateProfiles($id: ID!, $input: UpdateAPIKeyProfilesInput!) { updateAPIKeyProfiles(id: $id, input: $input) { id profiles { activeProfile profiles { name } } } }' "$TEST_PROFILES_JSON"
echo

echo "==> 开启配额强制（EXHAUSTED_ONLY；可重放）"
gql 'mutation UpdateEnf($input: UpdateQuotaEnforcementSettingsInput!) { updateQuotaEnforcementSettings(input: $input) }' \
  '{"input":{"enabled":true,"mode":"EXHAUSTED_ONLY"}}'
echo

echo "完成。运行 ./scripts/verify-issue-3.sh 验证"
