#!/usr/bin/env bash
# ai4s JIT 用户默认项目分配（幂等，issue #14；issue #68 起附带项目级能力下发）。
# 背景：axonhub v1.0.0-beta6 的 OIDC JIT 不支持"默认项目"（internal/server/biz/oidc.go resolveUser
# 只做建号+角色映射，不触碰 UserProject），飞书 SSO 首登用户 projects 为空、前端 "No Project Selected"。
# 本脚本把所有 activated 且不在 Default 项目的非 owner 用户补进 Default 项目（isOwner=false），
# 并按 SCOPES 变量下发项目级能力（issue #68：系统档已收窄为空，能力一律走 user_projects.scopes）。
#
# 能力集说明（issue #68 实测结论，上游 ent privacy 语义）：
#   - read_requests/write_requests：观测页 + playground 写请求，项目内安全（按成员身份过滤）。
#   - read_api_keys/write_api_keys **刻意不发**：上游项目级 read_api_keys 可见项目内全部
#     非 personal key 明文（无属主过滤），write_api_keys 可改任意 key profiles/模板（自助提额
#     绕过飞书审批），二者与"我的 Key 自助"是同一闸门、无法兼得，故员工 key 改为管理员签发。
#   若日后要恢复员工自助建 key，把两 scope 加进 SCOPES 即回退（同时重新暴露上述 P1，慎翻）。
# 用法：SSO 用户首次登录后运行；或加入 cron 定时兜底。重复运行安全（已 members 跳过）。
set -euo pipefail
cd "$(dirname "$0")/.."

SCOPES='["read_requests","write_requests"]'

AXONHUB_BASE="${AXONHUB_BASE:-http://localhost:3000}"
STATE_DIR=".local"

if [ ! -f .env ]; then
  echo "ERROR: deploy/.env 不存在" >&2
  exit 1
fi
set -a; . ./.env; set +a

# 登录（优先复用 bootstrap 存的 JWT，失效则重新登录）
TOKEN="${ADMIN_JWT:-$(cat "$STATE_DIR/admin-jwt" 2>/dev/null || true)}"
if [ -z "$TOKEN" ] || ! curl -fsS -o /dev/null -X POST "$AXONHUB_BASE/admin/graphql" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"query":"query { myProjects { id } }"}' 2>/dev/null; then
  echo "==> 管理 JWT 失效，重新登录"
  LOGIN_JSON=$(python3 -c 'import json,os;print(json.dumps({"email":os.environ["AXONHUB_ADMIN_EMAIL"],"password":os.environ["AXONHUB_ADMIN_PASSWORD"]}))')
  TOKEN=$(curl -fsS -X POST "$AXONHUB_BASE/admin/auth/signin" -H 'Content-Type: application/json' -d "$LOGIN_JSON" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
  echo -n "$TOKEN" > "$STATE_DIR/admin-jwt"
fi

gql() { # $1=query $2=variables(JSON)
  local payload
  payload=$(PAYLOAD_QUERY="$1" PAYLOAD_VARS="$2" python3 -c 'import json,os;print(json.dumps({"query":os.environ["PAYLOAD_QUERY"],"variables":json.loads(os.environ["PAYLOAD_VARS"])}))')
  curl -fsS -X POST "$AXONHUB_BASE/admin/graphql" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "$payload"
}

echo "==> 查询 Default 项目与全部用户"
PROJECT_ID=$(gql 'query { myProjects { id name status } }' '{}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["data"]["myProjects"][0]["id"])')
echo "    Default 项目: $PROJECT_ID"

USERS_JSON=$(gql 'query { users(first: 200) { edges { node { id email isOwner status projects(first: 50) { edges { node { id } } } } } } }' '{}')

# 筛出：activated、非 owner、且不在 Default 项目的用户
PENDING=$(USERS_JSON="$USERS_JSON" PROJECT_ID="$PROJECT_ID" python3 -c '
import json,os
data=json.loads(os.environ["USERS_JSON"])
pid=os.environ["PROJECT_ID"]
out=[]
for e in data["data"]["users"]["edges"]:
    n=e["node"]
    if n["isOwner"] or n["status"]!="activated":
        continue
    member={p["node"]["id"] for p in n["projects"]["edges"]}
    if pid not in member:
        out.append(n["id"])
print("\n".join(out))
')

if [ -z "$PENDING" ]; then
  echo "==> 没有待分配用户，完成"
  exit 0
fi

echo "$PENDING" | while read -r UID_; do
  [ -z "$UID_" ] && continue
  echo "==> 将 $UID_ 加入 Default 项目（项目级能力: ${SCOPES}）"
  RESP=$(gql 'mutation AddUserToProject($input: AddUserToProjectInput!) { addUserToProject(input: $input) { id userID projectID isOwner scopes } }' \
    "{\"input\":{\"projectId\":\"$PROJECT_ID\",\"userId\":\"$UID_\",\"isOwner\":false,\"scopes\":${SCOPES}}}")
  echo "$RESP"
done
echo "==> 完成"
