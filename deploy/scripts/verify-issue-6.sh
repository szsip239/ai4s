#!/usr/bin/env bash
# issue #6 验证脚本（测试即票据）：商密词表 v1（agentgateway webhook → shim → Presidio）
# seam：agentgateway /v1 调用面 + shim/Presidio 容器状态 + 请求记录观测
# 用法：./scripts/verify-issue-6.sh   —— 全绿退出码 0
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

echo "==> [sliceB] 词表热更新（改文件即生效，不重启 shim）"

# 追加新词后立刻验证命中；结束后移除该词还原
python3 - <<'EOF'
import json
p="dlp/confidential-terms.json"
d=json.load(open(p))
d["terms"].append({"value":"热更新验证词-玄武","rule_id":"confidential.codename"})
json.dump(d,open(p,"w"),ensure_ascii=False,indent=2)
EOF
sleep 1
R=$(probe "热更新验证词-玄武 这个词现在应该被拦截")
CODE=${R%%|*}
echo "    [热更新] HTTP ${CODE}"
[ "$CODE" = "451" ]
check "新词「热更新验证词-玄武」免重启生效 → 451" $?
python3 - <<'EOF'
import json
p="dlp/confidential-terms.json"
d=json.load(open(p))
d["terms"]=[t for t in d["terms"] if t["value"]!="热更新验证词-玄武"]
json.dump(d,open(p,"w"),ensure_ascii=False,indent=2)
EOF

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
