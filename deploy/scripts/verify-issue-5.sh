#!/usr/bin/env bash
# issue #5 验证脚本（测试即票据）：Secrets reject 双层 + 阻断不触发渠道切换
# seam：agentgateway /v1 调用面 + axonhub 管理 API + 请求记录观测
# 用法：./scripts/verify-issue-5.sh   —— 全绿退出码 0
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

# 正样例打一发，输出 "HTTP状态码|响应体前120字符"
probe() { # $1=key $2=prompt
  python3 -c 'import json,sys;print(json.dumps({"model":"mock-gpt","messages":[{"role":"user","content":sys.argv[1]}]}))' "$2" > .local/i5-body.json
  curl -s -w "\n%{http_code}" "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $1" -H "Content-Type: application/json" \
    --data-binary @.local/i5-body.json | python3 -c '
import sys
raw=sys.stdin.read()
code=raw.rstrip().rsplit("\n",1)[-1]
body=raw.rstrip().rsplit("\n",1)[0][:160].replace("\n"," ")
print(f"{code}|{body}")'
}

KEY=$(cat .local/test-api-key)
MULTI_KEY=$(cat .local/i2-multi-key)

echo "==> [slice1] agentgateway Secrets 规则：正样例全拒、负样例全放"

# 正样例（每类至少一）：期望 451 + 对应 rule_id
positive() { # $1=name $2=prompt $3=expect_rule
  R=$(probe "$KEY" "$2")
  CODE=${R%%|*}; BODY=${R#*|}
  echo "    [$1] HTTP ${CODE} ${BODY:0:100}"
  [ "$CODE" = "451" ] && echo "$BODY" | grep -q "$3"
  check "正样例 $1 → 451 + $3" $?
}

positive "openai-sk"      "帮我把这个 key 用上行吗：sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd" "secrets.openai_sk"
positive "anthropic-sk"   "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefgh 这个能用吗" "secrets.anthropic_sk"
positive "github-token"   "我的 token 是 ghp_0123456789abcdefGHIJKLMNOPQRSTUV，帮我提交代码" "secrets.github_token"
positive "github-pat"     "github_pat_11ABCDEFG0ijklMNOPQR_abcdefghijklmnopqrstuvwxyz0123456789ABCDE" "secrets.github_token"
positive "aws-ak"         "AKIAIOSFODNN7EXAMPLE 这个 AK 还能用吗" "secrets.aws_key"
positive "private-key"    "-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA7V7 fakecontent 帮我看看这个私钥" "secrets.private_key"
positive "aliyun-ak"      "LTAI5tAbCdEfGhIjKlMnOpQr 这个阿里云 AK 暴露了" "secrets.aliyun_ak"

# 负样例：谈论密钥概念但无真实密钥 → 必须 200
negative() { # $1=name $2=prompt
  R=$(probe "$KEY" "$2")
  CODE=${R%%|*}
  echo "    [$1] HTTP ${CODE}"
  [ "$CODE" = "200" ]
  check "负样例 $1 → 200 放行" $?
}

negative "概念讨论"   "什么是 API key？应该怎么安全地存储它？"
negative "掩码文本"   "我的 key 是 sk-**** 已掩码，帮我看看日志"
negative "短 sk 串"   "sk-123 这种也太短了吧"
negative "代码示例"   "用 python 读环境变量 os.environ[\"OPENAI_API_KEY\"] 的写法对吗"

echo "==> [slice2] axonhub 原生 Prompt Protection Rules（纵深）"

gql 'query { promptProtectionRules(first: 100) { edges { node { id name pattern status settings { action } } } } }' '{}' > .local/i5-ppr.json 2>/dev/null || echo '{"errors":"gql failed"}' > .local/i5-ppr.json
python3 - <<'EOF'
import json,sys
d=json.load(open(".local/i5-ppr.json"))
ppr=(d.get("data") or {}).get("promptProtectionRules")
if ppr is None:
    print("    promptProtectionRules 查询不可用"); sys.exit(1)
edges=ppr.get("edges",[])
names={e["node"]["name"]: e["node"] for e in edges}
print(f"    已配置规则 {len(edges)} 条：{list(names)}")
need={"secrets-openai-sk","secrets-aws-key","secrets-private-key"}
ok=all(n in names and names[n]["status"]=="enabled" and (names[n].get("settings") or {}).get("action")=="reject" for n in need)
sys.exit(0 if ok else 1)
EOF
check "axonhub Prompt Protection Rules 已配置核心三类（reject、enabled）" $?

# 纵深生效：经 shim 容器内网直连 axonhub（绕过 agentgateway；issue #60 宿主 :8090 已收）发正样例，应被 axonhub 自层拒绝
R=$(docker compose exec -T shim python3 -c '
import json, sys, urllib.request, urllib.error
body = json.dumps({"model":"mock-gpt","messages":[{"role":"user","content":"AKIAIOSFODNN7EXAMPLE 这个还能用吗"}]}).encode()
req = urllib.request.Request("http://axonhub:8090/v1/chat/completions", data=body,
    headers={"Content-Type":"application/json","Authorization":"Bearer "+sys.argv[1]})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print("200", r.read().decode()[:120])
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode()[:120])
except Exception as e:
    print(0, type(e).__name__)
' "$(cat .local/test-api-key)")
CODE=$(echo "$R" | awk '{print $1}'); BODY=$(echo "$R" | cut -d" " -f2- | head -c 120)
echo "    直连 axonhub（经 shim 内网）正样例 → HTTP ${CODE} ${BODY}"
[ "$CODE" != "200" ]
check "直连 axonhub 正样例被自层拒绝（纵深层真实生效）" $?

echo "==> [slice3] 阻断不触发渠道切换（多渠道 key）"

# 阻断前的最新请求时间基线（last:1 = 最新一条）
gql 'query { requests(last: 1) { edges { node { id createdAt } } } }' '{}' > .local/i5-before.json 2>/dev/null || echo '{"errors":"gql failed"}' > .local/i5-before.json
BEFORE_TS=$(python3 -c 'import json
d=json.load(open(".local/i5-before.json"))
e=(d.get("data") or {}).get("requests",{}).get("edges",[])
print(e[0]["node"]["createdAt"] if e else "")')

# 多渠道 key 发正样例：期望 451 且不得有任何渠道产生新请求记录
R=$(probe "$MULTI_KEY" "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd 请用这个")
CODE=${R%%|*}; BODY=${R#*|}
echo "    多渠道 key 正样例 → HTTP ${CODE}"
[ "$CODE" = "451" ]
check "多渠道 key 正样例 → 451 阻断" $?

sleep 2
gql 'query { requests(last: 3) { edges { node { id status channelID createdAt } } } }' '{}' > .local/i5-after.json 2>/dev/null || echo '{"errors":"gql failed"}' > .local/i5-after.json
python3 - "$BEFORE_TS" <<'EOF'
import json,sys
before=sys.argv[1]
d=json.load(open(".local/i5-after.json"))
edges=(d.get("data") or {}).get("requests",{}).get("edges",[])
new=[e["node"] for e in edges if e["node"]["createdAt"]>before]
print(f"    阻断后新增请求记录 {len(new)} 条（基线 {before}）")
# 被 agentgateway 阻断的请求不应到达 axonhub 产生任何记录
sys.exit(0 if not new else 1)
EOF
check "阻断后零新增请求记录（敏感内容未落到任何渠道）" $?

echo ""
if [ "$FAIL" = "0" ]; then echo "issue #5 验证：全部通过"; else echo "issue #5 验证：存在失败项"; exit 1; fi
