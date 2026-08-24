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

    fails = [r for r in results if r["fail"]]
    for name, ok, got in edm_fails + admin_results:
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
