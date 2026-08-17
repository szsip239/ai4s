#!/usr/bin/env bash
# ai4s 阶段 0 bootstrap（幂等）：
#   1. 等待 axonhub 健康
#   2. 首次运行时初始化管理面 owner 本地账号
#   3. 登录获取 JWT
#   4. 创建测试渠道（.env 有 CODEX_OAUTH_JSON / CLAUDECODE_OAUTH_JSON 用真实 OAuth 渠道，否则用 mock 上游渠道）
#   5. 创建测试 API key，写入 deploy/.local/（gitignored）
# 注意：macOS 自带 bash 3.2 对 $() 内嵌套双引号解析有 bug，JSON 一律用单引号 python3 -c 生成。
set -euo pipefail
cd "$(dirname "$0")/.."

AXONHUB_BASE="${AXONHUB_BASE:-http://localhost:3000}"
STATE_DIR=".local"
mkdir -p "$STATE_DIR"

if [ ! -f .env ]; then
  echo "ERROR: deploy/.env 不存在，先 cp .env.example .env 并填写" >&2
  exit 1
fi
set -a; . ./.env; set +a

echo "==> 等待 axonhub 健康（轮 docker healthcheck；:3000/health 已按 issue #62 撤掉，避免版本指纹泄到员工面）"
for i in $(seq 1 60); do
  HEALTH=$(docker inspect -f '{{.State.Health.Status}}' ai4s-axonhub 2>/dev/null || echo missing)
  if [ "$HEALTH" = "healthy" ]; then break; fi
  if [ "$i" = 60 ]; then echo "ERROR: axonhub 60 次重试后仍不健康（docker health: $HEALTH）" >&2; exit 1; fi
  sleep 2
done
echo "    axonhub docker health: healthy"

echo "==> 检查系统初始化状态"
IS_INIT=$(curl -fsS "$AXONHUB_BASE/admin/system/status" | python3 -c 'import json,sys;print(json.load(sys.stdin)["isInitialized"])')
if [ "$IS_INIT" = "False" ]; then
  echo "==> 首次初始化 owner 账号 $AXONHUB_ADMIN_EMAIL"
  INIT_JSON=$(python3 -c 'import json,os;print(json.dumps({
    "ownerEmail":os.environ["AXONHUB_ADMIN_EMAIL"],
    "ownerPassword":os.environ["AXONHUB_ADMIN_PASSWORD"],
    "ownerFirstName":os.environ.get("AXONHUB_ADMIN_FIRST_NAME","Admin"),
    "ownerLastName":os.environ.get("AXONHUB_ADMIN_LAST_NAME","User"),
    "brandName":"Ai-4S-infra",
    "preferLanguage":"zh-CN",
  }))')
  curl -fsS -X POST "$AXONHUB_BASE/admin/system/initialize" -H 'Content-Type: application/json' -d "$INIT_JSON"
  echo
else
  echo "    已初始化，跳过"
fi

echo "==> 登录获取 JWT"
LOGIN_JSON=$(python3 -c 'import json,os;print(json.dumps({"email":os.environ["AXONHUB_ADMIN_EMAIL"],"password":os.environ["AXONHUB_ADMIN_PASSWORD"]}))')
TOKEN=$(curl -fsS -X POST "$AXONHUB_BASE/admin/auth/signin" -H 'Content-Type: application/json' -d "$LOGIN_JSON" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
echo "    登录成功（管理面 http://localhost:3000 用 .env 中的账号登录）"
echo -n "$TOKEN" > "$STATE_DIR/admin-jwt"

gql() { # $1=query $2=variables(JSON)
  local payload
  payload=$(PAYLOAD_QUERY="$1" PAYLOAD_VARS="$2" python3 -c 'import json,os;print(json.dumps({"query":os.environ["PAYLOAD_QUERY"],"variables":json.loads(os.environ["PAYLOAD_VARS"])}))')
  curl -fsS -X POST "$AXONHUB_BASE/admin/graphql" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "$payload"
}

echo "==> 查询默认项目与已有渠道"
PROJECT_ID=$(gql 'query { myProjects { id name status } }' '{}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["myProjects"][0]["id"])')
echo "    默认项目: $PROJECT_ID"
CHANNELS_JSON=$(gql 'query QueryChannelNames($input: QueryChannelInput!) { queryChannels(input: $input) { edges { node { id name status } } } }' \
  '{"input":{"first":200,"where":{"statusIn":["enabled","disabled","archived"]}}}')
echo "$CHANNELS_JSON" > "$STATE_DIR/channels.json"

# --- 决定渠道形态 ---
if [ -n "${CODEX_OAUTH_JSON:-}" ]; then
  MODE=codex
  CH_NAME="codex-oauth-tracer"
  CH_INPUT=$(python3 -c 'import json,os;print(json.dumps({"input":{
    "type":"codex","name":"codex-oauth-tracer",
    "credentials":{"apiKey":os.environ["CODEX_OAUTH_JSON"]},
    "supportedModels":["gpt-5","gpt-5-codex"],
    "defaultTestModel":"gpt-5",
  }}))')
elif [ -n "${CLAUDECODE_OAUTH_JSON:-}" ]; then
  MODE=claudecode
  CH_NAME="claudecode-oauth-tracer"
  CH_INPUT=$(python3 -c 'import json,os;print(json.dumps({"input":{
    "type":"claudecode","name":"claudecode-oauth-tracer",
    "credentials":{"apiKey":os.environ["CLAUDECODE_OAUTH_JSON"]},
    "supportedModels":["claude-sonnet-4-5","claude-opus-4-1"],
    "defaultTestModel":"claude-sonnet-4-5",
  }}))')
else
  MODE=mock
  CH_NAME="mock-upstream-tracer"
  CH_INPUT='{"input":{"type":"openai","name":"mock-upstream-tracer","baseURL":"http://mock-upstream:8080/v1","credentials":{"apiKey":"mock-key"},"supportedModels":["mock-gpt"],"defaultTestModel":"mock-gpt"}}'
fi

if [ "$MODE" = mock ]; then
  echo "==> 确保 mock 上游容器运行"
  docker compose --profile mock up -d --wait mock-upstream
fi

CH_ID=$(CH_NAME="$CH_NAME" CHANNELS_JSON_PATH="$STATE_DIR/channels.json" python3 -c '
import json,os
data=json.load(open(os.environ["CHANNELS_JSON_PATH"]))
for e in data["data"]["queryChannels"]["edges"]:
    if e["node"]["name"]==os.environ["CH_NAME"]:
        print(e["node"]["id"]); break
' || true)

if [ -z "$CH_ID" ]; then
  echo "==> 创建渠道 ${CH_NAME}（模式：${MODE}）"
  RESP=$(gql 'mutation CreateChannel($input: CreateChannelInput!) { createChannel(input: $input) { id name status } }' "$CH_INPUT")
  echo "$RESP"
  CH_ID=$(echo "$RESP" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["createChannel"]["id"])')
else
  echo "    渠道 $CH_NAME 已存在（${CH_ID}），跳过创建"
fi

echo "==> 确保渠道处于 enabled"
RESP=$(gql 'mutation EnableChannel($id: ID!, $status: ChannelStatus!) { updateChannelStatus(id: $id, status: $status) { id name status } }' \
  "{\"id\":\"$CH_ID\",\"status\":\"enabled\"}")
echo "$RESP"
echo "$RESP" | python3 -c 'import json,sys;assert json.load(sys.stdin)["data"]["updateChannelStatus"]["status"]=="enabled"'

echo "==> 创建/复用测试 API key"
if [ -s "$STATE_DIR/test-api-key" ]; then
  echo "    已存在 $STATE_DIR/test-api-key，跳过"
else
  RESP=$(gql 'mutation CreateAPIKey($input: CreateAPIKeyInput!) { createAPIKey(input: $input) { id key name status } }' \
    "{\"input\":{\"name\":\"tracer-bullet-test\",\"projectID\":\"$PROJECT_ID\"}}")
  echo "$RESP"
  echo "$RESP" | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["createAPIKey"]["key"])' > "$STATE_DIR/test-api-key"
fi

case "$MODE" in
  codex) MODEL=gpt-5 ;;
  claudecode) MODEL=claude-sonnet-4-5 ;;
  *) MODEL=mock-gpt ;;
esac
echo -n "$MODEL" > "$STATE_DIR/test-model"
echo -n "$MODE" > "$STATE_DIR/mode"

echo
echo "完成：渠道模式=$MODE 模型=$MODEL"
echo "API key 已写入 $STATE_DIR/test-api-key；运行 ./scripts/smoke-test.sh 做端到端验证"
