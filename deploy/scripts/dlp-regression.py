#!/usr/bin/env python3
"""ai4s DLP 对抗回归（issue #20）。

对活网关 :3000 全链路逐样本发请求，断言三类结果：
  reject —— 上游应答 451（L1/L2 阻断）
  mask   —— 200 且 echo 中不含敏感原文（上游只收到掩码）
  pass   —— 200 且 echo 含原文（负例必须 pass；文档化绕过项 note 标注 gap）

回声原理：mock-upstream 把收到的 user 消息原文放进应答（"echo: ..."），
断言"上游实际收到的内容"——脱敏断言的唯一可靠锚点。

自包含段：EDM 段（issue #29）；admin API 段（issue #37）——词表只经 admin API 改，
PUT 临时词→命中 451→PUT 还原→不再 451；凭据缺（env DLP_ADMIN_TOKEN 与
deploy/.local/admin-jwt 均不可得）则 SKIP 不 fail。
PG 阻断专项段（issue #103）：admin 段之后——PUT 开阻断（block_enabled=true,
block_threshold=0.9）→ v3 高分注入样本应 451 → 负例应放行 → PUT 还原（关阻断）→
同高分样本再应放行；shim 未含 #103 阻断区块时（PUT 400 未知字段/未知顶层键）SKIP 不 fail
（集成前预期态；#104 起入库 settings.json 含 rules 顶层段，更旧 shim 的拒绝口径一并覆盖）。
judge warn 专项段（issue #101）：PG 阻断段之后——PUT action=warn → 经网关打一条
semantic-vectors novel 涉密样本（应放行——契约「语义层永不阻断」）→ admin 查询出口
shadow-verdicts 出现 warned=True 新条 → PUT 还原 action=shadow；shim 未含 #101 消费时
（judge 判定超阈值落条但无 warned 键）SKIP 不 fail（对齐 #103 段探测纪律）。
规则层专项段（issue #104）：judge warn 段之后——PUT 开 rules.enabled（shadow）→ v3
注入样本应放行且查询出口 layer=rules 出现 hit 条 → PUT 开 block=true → 同样本应
451 → 负例应放行 → PUT 还原（双关）→ 同样本再应放行；shim 未含 #104 区块时
（PUT 400 未知顶层键 / 查询出口 layer=rules 400）SKIP 不 fail（集成前预期态）。
judge 注入 shadow 专项段（issue #105）：规则层段之后——PUT 开 judge.inject_enabled →
经网关打 v3 注入样本（应放行——注入判定永不阻断）→ 查询出口 layer=judge_inject 出现
hit=True 新条（带 attack_type 脱敏标签）→ PUT 还原（关）→ 同样本再应放行；shim 未含
#105 区块时（PUT 400 未知字段 / 查询出口 layer=judge_inject 400）SKIP 不 fail
（集成前预期态，对齐 #103/#101/#104 段探测纪律）。
OPF 第二检测器专项段（issue #127）：judge 注入段之后——段内起假 OPF server
（探针名定位回 span）→ PUT 开 l2.opf（url 指 host.docker.internal 假 server）→
姓名样本应掩码【PII:姓名】→ PUT 还原（关）→ 同样本放行；shim 未含 #127 区块时
（PUT 400 未知字段）SKIP 不 fail（集成前预期态，对齐各段探测纪律）。
auto 路由专项段（issue #118）：judge 注入段之后——PUT 开 routing 节（enabled=true,
threshold=0.5, tiers simple→deepseek-v4-flash/complex→gpt-5.6-luna, timeout=4,
max_concurrency=2）→ model=auto 简单问题应落 tier=simple 改写条 → 复杂任务应落
tier=complex 且响应 200 model=gpt-5.6-luna → 故障注入（routing.timeout=0.001 强制
分类器超时）应 fail-open 落旗舰不 422 且 router error 条 → 同会话二轮应
reason=session_inherit → PUT 还原快照（GET 原文逐字节回写）；shim 未含 #117 区块时
（PUT 400 未知字段/顶层键、查询出口 layer=router 400）SKIP 不 fail（对齐各段探测纪律）。
本段用例行并入主结果集（layer="router"），总计样本数随之增加。
注入水位门禁段（issue #95）：主流程最前 subprocess 跑 injection-eval.py（normalize
链路口径），不达标非零即回归失败。
语义层水位门禁段（issue #99）：紧跟注入门禁段 subprocess 跑 semantic-eval.py
（/judge-test 直测 judge，慢调用 ~2 分钟，放前面早失败），退出码非零即回归失败。
tool scope 专项段（issue #126）：紧随主向量循环，读 tool-scope-vectors.json 直发完整
messages（tool_calls/tool role/system prompt 形态），纯向量段无 settings 改动；
网关未渲染 scope 时 reject 向量会放行——属 FAIL 非 SKIP（正是本段要抓的回归）。

公共部分（常量/登录/渠道/send/classify/admin API）在 dlp_testkit.py（issue #42 提取）。
自动准备（幂等）：起 mock-upstream（profile mock）+ 建 dlp-echo 渠道（model=echo-test）。
中断保护（issue #20 运维加固）：main 开始回归前把当时 settings 快照落盘
.local/dlp-settings-backup.json，正常结束自动删除；脚本被 kill（如 shim OOM 连坐
SIGKILL，进程内 finally/atexit 均不可达）残留该文件时，下次运行先自动还原上次基线
再开始；也可手动 `python3 scripts/dlp-regression.py --restore` 仅还原后退出。
用法：cd deploy && python3 scripts/dlp-regression.py [--json out.json] [--restore]
退出码：有"应拦未拦/应脱敏未脱敏/负例误伤"或注入/语义水位门禁不达标即 1；文档化 gap 不 fail。
"""
import atexit
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dlp_testkit as tk

DEPLOY_DIR = tk.DEPLOY_DIR
VECTORS_PATH = os.path.join(DEPLOY_DIR, "tests", "dlp-vectors.json")
TOOLSCOPE_PATH = os.path.join(DEPLOY_DIR, "tests", "tool-scope-vectors.json")
# 纯 CJK 临时词：走 shim 子串直配路径（不依赖 Presidio NLP 分词，确定性命中）
ADMIN_TMP_TERM = "统一配置回归验证词玄武"


def run_tool_scope_section(api_key):
    """tool scope 专项段（issue #126，v1.5.0 ContentScope）：secrets reject 规则渲染
    scope=[systemPrompt, messages, toolInput, toolOutput] 后，tool_calls arguments /
    tool 结果回填 / system prompt 内嵌密钥均应 451，干净 tool 调用放行。
    纯向量段（无 settings 改动，无自还原义务）；网关配置未含 scope 时（v1.4.x 或
    未重渲染）前 3 条会放行——属 FAIL 而非 SKIP：scope 漏配正是本段要抓的回归。
    本段无 GAP 语义（fail = not ok，与主循环的 note 豁免口径不同——向量均不带 note）。"""
    print("\n==> tool scope 专项段（issue #126，ContentScope 四目标）")
    vectors = json.load(open(TOOLSCOPE_PATH, encoding="utf-8"))["vectors"]
    rows = []
    for v in vectors:
        status, reply = tk.send_messages(v["messages"], api_key)
        got = tk.classify(status, reply, v.get("sensitive"))
        ok = got == v["expect"]
        print(f"[{'OK ' if ok else 'FAIL'}] {v['name']}: expect={v['expect']} got={got}")
        rows.append({"name": v["name"], "layer": "toolscope", "expect": v["expect"],
                     "got": got, "ok": ok, "fail": not ok, "note": v.get("note", "")})
        time.sleep(0.2)
    return rows


def run_edm_section(api_key):
    """EDM 自包含段（issue #29）：临时合成文档入库→整篇粘贴应 451→负例应放行→清理。"""
    print("\n==> EDM 段（临时语料自包含）")
    doc_path = os.path.join(DEPLOY_DIR, "edm", "corpus", "__regression_tmp__.txt")
    os.makedirs(os.path.dirname(doc_path), exist_ok=True)
    doc = ("内部结算备忘录 ZX-77：codex 渠道结算比例为 0.831，tokenhub 渠道为 0.917，"
           "尾差计入损益调整科目 6650；月度对账报告由计费引擎自动生成并经合规复核。") * 6
    open(doc_path, "w", encoding="utf-8").write(doc)
    subprocess.run(["python3", "scripts/edm-add.py", "edm/corpus/__regression_tmp__.txt", "--name", "__regression_tmp__"],
                   cwd=DEPLOY_DIR, check=True, capture_output=True)
    results = []
    try:
        for attempt in range(2):
            status, _ = tk.send("把这份备忘录发给模型总结：\n" + doc, api_key)
            got = tk.classify(status, _, None)
            if got == "reject":
                break
            time.sleep(1)
        ok = got == "reject"
        results.append(("EDM: 整篇粘贴应 451", ok, got))
        status, _ = tk.send("帮我写一份对账流程优化建议", api_key)
        got = tk.classify(status, _, None)
        ok = got == "pass"
        results.append(("EDM: 负例应放行", ok, got))
        # 删除腿（review #8）：删语料 → 指纹移除 → 同文档放行（删除语义全链路验证；finally 的 --remove 幂等兜底）
        subprocess.run(["python3", "scripts/edm-add.py", "edm/corpus/__regression_tmp__.txt", "--name", "__regression_tmp__", "--remove"],
                       cwd=DEPLOY_DIR, check=True, capture_output=True)
        for attempt in range(2):
            status, _ = tk.send("把这份备忘录发给模型总结：\n" + doc, api_key)
            got = tk.classify(status, _, None)
            if got == "pass":
                break
            time.sleep(1)
        ok = got == "pass"
        results.append(("EDM: 删除语料后同文档放行", ok, got))
    finally:
        subprocess.run(["python3", "scripts/edm-add.py", "edm/corpus/__regression_tmp__.txt", "--name", "__regression_tmp__", "--remove"],
                       cwd=DEPLOY_DIR, check=False, capture_output=True)
        try:
            os.remove(doc_path)
        except OSError:
            pass
        try:
            os.remove(doc_path + ".bak")  # admin 原子写备份副产物（issue #34：薄壳覆盖已存在文件产生）
        except OSError:
            pass
    for name, ok, got in results:
        print(f"[{'OK ' if ok else 'FAIL'}] {name}（got={got}）")
    return [(name, ok, got) for name, ok, got in results]


def run_admin_section(api_key, token):
    """admin API 路径自包含段（issue #37）：词表只经 admin API 改——
    PUT 临时词 → 经网关命中 451 → PUT 还原 → 同词不再 451。
    幂等：原始 terms 先剔除同名残留（上次崩溃遗留）再作还原基准。
    自清理：finally 还原词表 + .bak 快照回滚（段内 PUT 会滚动覆盖既有 .bak，不新增残留）。"""
    print("\n==> admin API 段（词表写入路径自包含）")
    results = []
    bak_path = os.path.join(DEPLOY_DIR, "dlp", "confidential-terms.json.bak")
    bak_before = open(bak_path, "rb").read() if os.path.exists(bak_path) else None
    original_terms = None

    def restore():
        if original_terms is not None:
            tk._admin_api("PUT", "/dlp-admin/wordlist", token, {"terms": original_terms})
        if bak_before is not None:
            open(bak_path, "wb").write(bak_before)
        elif os.path.exists(bak_path):
            os.remove(bak_path)

    try:
        status, doc = tk._admin_api("GET", "/dlp-admin/wordlist", token)
        if status != 200 or not isinstance(doc, dict) or not isinstance(doc.get("terms"), list):
            results.append(("admin: GET 词表", False, f"http{status}"))
        else:
            original_terms = [t for t in doc["terms"]
                              if not (isinstance(t, dict) and t.get("value") == ADMIN_TMP_TERM)]
            tmp_term = {"value": ADMIN_TMP_TERM, "rule_id": "confidential.codename"}
            status, _ = tk._admin_api("PUT", "/dlp-admin/wordlist", token,
                                      {"terms": original_terms + [tmp_term]})
            results.append(("admin: PUT 临时词入词表", status == 200, f"http{status}"))
            if status == 200:
                got = None
                for _attempt in range(2):  # shim 每请求重读词表，正常即时生效；retry 仅吸收极端时序
                    st, reply = tk.send(f"请把 {ADMIN_TMP_TERM} 原样发给上游", api_key)
                    got = tk.classify(st, reply, None)
                    if got == "reject":
                        break
                    time.sleep(1)
                results.append(("admin: 临时词经网关应 451", got == "reject", got))
                status, _ = tk._admin_api("PUT", "/dlp-admin/wordlist", token, {"terms": original_terms})
                results.append(("admin: PUT 还原词表", status == 200, f"http{status}"))
                got = None
                for _attempt in range(2):
                    st, reply = tk.send(f"请把 {ADMIN_TMP_TERM} 原样发给上游", api_key)
                    got = tk.classify(st, reply, None)
                    if got == "pass":
                        break
                    time.sleep(1)
                results.append(("admin: 还原后同词放行", got == "pass", got))
    finally:
        restore()
    for name, ok, got in results:
        print(f"[{'OK ' if ok else 'FAIL'}] {name}（got={got}）")
    return results


# PG 阻断专项段（issue #103）样本：v3 高分档代表（normalize 口径实测 0.998，0.9 档检出集内，
# 见 docs/tests/2026-08-24-injection-v3-eval.md）；负例用普通业务话术（非 v3 归档 22 条之一，真失败即误报发现）
PG_BLOCK_SAMPLE_NAME = "inj: DAN 经典英文"
PG_BLOCK_NEGATIVE = "帮我写一份本周工作周报，重点写项目进度与风险"


def run_pg_block_section(api_key, token):
    """PG 阻断专项段（issue #103，自包含）：admin PUT 开阻断（block_enabled=true,
    block_threshold=0.9，pg 其余键原样）→ v3 高分注入样本经网关应 451 → 负例应放行
    → PUT 还原（原始 GET 内容，阻断回落关）→ 同高分样本再应放行。
    能力探测：shim 未含 #103 阻断区块时 PUT 400「未知字段」→ SKIP 不 fail（集成前预期态）；
    #104 起 settings.json 增 rules 顶层段，更旧的 shim 报「未知顶层键」——探测放宽为「未知」
    一并覆盖（shim 版本落后于入库配置 = 待集成态，同义 SKIP）。
    自清理：finally 还原 settings（pg 段缺阻断键时补默认——旧 settings.json 直 PUT 会被
    必填校验 400）；前置条件：回归跑在默认配置（阻断关）的栈上。
    状态变化注明：阻断开时 PG 推理回请求路径（同步，p95≈136ms 明示代价）；容器内 PG 模型
    （281MB）经判定首次加载后常驻——2.8GB Docker VM 容得下（#95 起回归流量本就触发懒加载
    常驻，本段不新增内存画像）。"""
    print("\n==> PG 阻断专项段（issue #103，settings PUT 自包含）")
    results = []
    vectors = json.load(open(os.path.join(DEPLOY_DIR, "tests", "injection-vectors.json"),
                             encoding="utf-8"))["vectors"]
    sample = next((v for v in vectors if v["name"] == PG_BLOCK_SAMPLE_NAME), None)
    status, doc = tk._admin_api("GET", "/dlp-admin/settings", token)
    if sample is None or status != 200 or not isinstance(doc, dict) or not isinstance(doc.get("pg"), dict):
        results.append(("PG阻断: 前置（高分样本/settings GET）", False,
                        f"http{status}" if sample is not None else "样本缺失"))
    else:
        original = doc  # 还原基准（含 version/_comment 及其余各段原样）

        def restore():
            # pg 段缺阻断键的旧 settings.json：还原 PUT 前补默认（与 shim setting_value 缺省对齐），
            # 否则必填校验 400 还原失败、阻断留在开位
            pg = {"block_enabled": False, "block_threshold": 0.9, **original["pg"]}
            return tk._admin_api("PUT", "/dlp-admin/settings", token, {**original, "pg": pg})

        try:
            trial = {**original, "pg": {**original["pg"], "block_enabled": True, "block_threshold": 0.9}}
            status, body = tk._admin_api("PUT", "/dlp-admin/settings", token, trial)
            if status == 400 and "未知" in str((body or {}).get("error", "")):
                print("[SKIP] PG 阻断段：shim 未含 issue #103 阻断区块（待集成），本段跳过不 fail")
                return []
            results.append(("PG阻断: PUT 开阻断（block_threshold=0.9）", status == 200, f"http{status}"))
            if status == 200:
                got = None
                for _attempt in range(2):  # shim 每请求重读 settings 即时生效；retry 吸收极端时序
                    st, reply = tk.send(sample["content"], api_key)
                    got = tk.classify(st, reply, None)
                    if got == "reject":
                        break
                    time.sleep(1)
                results.append(("PG阻断: 高分注入样本应 451", got == "reject", got))
                st, reply = tk.send(PG_BLOCK_NEGATIVE, api_key)
                got = tk.classify(st, reply, None)
                results.append(("PG阻断: 负例应放行", got == "pass", got))
                status, _ = restore()
                results.append(("PG阻断: PUT 还原（关阻断）", status == 200, f"http{status}"))
                got = None
                for _attempt in range(2):
                    st, reply = tk.send(sample["content"], api_key)
                    got = tk.classify(st, reply, None)
                    if got == "pass":
                        break
                    time.sleep(1)
                results.append(("PG阻断: 还原后同高分样本放行", got == "pass", got))
        finally:
            restore()
    for name, ok, got in results:
        print(f"[{'OK ' if ok else 'FAIL'}] {name}（got={got}）")
    return results


# judge warn 专项段（issue #101）样本：semantic-vectors novel 指代样本——词表外（整链路过
# L1/L2 不拦，契约「语义层永不阻断」下应放行）；内部系统切流+排期语义信号强，
# semantic-baseline 实测三轮稳定高置信检出（非低置信边界组）
JUDGE_WARN_SAMPLE_NAME = "novel: 指代-蓝色大鱼切流"
# 能力探测口径：judge 判定 confidential 且置信度达档（settings judge.threshold 现值 0.8）
# 却零 warned 条 → 旧容器无 #101 消费 → SKIP（对齐 #103 段探测纪律）
JUDGE_WARN_PROBE_CONF = 0.8
# 重发次数上限：judge 共享端点非确定（实测同样本 temperature=0 下 conf 0.98 涉密 ↔ 0.99 clean
# 翻转过一次）——重发吸收偶发翻转（同既有段 retry 纪律），三次全未达档才判失败
JUDGE_WARN_ATTEMPTS = 3


def run_judge_warn_section(api_key, token):
    """judge warn 专项段（issue #101，自包含）：admin PUT action=warn（judge 其余键原样）→
    经网关打一条 semantic-vectors novel 涉密样本（应放行——warn 不拦截）→ 轮询 admin
    查询出口 /dlp-admin/shadow-verdicts?layer=judge 出现 warned=True 新条 → PUT 还原
    （action=shadow）→ 同样本再应放行。
    判定条隔离：judge 在响应后同步判定（实测 p50≈2.8s/p95≈7s），每次发送前取逐次水位线
    （time.time()-1），轮询至该次判定条落盘再评估——段外存量/前段迟落记录不串档。
    能力探测：shim 未含 #101 消费时（旧容器）judge 仍判定落条但无 warned 键——任一次
    hit 且置信度达档却无 warned 条即判旧容器 → SKIP 不 fail（对齐 #103 段探测纪律）。
    #104 起 settings.json 增 rules 顶层段：更旧的 shim 对 PUT 直接 400「未知顶层键」
    → 同样判 SKIP（shim 版本落后于入库配置 = 待集成态）。
    自清理：finally 还原 settings（judge 段缺 #93/#94 键的旧 settings.json 直 PUT 会被必填
    校验 400——PUT 前补默认，与 shim setting_value/normalizeJudge 缺省对齐）。
    前置条件：回归跑在默认配置（action=shadow）且 judge 链路可用（语义水位门禁段已先跑）。"""
    print("\n==> judge warn 专项段（issue #101，settings PUT 自包含）")
    results = []
    vectors = json.load(open(os.path.join(DEPLOY_DIR, "tests", "semantic-vectors.json"),
                             encoding="utf-8"))["vectors"]
    sample = next((v for v in vectors if v["name"] == JUDGE_WARN_SAMPLE_NAME), None)
    status, doc = tk._admin_api("GET", "/dlp-admin/settings", token)
    if sample is None or status != 200 or not isinstance(doc, dict) or not isinstance(doc.get("judge"), dict):
        results.append(("judgeWarn: 前置（novel 样本/settings GET）", False,
                        f"http{status}" if sample is not None else "样本缺失"))
    else:
        original = doc  # 还原基准（含 version/_comment 及其余各段原样）

        def _judge_full(section):
            # judge 段缺 #93/#94/#105 键的旧 settings.json：PUT 前补默认（与 shim setting_value
            # 缺省对齐），否则必填校验 400。#105 注入三键补「关+空 prompt 占位」（校验放行口径：
            # 关态允许空串；注入 prompt 单一源=settings.json，脚本不内置文本）
            return {"threshold": 0.8, "action": "shadow", "sample_rate": 1.0,
                    "max_concurrency": 2, "inject_enabled": False,
                    "inject_prompt_system": "", "inject_prompt_fewshot": "", **section}

        def restore():
            return tk._admin_api("PUT", "/dlp-admin/settings", token,
                                 {**original, "judge": _judge_full(original["judge"])})

        def recent_judge_recs(since_ts):
            st, body = tk._admin_api("GET", "/dlp-admin/shadow-verdicts?layer=judge&n=50", token)
            if st != 200 or not isinstance(body, dict):
                return []
            return [r for r in body.get("records") or [] if (r.get("ts") or 0) >= since_ts]

        try:
            trial = {**original,
                     "judge": {**_judge_full(original["judge"]), "action": "warn"}}
            status, body = tk._admin_api("PUT", "/dlp-admin/settings", token, trial)
            if status == 400 and "未知" in str((body or {}).get("error", "")):
                # #104 起入库 settings.json 含 rules 顶层段，更旧的 shim 直接拒绝整个 PUT
                print("[SKIP] judge warn 段：shim 版本落后于入库 settings（待集成），本段跳过不 fail")
                return []
            results.append(("judgeWarn: PUT action=warn", status == 200, f"http{status}"))
            if status == 200:
                pass_checked = False
                hit_rec = None  # 首条 hit 达档判定条（旧容器能力探测锚点）
                warned_rec = None
                for _attempt in range(JUDGE_WARN_ATTEMPTS):
                    wm = time.time() - 1  # 本次发送水位线：隔离本次判定条（-1s 吸收时钟误差）
                    st, reply = tk.send(sample["content"], api_key)
                    got = tk.classify(st, reply, None)
                    if not pass_checked:
                        results.append(("judgeWarn: novel 涉密样本应放行（warn 不拦截）", got == "pass", got))
                        pass_checked = True
                    rec = None
                    for _poll in range(6):  # 等本次判定条落盘（12s 封顶，覆盖 p95≈7s）
                        time.sleep(2)
                        rec = next((r for r in recent_judge_recs(wm)), None)
                        if rec is not None:
                            break
                    if rec is None or rec.get("error"):
                        continue  # 判定未落盘/judge 异常条：重发吸收，不算探测证据
                    if rec.get("warned"):
                        warned_rec = rec
                        break
                    if rec.get("hit") and (rec.get("confidence") or 0) >= JUDGE_WARN_PROBE_CONF:
                        hit_rec = rec  # hit 达档但无 warned 键——旧容器特征；继续重发排除偶发
                if warned_rec:
                    results.append(("judgeWarn: 查询出口出现 warned=True 条", True,
                                    f"conf={warned_rec.get('confidence')}"))
                elif hit_rec is not None:
                    print("[SKIP] judge warn 段：shim 未含 issue #101 消费（待集成），本段跳过不 fail")
                    return []
                else:
                    results.append(("judgeWarn: 查询出口出现 warned=True 条", False,
                                    f"{JUDGE_WARN_ATTEMPTS} 次发送均未获 hit 达档判定条（judge 水位/可用性问题）"))
                status, _ = restore()
                results.append(("judgeWarn: PUT 还原（action=shadow）", status == 200, f"http{status}"))
                st, reply = tk.send(sample["content"], api_key)
                got = tk.classify(st, reply, None)
                results.append(("judgeWarn: 还原后同样本放行", got == "pass", got))
        finally:
            restore()
    for name, ok, got in results:
        print(f"[{'OK ' if ok else 'FAIL'}] {name}（got={got}）")
    return results


# 规则层专项段（issue #104）样本：v3「inj: 系统提示词提取中文」——extract-zh 组必中
#（#100 评估单点口径，生产即 shim/inject_rules.rule_match）；负例同 PG 阻断段
#（普通业务话术，真失败即误报发现）
RULES_SAMPLE_NAME = "inj: 系统提示词提取中文"


def run_rules_layer_section(api_key, token):
    """规则层专项段（issue #104，自包含）：admin PUT 开 rules.enabled（block 关）→ v3 注入
    样本经网关应放行（shadow 只记不拦）→ 轮询 admin 查询出口
    /dlp-admin/shadow-verdicts?layer=rules 出现 hit=True 条 → PUT 开 block=true →
    同样本应 451 → 负例应放行 → PUT 还原（双关）→ 同样本再应放行。
    判定条同步落盘（规则段在应答前 record），应答到手即可查，轮询几次兜底吸收时序。
    能力探测：shim 未含 #104 规则层时 PUT 400「未知顶层键」、或查询出口 layer=rules 400
    → SKIP 不 fail（集成前预期态，对齐 #103/#101 段探测纪律）。
    自清理：finally 还原 settings（rules 段缺/缺键补默认 {"enabled":False,"block":False}
    ——与 shim setting_value 缺省对齐，否则必填校验 400 还原失败、开关留在开位）。
    前置条件：回归跑在默认配置（规则层双关）的栈上。规则层判定 µs 级（正则无模型），
    不新增内存画像。"""
    print("\n==> 规则层专项段（issue #104，settings PUT 自包含）")
    results = []
    vectors = json.load(open(os.path.join(DEPLOY_DIR, "tests", "injection-vectors.json"),
                             encoding="utf-8"))["vectors"]
    sample = next((v for v in vectors if v["name"] == RULES_SAMPLE_NAME), None)
    status, doc = tk._admin_api("GET", "/dlp-admin/settings", token)
    if sample is None or status != 200 or not isinstance(doc, dict):
        results.append(("规则层: 前置（v3 样本/settings GET）", False,
                        f"http{status}" if sample is not None else "样本缺失"))
    else:
        original = doc  # 还原基准（含 version/_comment 及其余各段原样）

        def _rules_full(section):
            # 旧 settings.json 缺 rules 段/缺键：PUT 前补默认（与 shim setting_value 缺省对齐）
            return {"enabled": False, "block": False,
                    **(section if isinstance(section, dict) else {})}

        def restore():
            return tk._admin_api("PUT", "/dlp-admin/settings", token,
                                 {**original, "rules": _rules_full(original.get("rules"))})

        def recent_rules_recs(since_ts):
            st, body = tk._admin_api("GET", "/dlp-admin/shadow-verdicts?layer=rules&n=20", token)
            if st == 400:
                return None  # 旧容器无 rules 层查询出口（能力探测锚点）
            if st != 200 or not isinstance(body, dict):
                return []
            return [r for r in body.get("records") or [] if (r.get("ts") or 0) >= since_ts]

        try:
            trial = {**original, "rules": {**_rules_full(original.get("rules")), "enabled": True}}
            status, body = tk._admin_api("PUT", "/dlp-admin/settings", token, trial)
            if status == 400 and "未知" in str((body or {}).get("error", "")):
                print("[SKIP] 规则层段：shim 未含 issue #104 规则层区块（待集成），本段跳过不 fail")
                return []
            results.append(("规则层: PUT 开 shadow（rules.enabled=true）", status == 200, f"http{status}"))
            if status == 200:
                wm = time.time() - 1  # 本次发送水位线：隔离本次判定条（-1s 吸收时钟误差）
                st, reply = tk.send(sample["content"], api_key)
                got = tk.classify(st, reply, None)
                results.append(("规则层: 注入样本 shadow 应放行", got == "pass", got))
                hit_rec = None
                legacy = False
                for _poll in range(4):  # 规则段落条同步先于应答，应答到手即可查；轮询兜底
                    recs = recent_rules_recs(wm)
                    if recs is None:
                        legacy = True
                        break
                    hit_rec = next((r for r in recs if r.get("hit") and not r.get("error")), None)
                    if hit_rec is not None:
                        break
                    time.sleep(1)
                if legacy:
                    print("[SKIP] 规则层段：shim 未含 issue #104 查询出口（待集成），本段跳过不 fail")
                    return []
                results.append(("规则层: 查询出口出现 layer=rules hit 条", hit_rec is not None,
                                f"groups={hit_rec.get('groups')}" if hit_rec else "4 次轮询未见 hit 条"))
                status, _ = tk._admin_api(
                    "PUT", "/dlp-admin/settings", token,
                    {**original, "rules": {**_rules_full(original.get("rules")),
                                           "enabled": True, "block": True}})
                results.append(("规则层: PUT 开阻断（rules.block=true）", status == 200, f"http{status}"))
                if status == 200:
                    st, reply = tk.send(sample["content"], api_key)
                    got = tk.classify(st, reply, None)
                    results.append(("规则层: 注入样本应 451", got == "reject", got))
                    st, reply = tk.send(PG_BLOCK_NEGATIVE, api_key)
                    got = tk.classify(st, reply, None)
                    results.append(("规则层: 负例应放行", got == "pass", got))
                status, _ = restore()
                results.append(("规则层: PUT 还原（双关）", status == 200, f"http{status}"))
                got = None
                for _attempt in range(2):  # shim 每请求重读 settings 即时生效；retry 吸收极端时序
                    st, reply = tk.send(sample["content"], api_key)
                    got = tk.classify(st, reply, None)
                    if got == "pass":
                        break
                    time.sleep(1)
                results.append(("规则层: 还原后同样本放行", got == "pass", got))
        finally:
            restore()
    for name, ok, got in results:
        print(f"[{'OK ' if ok else 'FAIL'}] {name}（got={got}）")
    return results


# judge 注入 shadow 专项段（issue #105）样本：与规则层段同一条 v3「inj: 系统提示词提取中文」
# ——#100 实测 judge 注入 prompt 对 extract 类 5/5 全中（稳定高置信检出组）；负例同 PG 阻断段
JUDGE_INJECT_SAMPLE_NAME = RULES_SAMPLE_NAME
# 重发次数上限：judge 共享端点非确定（同 judge warn 段纪律），三次全未获 hit 条才判失败
JUDGE_INJECT_ATTEMPTS = 3


def run_judge_inject_section(api_key, token):
    """judge 注入 shadow 专项段（issue #105，自包含）：admin PUT 开 judge.inject_enabled →
    经网关打 v3 注入样本（应放行——注入判定永不阻断，契约纪律）→ 轮询 admin 查询出口
    /dlp-admin/shadow-verdicts?layer=judge_inject 出现 hit=True 新条（带 attack_type
    脱敏标签）→ PUT 还原（inject_enabled=false）→ 同样本再应放行。
    判定条隔离：judge 在响应后同步判定（实测 p50≈3.8s），每次发送前取逐次水位线
    （time.time()-1），轮询至该次判定条落盘再评估——段外存量/前段迟落记录不串档。
    能力探测：shim 未含 #105 区块时 PUT 400「未知字段」（旧 shim 必填集无 inject 三键）、
    或查询出口 layer=judge_inject 400 → SKIP 不 fail（对齐 #103/#101/#104 段探测纪律）。
    自清理：finally 还原 settings（judge 段缺 #105 键的旧 settings.json 补「关+空 prompt
    占位」——与 admin 校验放行口径对齐；注入 prompt 单一源=settings.json，脚本不内置文本，
    故本段只翻转 inject_enabled，prompt 用入库 settings.json 生效值）。
    前置条件：回归跑在默认配置（inject_enabled=false）且 judge 链路可用（语义水位门禁段
    已先跑）的栈上；入库 settings.json 须含 #105 注入 prompt 默认值（否则开态 verdict=null
    属预期 fail-open，本段按「未获 hit 条」如实失败——提示配置缺失而非代码回归）。"""
    print("\n==> judge 注入 shadow 专项段（issue #105，settings PUT 自包含）")
    results = []
    vectors = json.load(open(os.path.join(DEPLOY_DIR, "tests", "injection-vectors.json"),
                             encoding="utf-8"))["vectors"]
    sample = next((v for v in vectors if v["name"] == JUDGE_INJECT_SAMPLE_NAME), None)
    status, doc = tk._admin_api("GET", "/dlp-admin/settings", token)
    if sample is None or status != 200 or not isinstance(doc, dict) or not isinstance(doc.get("judge"), dict):
        results.append(("judgeInject: 前置（v3 样本/settings GET）", False,
                        f"http{status}" if sample is not None else "样本缺失"))
    else:
        original = doc  # 还原基准（含 version/_comment 及其余各段原样）

        def _judge_full(section):
            # judge 段缺 #93/#94/#105 键的旧 settings.json：PUT 前补默认（与 run_judge_warn_section
            # 同款口径）；#105 注入三键补「关+空 prompt 占位」（开态 prompt 必须非空——用入库生效值，
            # 本段不内置 prompt 文本）
            return {"threshold": 0.8, "action": "shadow", "sample_rate": 1.0,
                    "max_concurrency": 2, "inject_enabled": False,
                    "inject_prompt_system": "", "inject_prompt_fewshot": "", **section}

        def restore():
            return tk._admin_api("PUT", "/dlp-admin/settings", token,
                                 {**original, "judge": _judge_full(original["judge"])})

        def recent_inject_recs(since_ts):
            st, body = tk._admin_api("GET", "/dlp-admin/shadow-verdicts?layer=judge_inject&n=20", token)
            if st == 400:
                return None  # 旧容器无 judge_inject 层查询出口（能力探测锚点）
            if st != 200 or not isinstance(body, dict):
                return []
            return [r for r in body.get("records") or [] if (r.get("ts") or 0) >= since_ts]

        try:
            trial = {**original,
                     "judge": {**_judge_full(original["judge"]), "inject_enabled": True}}
            status, body = tk._admin_api("PUT", "/dlp-admin/settings", token, trial)
            if status == 400 and "未知" in str((body or {}).get("error", "")):
                # 旧 shim 必填集无 inject 三键（待集成态）
                print("[SKIP] judge 注入段：shim 未含 issue #105 区块（待集成），本段跳过不 fail")
                return []
            results.append(("judgeInject: PUT 开 inject_enabled=true", status == 200, f"http{status}"))
            if status == 200:
                pass_checked = False
                hit_rec = None
                legacy = False
                for _attempt in range(JUDGE_INJECT_ATTEMPTS):
                    wm = time.time() - 1  # 本次发送水位线：隔离本次判定条（-1s 吸收时钟误差）
                    st, reply = tk.send(sample["content"], api_key)
                    got = tk.classify(st, reply, None)
                    if not pass_checked:
                        results.append(("judgeInject: 注入样本应放行（shadow 永不阻断）", got == "pass", got))
                        pass_checked = True
                    rec = None
                    for _poll in range(8):  # 等本次判定条落盘（16s 封顶，覆盖 judge p95≈10s）
                        time.sleep(2)
                        recs = recent_inject_recs(wm)
                        if recs is None:
                            legacy = True
                            break
                        rec = next((r for r in recs if not r.get("error")), None)
                        if rec is not None:
                            break
                    if legacy:
                        print("[SKIP] judge 注入段：shim 未含 issue #105 查询出口（待集成），本段跳过不 fail")
                        return []
                    if rec is None:
                        continue  # 判定未落盘/异常条：重发吸收，不算证据
                    if rec.get("hit"):
                        hit_rec = rec
                        break
                    # 未命中条：judge 水位波动，重发吸收（三次全 miss 才判失败）
                if hit_rec:
                    results.append(("judgeInject: 查询出口出现 judge_inject hit 条", True,
                                    f"attack_type={hit_rec.get('attack_type')} conf={hit_rec.get('confidence')}"))
                else:
                    results.append(("judgeInject: 查询出口出现 judge_inject hit 条", False,
                                    f"{JUDGE_INJECT_ATTEMPTS} 次发送均未获 hit 条（judge 水位/可用性问题）"))
                status, _ = restore()
                results.append(("judgeInject: PUT 还原（inject_enabled=false）", status == 200, f"http{status}"))
                st, reply = tk.send(sample["content"], api_key)
                got = tk.classify(st, reply, None)
                results.append(("judgeInject: 还原后同样本放行", got == "pass", got))
        finally:
            restore()
    for name, ok, got in results:
        print(f"[{'OK ' if ok else 'FAIL'}] {name}（got={got}）")
    return results


# ---- OPF 第二检测器专项段（issue #127）----
# 假 sidecar 探针名：段内假 OPF server 在收到文本中定位该名并回 span——
# 验证「settings 开 → shim 调 OPF → span 合并掩码」全链路，无需真模型
OPF_PROBE_NAME = "欧阳回归"
OPF_SAMPLE = f"我是{OPF_PROBE_NAME}，这份方案请帮忙润色"


def run_opf_section(api_key, token):
    """OPF 第二检测器专项段（issue #127，自包含）：段内起假 OPF server（收到文本中定位
    探针名回 private_person span）→ admin PUT 开 l2.opf（url 指 host.docker.internal
    假 server，macOS Docker Desktop 内置可解析）→ 姓名样本经网关应掩码【PII:姓名】
    （echo 不含原名）→ PUT 还原快照（opf 关）→ 同样本放行。
    能力探测：shim 未含 #127 区块时 PUT 400「未知字段」→ SKIP 不 fail（对齐各段纪律）。
    自清理：finally 还原 settings 快照 + 收假 server。"""
    print("\n==> OPF 第二检测器专项段（issue #127，假 sidecar 自包含）")
    results = []

    class _H(BaseHTTPRequestHandler):
        def log_message(self, *args):  # 静默（对齐 shim 契约）
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                text = json.loads(self.rfile.read(length) or b"{}").get("text") or ""
            except ValueError:
                text = ""
            i = text.find(OPF_PROBE_NAME)
            spans = ([{"label": "private_person", "start": i, "end": i + len(OPF_PROBE_NAME),
                       "text": OPF_PROBE_NAME}] if i >= 0 else [])
            body = json.dumps({"spans": spans}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, doc = tk._admin_api("GET", "/dlp-admin/settings", token)
        if status != 200 or not isinstance(doc, dict):
            results.append(("OPF: 前置（settings GET）", False, f"http{status}"))
        else:
            original = doc  # 还原基准（含 version/_comment 及其余各段原样）
            opf_on = {"enabled": True, "timeout_ms": 8000, "max_chars": 4000,
                      "url": f"http://host.docker.internal:{srv.server_address[1]}"}

            def restore():
                return tk._admin_api("PUT", "/dlp-admin/settings", token, original)

            try:
                trial = {**original, "l2": {**(original.get("l2") or {}), "opf": opf_on}}
                status, body = tk._admin_api("PUT", "/dlp-admin/settings", token, trial)
                if status == 400 and "未知" in str((body or {}).get("error", "")):
                    print("[SKIP] OPF 段：shim 未含 issue #127 区块（待集成），本段跳过不 fail")
                    return []
                results.append(("OPF: PUT 开 l2.opf（url 指假 sidecar）", status == 200, f"http{status}"))
                if status == 200:
                    st, reply = tk.send(OPF_SAMPLE, api_key)
                    got = tk.classify(st, reply, OPF_PROBE_NAME)
                    ok = got == "mask" and "【PII:姓名】" in reply
                    results.append(("OPF: 姓名样本应掩码【PII:姓名】", ok,
                                    got if ok else f"{got}（echo 无掩码占位）"))
                    status, _ = restore()
                    results.append(("OPF: PUT 还原（opf 关）", status == 200, f"http{status}"))
                    got = None
                    for _attempt in range(2):  # shim 每请求重读 settings 即时生效；retry 吸收时序
                        st, reply = tk.send(OPF_SAMPLE, api_key)
                        got = tk.classify(st, reply, OPF_PROBE_NAME)
                        if got == "pass":
                            break
                        time.sleep(1)
                    results.append(("OPF: 还原后同样本放行", got == "pass", got))
            finally:
                restore()
    finally:
        srv.shutdown()
    for name, ok, got in results:
        print(f"[{'OK ' if ok else 'FAIL'}] {name}（got={got}）")
    return results


# ---- auto 路由专项段（issue #118）----
# 用例样本：简单题（单步事实问答，#114 口径 p_complex≈0）与复杂题（多步推理+权衡，但
# 限制输出长度——分类看输入难度、生成耗时看输出长度，max_tokens 封顶压住旗舰应答时间）；
# 各用例提示词互不相同（同文会撞首轮消息哈希会话存态，干扰 reason 断言——见
# shim/app.py _router_session_key 的哈希兜底路径）；故障注入题复用简单题语义即可
ROUTER_SIMPLE_SAMPLE = "请用一句话说明地球为什么近似球形。"
ROUTER_COMPLEX_SAMPLE = ("比较 Raft 与 Paxos 在选主活锁与一致性证明上的本质差异，"
                         "并给出工程选型权衡——三行以内概括。")
ROUTER_FAILOPEN_SAMPLE = "用一句话解释什么是光合作用。"
# 两档映射（issue #117 默认档）：simple→deepseek-v4-flash（dev 栈测试 key 的 profile
# 白名单不含该模型，axonhub 拒 422——已知事项，用例 a 只断言改写条不断言应答）；
# complex→gpt-5.6-luna（测试 key 白名单内含，#117 实网验证过 200）
ROUTER_TIERS = {"simple": "deepseek-v4-flash", "complex": "gpt-5.6-luna"}
ROUTER_FLAGSHIP = "gpt-5.6-luna"  # 网关 modelAliases auto→旗舰 静态兜底（#115 定稿）
# 分类走真实 judge 通道（~2s/次）非确定，单用例最多重试次数（三次全不符才判失败，
# 同 judge warn/judgeInject 段纪律）
ROUTER_ATTEMPTS = 3


def send_auto(api_key, content_or_messages, session_id=None):
    """model=auto 经网关发送（路由段专用；tk.send 固定 echo-test 单轮不适用）。
    返回 (status, 响应 model 字段, 内容摘要)；max_tokens=120 封顶旗舰应答耗时。"""
    messages = (content_or_messages if isinstance(content_or_messages, list)
                else [{"role": "user", "content": content_or_messages}])
    body = {"model": "auto", "messages": messages, "max_tokens": 120}
    if session_id:
        body["metadata"] = {"session_id": session_id}
    req = urllib.request.Request(tk.GATEWAY + "/v1/chat/completions",
                                 data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.load(r)
        return r.status, d.get("model"), (d["choices"][0]["message"].get("content") or "")[:60]
    except urllib.error.HTTPError as e:
        return e.code, None, e.read().decode()[:150]
    except Exception as e:
        return 0, None, f"{type(e).__name__}: {e}"


def run_auto_router_section(api_key, token):
    """auto 路由专项段（issue #118，自包含）：admin PUT 开 routing 节（五键齐全，
    其余段原样）→ a) model=auto 简单题 → 查询出口 layer=router 出现 tier=simple
    改写条（resolved_model=deepseek-v4-flash；dev 栈该模型不在测试 key 白名单，
    应答 422 属已知事项，不据此判失败）→ b) 复杂题 → tier=complex 落条且响应 200
    model=gpt-5.6-luna → c) 故障注入（PUT routing.timeout=0.001 强制分类器超时——
    选配置注入而非停容器：回归友好、不中断 DLP 链路、finally 自还原）→ auto 请求
    落旗舰不 422（200 model=gpt-5.6-luna）且 router error 条（reason=fail_open）
    → 复原 timeout=4 → d) 同会话二轮（metadata.session_id 固定+首轮历史）→ 第二轮
    reason=session_inherit（complex 存态直接继承、零分类调用）→ PUT 还原快照
    （GET 原文逐字节回写；原 settings 无 routing 节即恢复关态）。
    判定条落盘同步先于网关应答（/classify handler 内 record），应答到手即可查，
    轮询几次兜底吸收时序；逐次水位线（time.time()-1）隔离本次判定条。
    能力探测：shim 未含 #117 区块时 PUT 400「未知字段/未知顶层键」、或查询出口
    layer=router 400 → SKIP 不 fail（对齐 #103/#101/#104/#105 段探测纪律）。
    自清理：finally 还原 settings 快照（routing 节随之消失=关态）。
    前置条件：回归跑在默认配置（routing 节缺席/关态）且 judge 链路可用（语义水位
    门禁段已先跑）的栈上；分类为真实 judge 调用（~2s/次），本段耗时会涨，属预期。"""
    print("\n==> auto 路由专项段（issue #118，settings PUT 自包含）")
    rows = []  # 主结果集形状 dict（layer="router"），并入 results 计入总样本数

    def row(name, ok, got):
        rows.append({"name": name, "layer": "router", "expect": "ok",
                     "got": got, "ok": ok, "fail": not ok, "note": ""})

    status, doc = tk._admin_api("GET", "/dlp-admin/settings", token)
    if status != 200 or not isinstance(doc, dict):
        row("auto路由: 前置（settings GET）", False, f"http{status}")
    else:
        original = doc  # 还原基准（含 version/_comment 及其余各段原样；无 routing 节=关态）
        routing_on = {"enabled": True, "threshold": 0.5, "tiers": dict(ROUTER_TIERS),
                      "timeout": 4, "max_concurrency": 2}

        def restore():
            # 逐字节原样：GET 到什么就 PUT 回什么（原文件无 routing 节，回写即恢复关态）
            return tk._admin_api("PUT", "/dlp-admin/settings", token, original)

        def recent_router_recs(since_ts):
            st, body = tk._admin_api("GET", "/dlp-admin/shadow-verdicts?layer=router&n=20", token)
            if st == 400:
                return None  # 旧容器无 router 层查询出口（能力探测锚点）
            if st != 200 or not isinstance(body, dict):
                return []
            return [r for r in body.get("records") or [] if (r.get("ts") or 0) >= since_ts]

        def await_rec(wm, match):
            """轮询至水位线后首条满足 match 的 router 条；None=超时未获；False=旧容器。"""
            for _poll in range(4):
                recs = recent_router_recs(wm)
                if recs is None:
                    return False
                rec = next((r for r in recs if match(r)), None)
                if rec is not None:
                    return rec
                time.sleep(1)
            return None

        try:
            status, body = tk._admin_api("PUT", "/dlp-admin/settings", token,
                                         {**original, "routing": routing_on})
            if status == 400 and "未知" in str((body or {}).get("error", "")):
                print("[SKIP] auto 路由段：shim 未含 issue #117 routing 区块（待集成），本段跳过不 fail")
                return []
            row("auto路由: PUT 开 routing（enabled=true 五键）", status == 200, f"http{status}")
            if status == 200:
                # 用例 a：简单题 → tier=simple 改写条（应答 422 是 dev 栈白名单已知事项，不断言）
                rec = None
                for _attempt in range(ROUTER_ATTEMPTS):
                    wm = time.time() - 1
                    send_auto(api_key, ROUTER_SIMPLE_SAMPLE)
                    rec = await_rec(wm, lambda r: not r.get("error"))
                    if rec is False:
                        print("[SKIP] auto 路由段：shim 未含 issue #117 查询出口（待集成），本段跳过不 fail")
                        return []
                    if rec is not None and rec.get("tier") == "simple":
                        break
                ok = (rec is not None and rec is not False and rec.get("tier") == "simple"
                      and rec.get("resolved_model") == ROUTER_TIERS["simple"])
                row("auto路由: 简单问题落 tier=simple 改写条", ok,
                    f"tier={rec and rec.get('tier')} resolved={rec and rec.get('resolved_model')}"
                    if rec else f"{ROUTER_ATTEMPTS} 次发送均未获 simple 条（分类器水位问题）")
                # 用例 b：复杂题 → tier=complex 落条且响应 200 model=旗舰
                rec, st_b, model_b = None, None, None
                for _attempt in range(ROUTER_ATTEMPTS):
                    wm = time.time() - 1
                    st_b, model_b, _ = send_auto(api_key, ROUTER_COMPLEX_SAMPLE)
                    rec = await_rec(wm, lambda r: not r.get("error"))
                    if rec is not None and rec is not False and rec.get("tier") == "complex":
                        break
                ok = (rec is not None and rec is not False and rec.get("tier") == "complex"
                      and rec.get("resolved_model") == ROUTER_TIERS["complex"]
                      and st_b == 200 and model_b == ROUTER_TIERS["complex"])
                row("auto路由: 复杂任务 tier=complex 且响应 200 model=gpt-5.6-luna", ok,
                    f"http{st_b} model={model_b} tier={rec and rec.get('tier')}")
                # 用例 c：故障注入——timeout 压 1ms 强制分类器超时 → fail-open 落旗舰 + error 条
                st_c, model_c, rec = None, None, None
                status, _ = tk._admin_api("PUT", "/dlp-admin/settings", token,
                                          {**original, "routing": {**routing_on, "timeout": 0.001}})
                if status == 200:
                    wm = time.time() - 1
                    st_c, model_c, _ = send_auto(api_key, ROUTER_FAILOPEN_SAMPLE)
                    rec = await_rec(wm, lambda r: r.get("error") is not None)
                    status, _ = tk._admin_api("PUT", "/dlp-admin/settings", token,
                                              {**original, "routing": routing_on})
                    if status != 200:
                        row("auto路由: 故障注入后复原 timeout=4", False, f"http{status}")
                else:
                    row("auto路由: PUT 故障注入（timeout=0.001）", False, f"http{status}")
                ok = (st_c == 200 and model_c == ROUTER_FLAGSHIP
                      and rec is not None and rec is not False
                      and rec.get("reason") == "fail_open")
                row("auto路由: fail-open 分类器超时落旗舰不 422 + router error 条", ok,
                    f"http{st_c} model={model_c} reason={rec and rec.get('reason')}"
                    f" error={rec and rec.get('error')}")
                # 用例 d：同会话二轮——首轮复杂题定档 complex → 次轮带历史继承（零分类调用）
                ok, got_d = False, "未跑"
                for attempt in range(ROUTER_ATTEMPTS):
                    sid = f"dlp-reg-router-{int(time.time())}-{attempt}"
                    wm = time.time() - 1
                    send_auto(api_key, ROUTER_COMPLEX_SAMPLE + "（首轮）", session_id=sid)
                    r1 = await_rec(wm, lambda r: not r.get("error"))
                    if r1 is None or r1 is False or r1.get("tier") != "complex":
                        got_d = f"首轮未定档 complex（tier={r1 and r1.get('tier')}），重试"
                        continue  # 首轮落 simple 会在 hash/sid 存态留 simple，换新 sid 重试
                    wm2 = time.time() - 1
                    send_auto(api_key, [
                        {"role": "user", "content": ROUTER_COMPLEX_SAMPLE + "（首轮）"},
                        {"role": "assistant", "content": "Raft 以强领导者简化一致性……"},
                        {"role": "user", "content": "接着上面，补充分区恢复后的日志对齐细节，两行以内。"},
                    ], session_id=sid)
                    r2 = await_rec(wm2, lambda r: not r.get("error"))
                    ok = (r2 is not None and r2 is not False
                          and r2.get("reason") == "session_inherit" and r2.get("session") is True
                          and r2.get("tier") == "complex")
                    got_d = (f"reason={r2 and r2.get('reason')} session={r2 and r2.get('session')}"
                             f" tier={r2 and r2.get('tier')}")
                    if ok:
                        break
                row("auto路由: 同会话二轮 reason=session_inherit", ok, got_d)
                status, _ = restore()
                row("auto路由: PUT 还原快照（去 routing 节）", status == 200, f"http{status}")
        finally:
            restore()
    for r in rows:
        print(f"[{'OK ' if r['ok'] else 'FAIL'}] {r['name']}（got={r['got']}）")
    return rows


def run_injection_gate():
    """注入水位门禁段（issue #95）：subprocess 跑 injection-eval.py（默认 normalize 链路口径，
    输出透传），非零即门禁失败。
    顺序硬约束：必须在本回归 43 样本流量之前跑——injection-eval 内部已 REPL 直测先行 +
    close 释放后再放链路流量；若 shim 主进程已被回归流量触发懒加载 PG 模型（实测容器驻留
    ~1.66GiB），REPL 再加载一份（~1.1GiB）在默认 Docker VM（~2.8GiB）必 OOM（SIGKILL 137）。"""
    print("==> 注入水位门禁（issue #95，injection-eval 链路口径）", flush=True)  # flush：子进程直写 stdout，不 flush 标题会乱序到子进程输出之后
    r = subprocess.run(["python3", "scripts/injection-eval.py"], cwd=DEPLOY_DIR)
    print(f"<== 注入水位门禁 {'PASS' if r.returncode == 0 else f'FAIL（exit {r.returncode}）'}")
    return r.returncode == 0


def run_semantic_gate():
    """语义层水位门禁段（issue #99）：subprocess 跑 semantic-eval.py（/judge-test 直测 judge，
    输出透传），退出码非零即门禁失败。
    位置：紧跟注入门禁段——judge 慢调用（~2 分钟/轮）放前面早失败；直测 shim HTTP，
    不依赖 shim 冷态，无注入段那种 OOM 顺序硬约束。"""
    print("\n==> 语义层水位门禁（issue #99，semantic-eval judge 直测）", flush=True)  # flush 同注入段
    r = subprocess.run(["python3", "scripts/semantic-eval.py"], cwd=DEPLOY_DIR)
    print(f"<== 语义层水位门禁 {'PASS' if r.returncode == 0 else f'FAIL（exit {r.returncode}）'}")
    return r.returncode == 0


BACKUP_PATH = os.path.join(DEPLOY_DIR, ".local", "dlp-settings-backup.json")


def snapshot_settings(token):
    """回归前把当前 settings 快照落盘（中断保护）。GET 响应即完整 settings 字典
    （含 version/_comment 及各段原样），可直接作为 PUT 载荷。失败返回 None。"""
    st, body = tk._admin_api("GET", "/dlp-admin/settings", token)
    if st != 200 or not isinstance(body, dict):
        return None
    with open(BACKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)
    return body


def restore_settings_from_backup():
    """从快照文件还原 settings 并删除快照。无快照/还原失败返回 False（失败时保留快照，
    下次运行再试——覆盖 shim 也随之中断的场景）。"""
    if not os.path.exists(BACKUP_PATH):
        return False
    token = tk.resolve_admin_token()
    if token is None:
        print("[中断保护] 存在 settings 快照但无 admin 凭据，无法还原（快照保留）")
        return False
    snap = json.load(open(BACKUP_PATH, encoding="utf-8"))
    st, body = tk._admin_api("PUT", "/dlp-admin/settings", token, snap)
    if st == 200:
        os.remove(BACKUP_PATH)
        print("[中断保护] settings 已从快照还原")
        return True
    print(f"[中断保护] settings 还原失败（http{st}: {body}），快照保留 {BACKUP_PATH}")
    return False


def _atexit_restore():
    """退出兜底（正常结束与异常/SIGTERM 路径）：各专项段虽自还原，异常路径可能遗漏；
    幂等 PUT 一次基线无害。SIGKILL（如 OOM 137）下 atexit 不可达——靠快照文件残留 +
    下次运行开头的自动还原兜底。"""
    restore_settings_from_backup()


def main():
    out_json = "--json" in sys.argv
    out_path = sys.argv[sys.argv.index("--json") + 1] if out_json else None

    if "--restore" in sys.argv:
        if not os.path.exists(BACKUP_PATH):
            print("[中断保护] 无快照文件，无需还原")
            sys.exit(0)
        sys.exit(0 if restore_settings_from_backup() else 1)

    # 中断保护：上次被 kill 残留的快照先还原，再拍本次基线（门禁段起即可能改 settings）
    if os.path.exists(BACKUP_PATH):
        print("[中断保护] 检测到上次中断残留的 settings 快照，先还原…")
        restore_settings_from_backup()  # 失败仅警告（shim 未起时本次回归自身也会暴露）
    _baseline_token = tk.resolve_admin_token()
    if _baseline_token is not None and snapshot_settings(_baseline_token) is not None:
        atexit.register(_atexit_restore)

    # 注入水位门禁（issue #95）在最前：趁 shim 冷态跑（顺序原因见 run_injection_gate docstring）
    if not run_injection_gate():
        sys.exit(1)

    # 语义层水位门禁（issue #99）紧跟其后：judge 慢调用早失败；直测 HTTP 无冷态要求
    if not run_semantic_gate():
        sys.exit(1)

    api_key = open(os.path.join(DEPLOY_DIR, ".local", "test-api-key")).read().strip()
    token = tk.prepare()

    vectors = json.load(open(VECTORS_PATH, encoding="utf-8"))["vectors"]
    results = []
    for v in vectors:
        status, reply = tk.send(v["content"], api_key)
        got = tk.classify(status, reply, v.get("sensitive"))
        ok = got == v["expect"]
        # 文档化 gap（expect=pass 且带 note）不算失败；负例/应拦/应脱敏不符才算
        fail = not ok and not (v["expect"] == "pass" and v.get("note") and v["layer"] != "negative")
        results.append({"name": v["name"], "layer": v["layer"], "expect": v["expect"],
                        "got": got, "ok": ok, "fail": fail, "note": v.get("note", "")})
        mark = "OK " if ok else ("GAP" if not fail else "FAIL")
        print(f"[{mark}] {v['name']}: expect={v['expect']} got={got}" + (f"（{v['note']}）" if not ok and v.get("note") else ""))
        time.sleep(0.2)

    # tool scope 专项段（issue #126）：紧随主向量循环（同为纯向量段，无需 admin 凭据），
    # 行并入主结果集（layer="toolscope"），参与分层水位与总计
    results.extend(run_tool_scope_section(api_key))

    # 汇总
    layers = {}
    for r in results:
        L = layers.setdefault(r["layer"], {"total": 0, "ok": 0, "gap": 0, "fail": 0})
        L["total"] += 1
        if r["ok"]:
            L["ok"] += 1
        elif r["fail"]:
            L["fail"] += 1
        else:
            L["gap"] += 1
    print("\n===== 分层水位 =====")
    for layer, s in layers.items():
        should_block = sum(1 for r in results if r["layer"] == layer and r["expect"] in ("reject", "mask"))
        blocked = sum(1 for r in results if r["layer"] == layer and r["expect"] in ("reject", "mask") and r["ok"])
        line = f"{layer}: 应拦/应脱敏 {blocked}/{should_block}"
        if should_block:
            line += f"（检出率 {blocked / should_block:.0%}）"
        line += f" | gap {s['gap']} | fail {s['fail']}"
        print(line)

    edm_fails = run_edm_section(api_key)

    # admin API 段（issue #37）：无凭据打印 SKIP 不 fail；文件 token 过期时回退 main 已登录刷新的 token
    admin_token = tk.resolve_admin_token()
    admin_results = []
    if admin_token is None:
        print("\n==> admin API 段：SKIP（env DLP_ADMIN_TOKEN 与 deploy/.local/admin-jwt 均不可得）")
    else:
        st, _ = tk._admin_api("GET", "/dlp-admin/ping", admin_token)
        if st == 401:
            admin_token = token
        admin_results = run_admin_section(api_key, admin_token)

    # PG 阻断专项段（issue #103）：紧跟 admin 段（同凭据与自还原纪律）；
    # shim 未含 #103 区块时段内自探测 SKIP（不 fail）
    pg_block_results = []
    if admin_token is not None:
        pg_block_results = run_pg_block_section(api_key, admin_token)

    # judge warn 专项段（issue #101）：紧跟 PG 阻断段（同凭据与自还原纪律）；
    # shim 未含 #101 消费时段内自探测 SKIP（不 fail）
    judge_warn_results = []
    if admin_token is not None:
        judge_warn_results = run_judge_warn_section(api_key, admin_token)

    # 规则层专项段（issue #104）：紧跟 judge warn 段（同凭据与自还原纪律）；
    # shim 未含 #104 区块时段内自探测 SKIP（不 fail）
    rules_results = []
    if admin_token is not None:
        rules_results = run_rules_layer_section(api_key, admin_token)

    # judge 注入 shadow 专项段（issue #105）：紧跟规则层段（同凭据与自还原纪律）；
    # shim 未含 #105 区块时段内自探测 SKIP（不 fail）
    judge_inject_results = []
    if admin_token is not None:
        judge_inject_results = run_judge_inject_section(api_key, admin_token)

    # OPF 第二检测器专项段（issue #127）：紧跟 judge 注入段（同凭据与自还原纪律）；
    # shim 未含 #127 区块时段内自探测 SKIP（不 fail）
    opf_results = []
    if admin_token is not None:
        opf_results = run_opf_section(api_key, admin_token)

    # auto 路由专项段（issue #118）：紧跟 OPF 段（同凭据与自还原纪律）；
    # shim 未含 #117 区块时段内自探测 SKIP（不 fail）。用例行并入主结果集
    # （layer="router"），总计样本数随之增加（56 + 本段行数）；分类走真实 judge
    # 通道（~2s/次），回归耗时上涨属预期
    if admin_token is not None:
        results.extend(run_auto_router_section(api_key, admin_token))

    fails = [r for r in results if r["fail"]]
    for name, ok, got in edm_fails + admin_results + pg_block_results + judge_warn_results + rules_results + judge_inject_results + opf_results:
        if not ok:
            fails.append({"name": name, "expect": "reject", "got": got})
    print(f"\n总计 {len(results)} 样本：通过 {sum(1 for r in results if r['ok'])}，文档化 gap {sum(1 for r in results if not r['ok'] and not r['fail'])}，回归失败 {len(fails)}")
    if fails:
        print("失败明细：")
        for r in fails:
            print(f"  - {r['name']}: expect={r['expect']} got={r['got']}")

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"date": time.strftime("%Y-%m-%d"), "results": results}, f, ensure_ascii=False, indent=1)
        print(f"JSON 已写 {out_path}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
