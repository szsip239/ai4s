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
注入水位门禁段（issue #95）：主流程最前 subprocess 跑 injection-eval.py（normalize
链路口径），不达标非零即回归失败。
语义层水位门禁段（issue #99）：紧跟注入门禁段 subprocess 跑 semantic-eval.py
（/judge-test 直测 judge，慢调用 ~2 分钟，放前面早失败），退出码非零即回归失败。

公共部分（常量/登录/渠道/send/classify/admin API）在 dlp_testkit.py（issue #42 提取）。
自动准备（幂等）：起 mock-upstream（profile mock）+ 建 dlp-echo 渠道（model=echo-test）。
用法：cd deploy && python3 scripts/dlp-regression.py [--json out.json]
退出码：有"应拦未拦/应脱敏未脱敏/负例误伤"或注入/语义水位门禁不达标即 1；文档化 gap 不 fail。
"""
import json
import os
import subprocess
import sys
import time

import dlp_testkit as tk

DEPLOY_DIR = tk.DEPLOY_DIR
VECTORS_PATH = os.path.join(DEPLOY_DIR, "tests", "dlp-vectors.json")
# 纯 CJK 临时词：走 shim 子串直配路径（不依赖 Presidio NLP 分词，确定性命中）
ADMIN_TMP_TERM = "统一配置回归验证词玄武"


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
            # judge 段缺 #93/#94 键的旧 settings.json：PUT 前补默认（与 shim setting_value
            # 缺省对齐），否则必填校验 400
            return {"threshold": 0.8, "action": "shadow", "sample_rate": 1.0,
                    "max_concurrency": 2, **section}

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


def main():
    out_json = "--json" in sys.argv
    out_path = sys.argv[sys.argv.index("--json") + 1] if out_json else None

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

    fails = [r for r in results if r["fail"]]
    for name, ok, got in edm_fails + admin_results + pg_block_results + judge_warn_results + rules_results:
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
