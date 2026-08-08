#!/usr/bin/env bash
# issue #6 验证脚本（测试即票据）：商密词表 v1（agentgateway webhook → shim → Presidio）
# seam：agentgateway /v1 调用面 + shim/Presidio 容器状态 + 请求记录观测
# 用法：./scripts/verify-issue-6.sh   —— 全绿退出码 0
# sliceB（词表热更新）走 admin API（issue #37）：token 取 env DLP_ADMIN_TOKEN，缺省读 .local/admin-jwt；
#   地址取 env DLP_ADMIN_URL（默认 http://localhost:18080）；无凭据或 ping 预检 401 → 该段 SKIP 不 fail
set -uo pipefail
cd "$(dirname "$0")/.."

GW=http://localhost:3000
FAIL=0

check() { if [ "$2" = "0" ]; then echo "  PASS: $1"; else echo "  FAIL: $1"; FAIL=1; fi; }

probe() { # $1=prompt -> "CODE|BODY"
  python3 -c 'import json,sys;print(json.dumps({"model":"mock-gpt","messages":[{"role":"user","content":sys.argv[1]}]}))' "$1" > .local/i6-body.json
  curl -s -w "\n%{http_code}" "$GW/v1/chat/completions" \
    -H "Authorization: Bearer $(cat .local/test-api-key)" -H "Content-Type: application/json" \
    --data-binary @.local/i6-body.json | python3 -c '
import sys
raw=sys.stdin.read()
code=raw.rstrip().rsplit("\n",1)[-1]
body=raw.rstrip().rsplit("\n",1)[0][:160].replace("\n"," ")
print(f"{code}|{body}")'
}

wl_api() { # $1 = add|restore：经 admin API 改词表（sliceB 用；地址/凭据读 env DLP_ADMIN_URL/DLP_ADMIN_TOKEN）
python3 - "$1" <<'EOF'
import json, os, sys, urllib.request
API=os.environ["DLP_ADMIN_URL"]
tok=os.environ["DLP_ADMIN_TOKEN"].strip()
def call(m,p,d=None):
    data=json.dumps(d,ensure_ascii=False).encode() if d is not None else None
    r=urllib.request.Request(API+p,data=data,method=m,headers={"Authorization":"Bearer "+tok,"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=10) as resp: return json.loads(resp.read() or b"null")
doc=call("GET","/dlp-admin/wordlist")
terms=[t for t in doc["terms"] if t["value"]!="热更新验证词-玄武"]
if sys.argv[1]=="add":
    terms=terms+[{"value":"热更新验证词-玄武","rule_id":"confidential.codename"}]
call("PUT","/dlp-admin/wordlist",{"terms":terms})
EOF
}

echo "==> [sliceA] 词表命中 reject（经 shim → Presidio）"

# 正样例：词表词 → 451 + confidential rule id
R=$(probe "我们把凤凰计划的下个里程碑提前到 Q3 吧")
CODE=${R%%|*}; BODY=${R#*|}
echo "    [词表-项目代号] HTTP ${CODE} ${BODY:0:90}"
[ "$CODE" = "451" ] && echo "$BODY" | grep -q "confidential"
check "词表词「凤凰计划」→ 451 + confidential" $?

R=$(probe "这个接口文档在 internal.ai4s.local 上能查到")
CODE=${R%%|*}; BODY=${R#*|}
echo "    [词表-内部域名] HTTP ${CODE} ${BODY:0:90}"
[ "$CODE" = "451" ] && echo "$BODY" | grep -q "confidential"
check "词表词「internal.ai4s.local」→ 451 + confidential" $?

R=$(probe "ProjectBlueWhale 的架构评审定在周五")
CODE=${R%%|*}; BODY=${R#*|}
echo "    [词表-英文代号/Presidio 路径] HTTP ${CODE} ${BODY:0:90}"
[ "$CODE" = "451" ] && echo "$BODY" | grep -q "confidential"
check "词表词「ProjectBlueWhale」→ 451 + confidential（Presidio 路径）" $?

# 负样例：正常业务话术 → 200
R=$(probe "总结一下这个季度的 OKR 完成情况，凤凰这个词在中文里有什么寓意")
CODE=${R%%|*}
echo "    [负样例] HTTP ${CODE}"
[ "$CODE" = "200" ]
check "负样例（含「凤凰」非词表词）→ 200 放行" $?

echo "==> [sliceB] 词表热更新（admin API 写入即生效，不重启 shim）"

# issue #37 起词表唯一写入路径 = admin API（不再直改文件，契约"统一配置"节）；
# 追加新词后立刻验证命中，结束后经 API 剔除该词还原（幂等：先滤同名残留再追加）。
# 凭据/地址纪律同 dlp-regression.py：无凭据或 ping 预检非 200（token 过期 401/不可达）→ SKIP 不 fail。
export DLP_ADMIN_URL="${DLP_ADMIN_URL:-http://localhost:18080}"
DLP_ADMIN_TOKEN="${DLP_ADMIN_TOKEN:-}"
[ -z "$DLP_ADMIN_TOKEN" ] && [ -f .local/admin-jwt ] && DLP_ADMIN_TOKEN=$(cat .local/admin-jwt)
export DLP_ADMIN_TOKEN

PING=$(python3 - <<'EOF'
import os, urllib.error, urllib.request
tok=os.environ.get("DLP_ADMIN_TOKEN","").strip()
if not tok:
    print("NO_TOKEN"); raise SystemExit
req=urllib.request.Request(os.environ["DLP_ADMIN_URL"]+"/dlp-admin/ping",
    headers={"Authorization":"Bearer "+tok})
try:
    with urllib.request.urlopen(req,timeout=10) as r: print(r.status)
except urllib.error.HTTPError as e: print(e.code)
except Exception: print("UNREACHABLE")
EOF
)

if [ "$PING" != "200" ]; then
  case "$PING" in
    NO_TOKEN) reason="无凭据" ;;
    401) reason="token 无效或过期（ping 401）" ;;
    UNREACHABLE) reason="admin API 不可达" ;;
    *) reason="ping HTTP $PING" ;;
  esac
  echo "    [SKIP] sliceB 跳过：${reason}。提供凭据后重跑：env DLP_ADMIN_TOKEN 或 .local/admin-jwt（地址 env DLP_ADMIN_URL，默认 http://localhost:18080）"
else
  wl_api add
  sleep 1
  R=$(probe "热更新验证词-玄武 这个词现在应该被拦截")
  CODE=${R%%|*}
  echo "    [热更新] HTTP ${CODE}"
  [ "$CODE" = "451" ]
  check "新词「热更新验证词-玄武」免重启生效 → 451" $?
  wl_api restore
fi

echo "==> [sliceC] fail-open 分级（shim 故障放行 + L1 不受影响）"

docker compose stop shim >/dev/null 2>&1
sleep 2
R=$(probe " shim 停机期间，这条正常请求应该被放行（fail-open）")
CODE=${R%%|*}
echo "    [shim 停机-正常请求] HTTP ${CODE}"
[ "$CODE" = "200" ]
check "shim 停机 → 正常请求 200 放行（fail-open）" $?

R=$(probe "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd 拿着用")
CODE=${R%%|*}
echo "    [shim 停机-Secrets 正样例] HTTP ${CODE}"
[ "$CODE" = "451" ]
check "shim 停机 → L1 Secrets 仍 451（本地规则不受影响）" $?

docker compose start shim >/dev/null 2>&1
sleep 3

echo "==> [sliceD] 阻断不触发渠道切换"

BEFORE_TS=$(date -u +%Y-%m-%dT%H:%M:%S.000000Z)
R=$(probe "凤凰计划 的预算表发我一份")
CODE=${R%%|*}
echo "    [多渠道-词表] HTTP ${CODE}"
[ "$CODE" = "451" ]
check "词表正样例 → 451 阻断" $?

sleep 2
JWT=$(cat .local/admin-jwt)
python3 - "$BEFORE_TS" <<'EOF'
import json,sys,urllib.request
req=urllib.request.Request("http://localhost:8090/admin/graphql",
    data=json.dumps({"query":"{ requests(last: 3) { edges { node { id status createdAt } } } }","variables":{}}).encode(),
    headers={"Authorization":"Bearer "+open(".local/admin-jwt").read().strip(),"Content-Type":"application/json"})
d=json.load(urllib.request.urlopen(req))
edges=(d.get("data") or {}).get("requests",{}).get("edges",[])
new=[e["node"] for e in edges if e["node"]["createdAt"]>sys.argv[1]]
print(f"    阻断后新增请求记录 {len(new)} 条")
sys.exit(0 if not new else 1)
EOF
check "阻断后零新增请求记录（词表内容未落到任何渠道）" $?

echo ""
if [ "$FAIL" = "0" ]; then echo "issue #6 验证：全部通过"; else echo "issue #6 验证：存在失败项"; exit 1; fi
