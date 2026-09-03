#!/usr/bin/env python3
"""ai4s DLP shim：agentgateway promptGuard webhook ↔ Presidio。

契约：docs/contracts/dlp-webhook-shim.md（L2 语义/词表层，fail-open 分级）。
线路格式对齐 agentgateway v1.4.1 webhook.rs：
  POST /request  {"body": {"messages": [...]}}
  放行 ← {"reason": "..."}（PassAction）
  阻断 ← {"body": "<err json>", "status_code": 451, "reason": "..."}（RejectAction）

检测职责划分：
  - 非 CJK 词：Presidio /analyze（ad-hoc deny-list recognizer 随请求注入，词表热更新）
  - CJK 词：shim 直配兜底（Presidio deny-list 依赖 NLP 分词，中文不可靠）
  - 商密语义层（issue #21/#93）：LLM judge 响应定稿后 shadow 判定（只记录不阻断），输入为
    L1/L2 掩码后文本；issue #107 起送判输入先经前置单趟 base64 解码（judge_pre_decode，
    对齐 PG normalize_for_scoring 的 base64 段语义，闭合 #99 实测的 base64 包裹漏报盲区）；
    issue #105 起 judge 兼注入判定第二职责（#100 路线③生产落点）：专用
    注入 prompt（judge.inject_prompt_*，单一源=settings.json）+ inject_enabled 开关
    （默认 false 先进场 shadow），与商密判定同一次采样/并发预算门槛内第二次调用，结果落
    shadow_log 独立层 "judge_inject"——注入判定永不阻断、永不落 warned 条（#101 warn 消费
    是商密专属），观测价值只在 shadow 水位统计（检出/误报/延迟对账 #100 基线）
  - 注入 PG：PromptGuard 2 进程内打分，默认 shadow 仅记录不阻断（fail-open）；issue #103
    高分档阻断试点——pg.block_enabled 开 → /request 应答前同步判定（推理延迟回请求路径
    是试点明示代价），score ≥ pg.block_threshold（默认 0.9）返回 451，低于阈值维持 shadow
  - 注入规则层（issue #104，#100 路线② 生产落点）：inject_rules 语义模式组命中即记
    （归一化扩清除表 + 迭代 base64 解码探针 + 16 个模式组，µs 级全文扫描无 4000 截断），
    默认 rules.enabled=false 零开销零落条；开启后 shadow 落条（groups 脱敏字段），
    rules.block 开 → 应答前同步 451（复用 #103 应答形状，code=rules.injection）。
    与 PG 分工：规则层管确定性模式命中（布尔无分数），PG 管模型打分，两段各自独立开关
  - auto 智能路由（issue #117）：/classify extAuthz 端点真实分类器（#115 spike 桩退役）——
    judge 通道 LLM pcomplex 分类 + routing.tiers 两档映射改写 model + 会话档位稳定
    （首轮定档/继承/强置信升档/tool-loop 锁/thinking 锁），fail-open 落旗舰；
    决策日志 layer="router" 落 shadow_log（详见下方「auto 智能路由」段头注）
  - Key 级绕行（issue #129）：webhook headers CEL 注入的 Authorization 取 Bearer →
    SHA-256 比对绕行名单（bypass_keys，不落明文）；scope=all → shim 侧全层跳过直放
    （网关 L1 只能经 /bv1 专用入口绕，该入口挂 /bv1-authz extAuthz fail-closed 门）；
    scope=layers → 生成所选层 enabled=False 的 settings 覆盖副本继续链路（下游门控
    统一经 setting_value，全覆盖；请求/响应两侧只对本侧生效层落「按层跳过」审计条，
    不记与本侧无关的层）。审计落 shadow_log layer=bypass（不落原文不记 token）
  - 内容阻断观测（issue #130）：/request 词表/归一化 secrets/EDM 451 分支落
    shadow_log layer=block（blocked=True + rule_ids 规则族标识 + model）+
    日志行；alert_poller 巡检项 5 复用阻断游标通道发飞书卡；issue #134 起增强：
    响应侧 451 同槽落条，block 条带 side/key_hash（SHA-256 指纹）/excerpts
    （词表原样、secrets 掩码），读侧 shadow-verdicts 按 key_hash 回填 key 名/用户邮箱
本模块（检测路径）依赖仅标准库；镜像 python:3.12-slim（issue #48 起含 doc_extract 文档解析依赖，
检测路径不 import 第三方库，纪律不变）。
"""
import base64
import collections
import hashlib
import json
import os
import queue
import random
import re
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import admin_api  # DLP 统一配置 admin 平面（issue #31）：/dlp-admin/*，与检测路径隔离
import self_api   # 员工自助平面（issue #74）：/self/*（本人 key 列表，无明文）
import edm_lib    # EDM 指纹算法共享库（issue #34）：入库/检测同法（契约铁律）
import feishu_lib  # 飞书签名共享实现（issue #70）：alert_poller 同一份
import alert_poller  # 告警巡检+提额审批（issue #56 并入）：import 不起线程，仅 __main__ start_daemon
import shadow_log  # shadow 判定观测闭环（issue #92）：stdlib-only；judge/PG 判定持久化供巡检/查询
import bypass_keys  # Key 级 DLP 绕行（issue #129）：stdlib-only；/request //response //bv1-authz 消费
import inject_rules  # 注入规则层匹配器（issue #104）：纯 stdlib 正则，import 免费（无第三方依赖）

PRESIDIO_URL = os.environ.get("PRESIDIO_URL", "http://presidio:3000")
# issue #127：privacy-filter（OPF）sidecar 地址（compose profile 默认不启动；仅存 URL，
# 开关在 settings l2.opf / env OPF_ENABLED）
OPF_URL = os.environ.get("OPF_URL", "http://opf:8081")
WORDLIST_PATH = os.environ.get("WORDLIST_PATH", "/dlp/confidential-terms.json")
PII_RECOGNIZERS_PATH = os.environ.get("PII_RECOGNIZERS_PATH", "/recognizers/pii-zh.json")
SETTINGS_PATH = os.environ.get("SETTINGS_PATH", "/dlp/settings.json")
MAX_BODY = 256 * 1024  # 契约：请求体超限截断送检
# /graphql-authz 检查体上限（2026-09-03）：与网关 extAuthz includeRequestBody
# maxRequestBytes 对齐（1 MiB）；控制台真实 GraphQL 查询为 KB 级，超限即拒
MAX_GRAPHQL_AUTHZ_BODY = 1024 * 1024

# auto 智能路由（issue #117）：#115 spike 的 /classify 桩（auto→echo-test 写死、其他原值
# 回显）已退役，真实分档分类见下方「auto 智能路由」段。响应头白名单纪律保留：进
# x-resolved-model 响应头的值（routing.tiers 映射目标，经 settings 可变）必须过白名单
#（防响应拆分/头注入）。
_CLASSIFY_MODEL_SAFE = re.compile(r"[A-Za-z0-9._:-]{1,128}")


# ---- 统一配置（issue #35）：settings.json > env > 内置默认 ----
# JUDGE/EDM/PG 开关阈值收敛到 settings.json（每请求重读，热生效，admin 平面 PUT /dlp-admin/settings
# 维护）；env 层保留作覆盖/回退。judge prompt 单一源=settings.json（review #2：无 env/代码默认，
# 缺失即 judge 降级纯词表）。凭据（JUDGE_API_KEY/FEISHU_*）永远只走 env。
def load_settings() -> dict:
    """每请求重读 settings.json（热更新）。缺失 → {}（可选覆盖层，合法回退态，不 warn）；
    损坏/不可读/非对象 → warn + {}（回退 env/内置默认，fail-open 语义）。"""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"[settings] 读取失败回退 env/默认: {SETTINGS_PATH}: {type(e).__name__}", flush=True)
        return {}
    if not isinstance(data, dict):
        print(f"[settings] 读取失败回退 env/默认: {SETTINGS_PATH}: 顶层非对象", flush=True)
        return {}
    return data


# 逐键类型护栏（review #1）：手工改坏 JSON 单个值类型时，坏键回退 env/默认 + warn，好键仍生效——
# 否则检测路径在响应已发后抛 TypeError（如 score >= "0.7"）。与 admin 写侧 _validate_settings 同款映射。
_SETTINGS_SCHEMA = {
    "judge": {"enabled": "bool", "model": "str", "base_url": "str", "timeout": "number",
              "prompt_system": "str", "prompt_fewshot": "str",
              "threshold": "number", "action": "str",  # 阈值/动作分级（issue #94 schema；#101 消费落地 /request 链路）
              "sample_rate": "number", "max_concurrency": "int",  # 采样率/并发预算（issue #93）
              # 注入判定第二职责（issue #105）：开关三级取值；prompt 单一源=settings.json（同商密 prompt 纪律）
              "inject_enabled": "bool", "inject_prompt_system": "str", "inject_prompt_fewshot": "str"},
    "edm": {"enabled": "bool", "min_hits": "int"},
    "pg": {"enabled": "bool", "threshold": "number", "normalize": "bool",
           "block_enabled": "bool", "block_threshold": "number"},  # 阻断试点（issue #103）
    "rules": {"enabled": "bool", "block": "bool"},  # 注入规则层（issue #104）：布尔命中无分数，阻断开关单键
    # auto 智能路由（issue #117）：routing 节缺席=disabled 零行为（可选节，admin 校验侧同）；
    # tiers 为对象需 dict 护栏（两档映射内容在 route_resolve 消费侧过白名单细验）
    # issue #119 可选增补键（缺席=内置默认保现网行为）：prompt=分类系统提示/
    # escalate_conf=升档门槛/session_ttl=会话 TTL/tool_loop_lock/thinking_lock=锁开关
    "routing": {"enabled": "bool", "threshold": "number", "tiers": "dict",
                "timeout": "number", "max_concurrency": "int",
                "prompt": "str", "escalate_conf": "number", "session_ttl": "number",
                "tool_loop_lock": "bool", "thinking_lock": "bool"},
    # 分层总开关（issue #40）：默认 True 保现网行为；关掉即整层跳过（l1=格式规则全族，
    # l2=词表/Presidio PII，response=响应侧整分支）
    "l1": {"enabled": "bool"},
    "l2": {"enabled": "bool"},
    "response": {"enabled": "bool"},
}


def _json_type_ok(v, kind: str) -> bool:
    """settings JSON 值类型检查（bool 先于数值排除——Python bool 是 int 子类）。"""
    if kind == "bool":
        return isinstance(v, bool)
    if kind == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if kind == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if kind == "str":
        return isinstance(v, str)
    if kind == "dict":
        return isinstance(v, dict)  # issue #117：routing.tiers 两档映射
    return True


def setting_value(settings: dict, section: str, key: str, env_name, default):
    """三级取值（issue #35）：settings.json[section][key] > env（非空） > 内置默认。
    JSON 值先过 _SETTINGS_SCHEMA 类型护栏：不符 → 该键回退 env/默认 + warn（不含值本身）。
    env 按 default 类型转换：bool 对齐 "1" 语义；int/float/str 转换失败回退默认
    （比旧模块级 int()/float() 非法 env 启动即崩更宽容）。"""
    sec = settings.get(section)
    if isinstance(sec, dict) and key in sec:
        v = sec[key]
        kind = _SETTINGS_SCHEMA.get(section, {}).get(key)
        if kind is None or _json_type_ok(v, kind):
            return v
        print(f"[settings] {section}.{key} 类型不符（期望 {kind}），回退 env/默认", flush=True)
    if env_name:
        v = os.environ.get(env_name, "")
        if v != "":
            if isinstance(default, bool):
                return v == "1"
            try:
                return type(default)(v)
            except (TypeError, ValueError):
                return default
    return default


# 分层总开关可观测（issue #40 review）：l1/l2/response 开关状态变化时打一行 [settings] warn——
# 模块级记忆上次状态，不每请求刷日志；启动后首次观测到关闭也打（启动即关不留盲区）。
_LAYER_SWITCH_STATE = {}
_LAYER_SWITCH_TEXT = {
    "l1": ("格式规则层已撤防（密钥拦截敞口）", "格式规则层恢复生效"),
    "l2": ("词表/PII 检测层已跳过", "词表/PII 检测层恢复生效"),
    "response": ("响应侧输出检查已关闭", "响应侧输出检查恢复生效"),
}


def _layer_switch_observe(settings: dict) -> None:
    """每请求调用（基于已加载的 settings，成本可忽略）；并发下重复打一行可接受，不加锁（KISS）。"""
    for section, env_name in (("l1", "L1_ENABLED"), ("l2", "L2_ENABLED"), ("response", "RESPONSE_ENABLED")):
        cur = setting_value(settings, section, "enabled", env_name, True)
        prev = _LAYER_SWITCH_STATE.get(section)
        if prev == cur:
            continue
        _LAYER_SWITCH_STATE[section] = cur
        off_text, on_text = _LAYER_SWITCH_TEXT[section]
        if not cur:
            print(f"[settings] {section}.enabled=false，{off_text}", flush=True)
        elif prev is not None:  # 首次观测到开启是正常启动态，不打
            print(f"[settings] {section}.enabled=true，{on_text}", flush=True)

# 飞书告警适配（issue #17）：axonhub webhook → 群机器人（签名校验 + 有限重试，补 axonhub fire-and-forget 无重试）
FEISHU_WEBHOOK = os.environ.get("FEISHU_ALERT_WEBHOOK", "")
FEISHU_SECRET = os.environ.get("FEISHU_ALERT_SECRET", "")
ALERT_RETRIES = 2

# 商密语义层 shadow（issue #21）：LLM judge 只记录不阻断。
# issue #93 起确定走外部 API 路线（无内网机型，用户拍板）：judge 输入先过 L1/L2 掩码管线
# 脱敏再外发（/request 复用已算好的 masked_msgs，/judge-test 包单条 messages 走同一管线），
# 并有采样率/并发预算两键（settings judge.sample_rate / judge.max_concurrency）做流量护栏。
# 开关/模型/超时/prompt 走统一 settings（issue #35）；凭据永远只走 env。
JUDGE_API_KEY = os.environ.get("JUDGE_API_KEY", "")

# judge 并发预算（issue #93）：模块级锁+计数器，不用 Semaphore——上限每请求现读 settings
# 热生效，Semaphore 尺寸创建后僵化。占满即跳过判定（不落 shadow_log 条：skip 非层异常，
# 不能污染 #92 的 error_rate）。
_JUDGE_INFLIGHT = 0
_JUDGE_LOCK = threading.Lock()


def judge_budget_try_enter(limit: int) -> bool:
    """占一个 judge 并发名额；已达 limit 返回 False（不占位）。limit 由调用方每请求现读 settings。"""
    global _JUDGE_INFLIGHT
    with _JUDGE_LOCK:
        if _JUDGE_INFLIGHT >= limit:
            return False
        _JUDGE_INFLIGHT += 1
        return True


def judge_budget_exit() -> None:
    """释放一个名额（与 try_enter 成功配对，finally 中调用）。"""
    global _JUDGE_INFLIGHT
    with _JUDGE_LOCK:
        _JUDGE_INFLIGHT = max(0, _JUDGE_INFLIGHT - 1)


# judge action 档位可观测（issue #101）：reject 档在 schema 存在（#94）但契约不支持消费
# （语义层永不阻断）——/request 按 shadow 同等处理并打一行提示。模块级记忆上次档位，
# 状态变化才打（同 _layer_switch_observe 纪律），不每请求刷日志；并发下重复打一行可接受。
_JUDGE_ACTION_STATE = {}


def _judge_action_observe(action) -> None:
    """每请求调用（基于已加载的 settings，成本可忽略）；只关心 reject 档提示，其余档位正常态不打。"""
    prev = _JUDGE_ACTION_STATE.get("judge")
    if prev == action:
        return
    _JUDGE_ACTION_STATE["judge"] = action
    if action == "reject":
        print("[settings] judge.action=reject 契约不支持（语义层永不阻断），按 shadow 仅记录处理", flush=True)

# EDM 文档指纹（issue #29，L3 层 PoC）：归一化 shingle + SHA-256，命中≥阈值即 451。
# 语料/指纹库 gitignored；指纹是单向哈希，不存原文。
# 算法收编 shim/edm_lib.py（issue #34）：归一化/窗口/行级阈值与入库侧同法。
# 开关/命中阈值走统一 settings（issue #35）；指纹库路径仍走 env。
EDM_FP_PATH = os.environ.get("EDM_FP_PATH", "/edm/fingerprints.json")


def load_edm_fps():
    """指纹库（每请求重读，热更新）。读不到 → 两个空集（fail-open 语义）。
    返回 (shingle哈希集, 行级哈希集)。"""
    shingles_set, lines_set = set(), set()
    try:
        d = json.load(open(EDM_FP_PATH, encoding="utf-8"))
        for v in (d.get("docs") or {}).values():
            if isinstance(v, list):
                shingles_set.update(v)
            else:
                shingles_set.update(v.get("shingles") or [])
                lines_set.update(v.get("lines") or [])
    except Exception:
        pass
    return shingles_set, lines_set

def edm_hit_count(text: str, fps) -> int:
    """双通道命中数（issue #29）：char-shingle（整段/连续片段）+ 行级（抗乱序）。任一通道达阈即命中。
    算法走 edm_lib（issue #34）：归一化/哈希与入库侧同法。"""
    shingle_fps, line_fps = fps
    if not text:
        return 0
    hits = 0
    if shingle_fps:
        sh = {edm_lib.fp_of(s) for s in edm_lib.shingles(text)}
        hits = max(hits, len(sh & shingle_fps))
    if line_fps:
        hits = max(hits, len(edm_lib.line_hashes(text) & line_fps))
    return hits

# judge prompt 单一源 = settings.json（issue #35 review #2，AC：代码内不留默认 prompt）——
# JSON 缺失或 prompt 键缺失/类型不符时 judge 不可用，judge_text 返回 None 降级纯词表（既有 fail-open）。
# 现网生效 prompt 见 deploy/dlp/settings.json；{{ }}/{terms} 为 .format 转义/占位，改 prompt 时须保留。
# issue #105：注入判定第二职责的 prompt（judge.inject_prompt_system/inject_prompt_fewshot）同纪律
# 单一源=settings.json——但**原文直用不过 .format**（无 {terms} 占位需求；#100 平移的默认注入
# prompt 含 JSON 字面花括号，过 .format 必炸）。商密/注入两职责共享 _judge_chat 调用底座
# （同模型/同地址/同超时/同凭据/同参数形态），差异只在 prompt 与 verdict 形状。


# judge 前置单趟 base64 解码（issue #107）：#99 基线实测 base64 包裹载荷 judge 高置信漏报
# （sk-ant base64 样本 conf 0.97–0.99 判 clean，与 #96 注入 nested_encoding 盲区同构）——
# judge 不解 base64 是结构性盲区。此处对齐 pg_engine.normalize_for_scoring 的 base64 解码
# 语义（judge 侧自建同语义纯函数，不动 PG 侧；无零宽/NFKC——judge 输入已是 L1 掩码后文本）。
# 打分输入级处理、无条件生效（对齐 PG 前置归一化先例，不加 settings 开关）；
# 只改送判文本，掩码管线与转发原文不受影响；解码内容不落日志（secret 纪律）。
_B64_TOKEN = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def judge_pre_decode(text: str) -> str:
    """judge 送判前置单趟 base64 解码（纯函数，issue #107，可单测）。
    base64 形 token 可解码为可打印文本（比例 >0.9）时内联替换；单趟不迭代——
    解码结果不再二次扫描（#104 规则层的迭代探针是另一层，语义不混）。
    任何解码异常 fail-open：token 原样保留，绝不向 judge 主流程抛。"""
    for m in _B64_TOKEN.findall(text):
        try:
            s = base64.b64decode(m, validate=True).decode("utf-8")
        except Exception:
            continue
        if s and sum(ch.isprintable() or ch.isspace() for ch in s) / len(s) > 0.9:
            text = text.replace(m, s)
    return text


def _judge_chat(model, base_url, timeout, system_content, fewshot, text):
    """judge HTTP 调用底座（issue #105 从 judge_text 抽出，商密/注入两职责共享）：
    system + fewshot user + text[:4000]，max_tokens 1500、temperature 0（#61：推理模型
    300 会被 reasoning 烧尽）；返回解析后的 verdict JSON dict，HTTP/解析异常返回 None
    （fail-open 语义与 judge_text 一致）。凭据 JUDGE_API_KEY 永远只走 env。"""
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": fewshot},
            {"role": "user", "content": text[:4000]},
        ],
        "max_tokens": 1500,  # issue #61：deepseek-v4-flash 是推理模型，300 会被 reasoning 烧尽（finish_reason=length、content 空 → ERR）；1500 实测够用
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        base_url + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {JUDGE_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        content = (d["choices"][0]["message"].get("content") or "").strip()
        content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(content)
    except Exception:
        return None


def judge_text(text: str):
    """语义涉密判定。返回 {"confidential": bool, "entities": [...], "confidence": float}；异常/未启用返回 None（fail-open）。
    开关/模型/地址/超时三级取值（issue #35）：settings.json judge.* > env > 内置默认；
    prompt 只认 settings.json（无 env/代码默认）；凭据 JUDGE_API_KEY 永远只走 env。"""
    if not text:
        return None
    s = load_settings()
    enabled = setting_value(s, "judge", "enabled", "JUDGE_ENABLED", False)
    if not (enabled and JUDGE_API_KEY):
        return None
    model = setting_value(s, "judge", "model", "JUDGE_MODEL", "deepseek-v4-flash")
    base_url = setting_value(s, "judge", "base_url", "JUDGE_BASE_URL", "http://axonhub:8090/v1")
    timeout = setting_value(s, "judge", "timeout", "JUDGE_TIMEOUT", 8)
    prompt_system = setting_value(s, "judge", "prompt_system", None, None)
    prompt_fewshot = setting_value(s, "judge", "prompt_fewshot", None, None)
    if not prompt_system or not prompt_fewshot:
        # prompt 无源（settings.json 缺失/键缺失/类型不符）→ judge 不可用，降级纯词表（fail-open 语义沿用契约分级）
        print("[settings] judge prompt 缺失（settings.json judge.prompt_system/prompt_fewshot），judge 降级纯词表", flush=True)
        return None
    terms = "、".join(t["value"] for t in load_terms())
    try:
        system_content = prompt_system.format(terms=terms)
    except Exception:
        return None  # settings 里 prompt 占位损坏 → fail-open（与 judge 其余异常同语义）
    v = _judge_chat(model, base_url, timeout, system_content, prompt_fewshot, text)
    if v is None:
        return None
    try:
        return {"confidential": bool(v.get("confidential")),
                "entities": [str(e) for e in v.get("entities") or []],
                "confidence": float(v.get("confidence") or 0)}
    except Exception:
        return None  # verdict 形状损坏（confidence 非数等）→ fail-open（同其余异常语义）


def judge_inject_text(text: str):
    """注入判定（judge 第二职责，issue #105，#100 路线③ shadow 观测落点）。
    返回 {"injection": bool, "confidence": float, "attack_type": str}；异常/未启用返回 None（fail-open）。
    门控：judge.enabled（服务级总开关，商密判定同键）且 judge.inject_enabled（三级取值，
    默认 false 进场 shadow）——inject_enabled 只是第二职责开关，不能绕过服务级开关单独外发。
    prompt 单一源=settings.json judge.inject_prompt_system/inject_prompt_fewshot（同 #35
    review #2 纪律，无 env/代码默认），**原文直用不过 .format**（无 {terms} 占位需求；
    默认注入 prompt 含 JSON 字面花括号）。模型/地址/超时/凭据与商密判定共享（judge.* 键
    + JUDGE_API_KEY）——#100 实测口径即「与生产 judge 同模型 gpt-5.6-luna」。"""
    if not text:
        return None
    s = load_settings()
    enabled = setting_value(s, "judge", "enabled", "JUDGE_ENABLED", False)
    inject_on = setting_value(s, "judge", "inject_enabled", "JUDGE_INJECT_ENABLED", False)
    if not (enabled and inject_on and JUDGE_API_KEY):
        return None
    model = setting_value(s, "judge", "model", "JUDGE_MODEL", "deepseek-v4-flash")
    base_url = setting_value(s, "judge", "base_url", "JUDGE_BASE_URL", "http://axonhub:8090/v1")
    timeout = setting_value(s, "judge", "timeout", "JUDGE_TIMEOUT", 8)
    prompt_system = setting_value(s, "judge", "inject_prompt_system", None, None)
    prompt_fewshot = setting_value(s, "judge", "inject_prompt_fewshot", None, None)
    if not prompt_system or not prompt_fewshot:
        # 注入 prompt 无源（键缺失/类型不符/空串——含绕过 admin 校验的「开+空」手改文件）→
        # 注入判定不可用返回 None（fail-open；/request 链路落 error 条，巡检项 4 有数据）
        print("[settings] judge 注入 prompt 缺失（settings.json judge.inject_prompt_system/inject_prompt_fewshot），注入判定跳过", flush=True)
        return None
    v = _judge_chat(model, base_url, timeout, prompt_system, prompt_fewshot, text)
    if v is None:
        return None
    try:
        return {"injection": bool(v.get("injection")),
                "confidence": float(v.get("confidence") or 0),
                "attack_type": str(v.get("attack_type") or "")}
    except Exception:
        return None  # verdict 形状损坏 → fail-open（同其余异常语义）


# ---- auto 智能路由（issue #117）：/classify 真实分类器（#115 spike 桩退役收口）----
# 分类器 = #114 票选候选 B：judge 通道外部 LLM pcomplex 校准模式——模型/地址/凭据沿用
# judge 链路（settings judge.model/judge.base_url + JUDGE_API_KEY env 只走环境变量），
# 系统提示只让模型输出 JSON {"p_complex": 0~1}，shim 侧按 routing.threshold 判档
#（p >= 阈值 → complex）。外发纪律同 #93：分类输入先过 mask_pipeline（L1/L2 掩码）再外发。
# routing 节（settings.json 热更新；节缺席或 enabled=false → 零行为：/classify 恒 200 无头）：
#   enabled（默认 false——新层进场先关，验证后再开）/threshold（默认 0.5，#114 §7 推荐
#   默认工作点）/tiers（两档映射，默认 simple→deepseek-v4-flash、complex→gpt-5.6-luna，
#   与网关 modelAliases auto→gpt-5.6-luna 兜底一致；映射目标只选全量开放模型池，
#   避免与 axonhub profile 白名单耦合）/timeout（默认 4s——#114 §8 建议 3~5s：分类在
#   请求关键路径上，judge.timeout=8s 盖不住长尾；超时 fail-open）/max_concurrency
#  （默认 2，**独立预算键**，与商密/注入 judge 不互挤——#114 §8）。
# 会话策略（方案 A）：同会话首轮定档、后续继承；只允许强置信升档（simple→complex，
# 本轮 p_complex ≥ escalate_conf），永不降档；tool-loop 硬锁（最后一条消息
# role=tool 或含 tool_calls → 不分类直接继承）；thinking 锁（升档决策检出
# thinking/redacted_thinking blocks → 放弃升档，见 _router_has_thinking 头注）；
# 会话状态进程内 LRU（TTL 默认 3600s 命中续期，重启即丢=新会话重新定档，无害）。
# issue #119：分类 prompt/升档门槛 escalate_conf/会话 TTL session_ttl/两道锁开关
# tool_loop_lock/thinking_lock 均挪入 routing 节可选键（settings.json 热更新，
# 缺席=下方 ROUTER_* 常量默认=现网行为逐点不变；admin 校验侧同步可选语义）。
# fail-open：分类器超时/异常/verdict 损坏/预算占满 → 200 不带 x-resolved-model 头
#（网关 CEL 回退 + modelAliases 落旗舰，#115 实证通路）；超时/异常/配置坏落
# shadow_log error 条（layer="router"，alert_poller 巡检项 4 消费发飞书）+ print
# 告警行；预算占满只 print 不落条（skip 非层异常，同 #93 语义不污染 error_rate）。

# 分类系统提示（issue #119 起为 routing.prompt 默认值——缺省键时逐字使用本常量，
# 保现网行为）：#114 pcomplex 评测获胜 prompt 原样（评测口径 227 样本 98.2% @thr0.5）
ROUTER_PROMPT_SYSTEM = """你是 LLM 网关的路由分类器。把用户请求分为两档：
- simple：单步、有确定答案、无需长链推理或跨上下文综合——事实问答、短翻译、一句话润色、单行代码修改、简单命令、单工具直取。
- complex：多步推理、设计/权衡、长上下文代码任务、跨文件依赖、专业领域翻译、实验/建模设计、多轮工具编排与错误恢复。
注意：输入文本长短不代表难度；带 [SYSTEM]/[USER]/[TOOL] 标记的是 coding agent 多轮会话形态，按最后待完成的任务定档。
只输出 JSON：{"p_complex": 0 到 1 的小数}，表示「该请求需要旗舰模型（complex 档）」的概率。"""

# issue #119：以下两常量转为 routing.escalate_conf/routing.session_ttl 的内置默认值
#（setting_value 三级取值缺省回退点），缺省键=现网行为不变
ROUTER_ESCALATE_CONF = 0.85  # 升档强置信门槛默认（issue #117 定案）：只在 simple→complex 升档用
ROUTER_SESSION_TTL = 3600    # 会话档位 TTL 默认（秒），命中续期
ROUTER_SESSION_MAX = 1024    # 会话 LRU 容量封顶（内存上限优先，逐出=按新会话重新定档）
_ROUTER_MAX_TOKENS = 64      # #114 评测口径：{"p_complex": x} 输出 64 token 足够（454 次调用 0 解析失败）

# 路由分类并发预算（独立 judge 预算键，#114 §8）：模块级锁+计数器，同 #93 形态——
# 上限每请求现读 settings 热生效，不用 Semaphore（尺寸创建后僵化）。
_ROUTER_INFLIGHT = 0
_ROUTER_LOCK = threading.Lock()

# 会话档位存态：key -> [tier, expire_ts]（list 可变便于命中续期就地更新）
_router_sessions = collections.OrderedDict()
_router_sessions_lock = threading.Lock()


def router_budget_try_enter(limit: int) -> bool:
    """占一个路由分类并发名额；已达 limit 返回 False（不占位）。limit 由调用方每请求现读 settings。"""
    global _ROUTER_INFLIGHT
    with _ROUTER_LOCK:
        if _ROUTER_INFLIGHT >= limit:
            return False
        _ROUTER_INFLIGHT += 1
        return True


def router_budget_exit() -> None:
    """释放一个名额（与 try_enter 成功配对，finally 中调用）。"""
    global _ROUTER_INFLIGHT
    with _ROUTER_LOCK:
        _ROUTER_INFLIGHT = max(0, _ROUTER_INFLIGHT - 1)


def _router_session_get(key, ttl):
    """会话档位查询：命中返回 tier 并续期（TTL 重置 + LRU 移到尾）；未命中/过期返回 None。
    ttl 由调用方每请求现读 settings（routing.session_ttl，issue #119 热更新）。"""
    if not key:
        return None
    now = time.time()
    with _router_sessions_lock:
        ent = _router_sessions.get(key)
        if ent is None:
            return None
        if ent[1] < now:
            del _router_sessions[key]
            return None
        ent[1] = now + ttl  # 命中续期
        _router_sessions.move_to_end(key)
        return ent[0]


def _router_session_put(key, tier, ttl) -> None:
    """定档/升档写入（满员逐出最久未用——LRU；被逐/重启即按新会话重新定档，无害）。
    ttl 由调用方每请求现读 settings（routing.session_ttl，issue #119 热更新）。"""
    if not key:
        return
    with _router_sessions_lock:
        _router_sessions[key] = [tier, time.time() + ttl]
        _router_sessions.move_to_end(key)
        while len(_router_sessions) > ROUTER_SESSION_MAX:
            _router_sessions.popitem(last=False)


def _router_session_key(headers, payload):
    """会话 key 来源优先级（issue #117）：请求头 x-session-id > body.metadata.session_id >
    首轮 user 消息内容哈希（chat 协议无状态，客户端重发全历史，第一条 user 消息是同会话
    稳定指纹）。都不可得 → None（本请求分类结果不入会话）。
    记账：extAuthz 是否上送原始请求头取决于网关转发头范围（#115 spike 未实证），
    头路径拿不到时由 metadata/哈希路径兜底。"""
    sid = headers.get("x-session-id") if headers is not None else None
    if isinstance(sid, str) and sid.strip():
        return "h:" + sid.strip()
    meta = payload.get("metadata")
    if isinstance(meta, dict):
        v = meta.get("session_id")
        if isinstance(v, str) and v.strip():
            return "m:" + v.strip()
    for m in payload.get("messages") or []:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str) and c:
            return "u:" + hashlib.sha256(c.encode()).hexdigest()
        if isinstance(c, list):  # 多段 content：拼接 text 段取哈希
            txt = "".join(p.get("text", "") for p in c
                          if isinstance(p, dict) and isinstance(p.get("text"), str))
            if txt:
                return "u:" + hashlib.sha256(txt.encode()).hexdigest()
    return None


def _router_tool_loop(messages) -> bool:
    """tool-loop 硬锁（issue #117）：最后一条消息 role=tool（OpenAI 工具结果回传）或含
    tool_calls（assistant 发起工具调用待结果形态），或 content 含 tool_result block
    （Anthropic 形态——工具结果随 user 消息回传）→ 工具循环进行中，禁止换档。"""
    if not messages or not isinstance(messages[-1], dict):
        return False
    last = messages[-1]
    if last.get("role") == "tool" or last.get("tool_calls"):
        return True
    c = last.get("content")
    if isinstance(c, list):
        return any(isinstance(p, dict) and p.get("type") == "tool_result" for p in c)
    return False


def _router_has_thinking(messages) -> bool:
    """messages 含 Anthropic thinking/redacted_thinking blocks。官方纪律：thinking 绑产出
    模型，换模型前必须剥离，否则沉默烧钱；但 shim 在 extAuthz 点只回传 model 名、改不了
    messages——故升档决策检出 thinking 即**放弃升档**（保缓存与上下文完整的保守等价
    实现，落条 reason=thinking_lock）。"""
    for m in messages:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and p.get("type") in ("thinking", "redacted_thinking"):
                    return True
    return False


def _router_chat(model, base_url, timeout, text, prompt):
    """路由分类 HTTP 调用（issue #117）：judge 通道同款调用方式（OpenAI 兼容
    /chat/completions、Bearer JUDGE_API_KEY、temperature 0、```json 围栏剥离后
    json.loads、异常返 None fail-open——与 _judge_chat 同纪律）；消息形状与参数按
    #114 pcomplex 评测定稿（system 指令 + 单 user 文本，max_tokens 64）。
    prompt=分类系统提示（routing.prompt，issue #119 起可配，缺省=ROUTER_PROMPT_SYSTEM）。
    text[:4000] 截断与 _judge_chat 一致——超长会话保留首部（system prompt + 首轮
    任务描述是复杂度主要信号；#114 §8 全量送判建议受 _judge_chat 复用口径约束，记账）。"""
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text[:4000]},
        ],
        "max_tokens": _ROUTER_MAX_TOKENS,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        base_url + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {JUDGE_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        content = (d["choices"][0]["message"].get("content") or "").strip()
        content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(content)
    except Exception:
        return None


def router_classify(text: str, settings: dict):
    """auto 路由复杂度分类（#114 票选候选 B 生产落点）：返回 0~1 p_complex；
    异常/超时/verdict 损坏/缺凭据返回 None（fail-open 由调用方落旗舰）。
    模型/地址沿用 settings judge.*（与商密/注入 judge 同通道）；超时独立
    routing.timeout（默认 4s）。settings 由调用方一次读入（热更新每请求重读）。"""
    if not text or not JUDGE_API_KEY:
        return None
    model = setting_value(settings, "judge", "model", "JUDGE_MODEL", "deepseek-v4-flash")
    base_url = setting_value(settings, "judge", "base_url", "JUDGE_BASE_URL", "http://axonhub:8090/v1")
    timeout = setting_value(settings, "routing", "timeout", "ROUTING_TIMEOUT", 4)
    # issue #119：分类系统提示可配（routing.prompt），缺省=ROUTER_PROMPT_SYSTEM 常量逐字
    prompt = setting_value(settings, "routing", "prompt", "ROUTING_PROMPT", ROUTER_PROMPT_SYSTEM)
    v = _router_chat(model, base_url, timeout, text, prompt)
    if not isinstance(v, dict):
        return None
    p = v.get("p_complex")
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1:
        return None  # verdict 形状损坏（含 NaN：比较恒假）→ fail-open（同 judge 各分支语义）
    return float(p)


def route_resolve(payload: dict, headers, settings: dict) -> dict:
    """auto 路由定档（issue #117）。返回决策 dict：
      resolved_model（None=不给结论——调用方 200 无头，网关 CEL 回退 + modelAliases 落旗舰）、
      tier/p_complex（None=本轮未分类：继承/锁定路径）、reason（classify/session_inherit/
      escalate/tool_loop_lock/thinking_lock/fail_open）、session（会话命中布尔位）、
      error（"unavailable"=分类器故障/配置坏，调用方落 shadow_log error 条）、
      latency_ms（分类调用耗时，未分类为 None）。
    决策流：tool-loop 硬锁（不分类直接继承）> 会话继承（complex 存态不再分类——
    永不降档且省一次 LLM 调用）> 升档检查（simple 存态每轮仍分类，强置信才升）>
    首轮分类定档（p >= threshold → complex）。"""
    messages = payload.get("messages")
    messages = messages if isinstance(messages, list) else []
    tiers = setting_value(settings, "routing", "tiers", None,
                          {"simple": "deepseek-v4-flash", "complex": "gpt-5.6-luna"})
    # tiers 值进响应头：过白名单（settings 可能被手改绕过 admin 校验——防响应拆分
    # 纪律与 #115 桩一致）。配置坏=分类不可用 → fail-open + error 落条（要可感知）。
    if not (isinstance(tiers, dict)
            and all(isinstance(tiers.get(k), str) and _CLASSIFY_MODEL_SAFE.fullmatch(tiers[k])
                    for k in ("simple", "complex"))):
        print("[router] routing.tiers 配置非法（simple/complex 须为过白名单的模型名），fail-open 落旗舰", flush=True)
        return {"resolved_model": None, "tier": None, "p_complex": None,
                "reason": "fail_open", "session": False, "error": "unavailable", "latency_ms": None}
    skey = _router_session_key(headers, payload)
    # issue #119 可配项（routing 节可选键，缺席=内置默认保现网行为）：会话 TTL/两道锁开关
    session_ttl = setting_value(settings, "routing", "session_ttl", "ROUTING_SESSION_TTL",
                                ROUTER_SESSION_TTL)
    tool_loop_lock = setting_value(settings, "routing", "tool_loop_lock", "ROUTING_TOOL_LOOP_LOCK", True)
    thinking_lock = setting_value(settings, "routing", "thinking_lock", "ROUTING_THINKING_LOCK", True)
    escalate_conf = setting_value(settings, "routing", "escalate_conf", "ROUTING_ESCALATE_CONF",
                                  ROUTER_ESCALATE_CONF)
    stored = _router_session_get(skey, session_ttl)
    # tool-loop 硬锁（可配开关，缺省开）：不分类直接继承；无会话可继承 → 不改写（网关兜底旗舰=complex 语义等价）
    if tool_loop_lock and _router_tool_loop(messages):
        if stored is not None:
            return {"resolved_model": tiers[stored], "tier": stored, "p_complex": None,
                    "reason": "tool_loop_lock", "session": True, "error": None, "latency_ms": None}
        return {"resolved_model": None, "tier": None, "p_complex": None,
                "reason": "tool_loop_lock", "session": False, "error": None, "latency_ms": None}
    # complex 存态直接继承（永不降档，无需再分类）
    if stored == "complex":
        return {"resolved_model": tiers["complex"], "tier": "complex", "p_complex": None,
                "reason": "session_inherit", "session": True, "error": None, "latency_ms": None}
    # 本轮需分类：首轮定档（无会话）或 simple 存态升档检查。外发前置脱敏（#93 既定纪律）。
    # 评审 P1-1：畸形 messages（非 dict 元素等）会让掩码/抽取抛异常——handler 线程崩掉、
    # 连接无响应断开（无 200 无 error 落条）。此处兜底：fail-open + error 落条
    # （对齐 /request 链路先例），绝不让 /classify 断连（网关 failureMode 只管传输层）。
    try:
        masked_msgs, _, _ = mask_pipeline(
            messages,
            setting_value(settings, "l1", "enabled", "L1_ENABLED", True),
            setting_value(settings, "l2", "enabled", "L2_ENABLED", True),
        )
        text = extract_text(masked_msgs)
    except Exception:
        print("[router] messages 预处理异常（畸形输入?），fail-open 落旗舰", flush=True)
        return {"resolved_model": None, "tier": None, "p_complex": None,
                "reason": "fail_open", "session": stored is not None,
                "error": "unavailable", "latency_ms": None}
    if not text:
        # 无可判输入（空 messages/纯图片）→ 不改写；落条但非 error（同 #92 空 text
        # 不落 error 条纪律，不污染 error_rate）
        return {"resolved_model": None, "tier": None, "p_complex": None,
                "reason": "fail_open", "session": stored is not None, "error": None, "latency_ms": None}
    limit = setting_value(settings, "routing", "max_concurrency", "ROUTING_MAX_CONCURRENCY", 2)
    if not router_budget_try_enter(limit):
        # 预算占满=跳过分类（skip 非层异常，同 #93 语义：print 不落 error 条）→ 不改写落旗舰
        print(f"[router] skipped (concurrency budget {limit})，fail-open 落旗舰", flush=True)
        return {"resolved_model": None, "tier": None, "p_complex": None,
                "reason": "fail_open", "session": stored is not None, "error": None, "latency_ms": None}
    try:
        t0 = time.monotonic()
        p = router_classify(text, settings)
        ms = int((time.monotonic() - t0) * 1000)
    finally:
        router_budget_exit()
    if p is None:
        return {"resolved_model": None, "tier": None, "p_complex": None,
                "reason": "fail_open", "session": stored is not None,
                "error": "unavailable", "latency_ms": ms}
    if stored == "simple":
        # 升档检查：本轮强置信才升（p 低维持 simple 即继承——永不降档）
        if p >= escalate_conf:
            if thinking_lock and _router_has_thinking(messages):  # thinking 锁（见 _router_has_thinking 头注；可配开关缺省开）
                return {"resolved_model": tiers["simple"], "tier": "simple", "p_complex": p,
                        "reason": "thinking_lock", "session": True, "error": None, "latency_ms": ms}
            _router_session_put(skey, "complex", session_ttl)
            return {"resolved_model": tiers["complex"], "tier": "complex", "p_complex": p,
                    "reason": "escalate", "session": True, "error": None, "latency_ms": ms}
        return {"resolved_model": tiers["simple"], "tier": "simple", "p_complex": p,
                "reason": "session_inherit", "session": True, "error": None, "latency_ms": ms}
    # 首轮定档
    threshold = setting_value(settings, "routing", "threshold", "ROUTING_THRESHOLD", 0.5)
    tier = "complex" if p >= threshold else "simple"
    if tier == "simple" and thinking_lock and _router_has_thinking(messages):
        # 评审 P2-1：thinking 锁守首轮定档——无会话存态（TTL 过期/LRU 逐出/无头续聊）
        # 判 simple 但带 thinking blocks 时，换便宜模型不剥 thinking 同属沉默烧钱场景。
        # 不下结论（200 无头 → 网关 modelAliases 兜底旗舰=维持原状）、不落会话卡
        # （下轮重判）；判 complex 不拦（thinking 本就是旗舰产出，发卡与兜底等价）。
        return {"resolved_model": None, "tier": None, "p_complex": p,
                "reason": "thinking_lock", "session": False, "error": None, "latency_ms": ms}
    _router_session_put(skey, tier, session_ttl)
    return {"resolved_model": tiers[tier], "tier": tier, "p_complex": p,
            "reason": "classify", "session": False, "error": None, "latency_ms": ms}


def send_feishu_text(text: str) -> bool:
    """签名发送飞书群机器人文本；成功返回 True。secret/URL 不进日志。"""
    if not FEISHU_WEBHOOK:
        return False
    for attempt in range(ALERT_RETRIES + 1):
        try:
            body = {"msg_type": "text", "content": {"text": text}}
            if FEISHU_SECRET:
                ts = str(int(time.time()))
                body["timestamp"] = ts
                body["sign"] = feishu_lib.feishu_sign(ts, FEISHU_SECRET)
            req = urllib.request.Request(
                FEISHU_WEBHOOK,
                data=json.dumps(body, ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                resp = json.load(r)
            if resp.get("code") == 0 or resp.get("StatusCode") == 0:
                return True
        except Exception:
            pass
        if attempt < ALERT_RETRIES:
            time.sleep(1)
    return False


def format_axonhub_alert(p: dict) -> str:
    """axonhub webhook payload（channel.auto_disabled）→ 飞书文本。"""
    ch = p.get("channel") or {}
    tr = p.get("trigger") or {}
    return (
        f"[ai4s 告警] 渠道被自动禁用\n"
        f"事件: {p.get('event', '-')}\n"
        f"渠道: {ch.get('name', '-')}（provider={ch.get('provider', '-')}）\n"
        f"触发: 状态码 {tr.get('status_code', '-')} 连续 {tr.get('actual_count', '-')}/{tr.get('threshold', '-')} 次\n"
        f"原因: {tr.get('reason', '-')}\n"
        f"时间: {p.get('occurred_at', '-')}"
    )


def load_pii_recognizers() -> list:
    """PII recognizer 定义（issue #15，recognizers/ 首件）——每请求重读，热更新。"""
    try:
        with open(PII_RECOGNIZERS_PATH, encoding="utf-8") as f:
            return json.load(f).get("recognizers", [])
    except Exception:
        return []


def load_terms() -> list:
    try:
        with open(WORDLIST_PATH, encoding="utf-8") as f:
            return json.load(f).get("terms", [])
    except Exception:
        return []  # 词表读不到 → 零命中（fail-open 语义）


def extract_text(messages) -> str:
    parts = []
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    parts.append(p["text"])
    return "\n".join(parts)


def presidio_hits(text: str, latin_terms: list) -> list:
    body = {
        "text": text,
        "language": "en",
        "ad_hoc_recognizers": [
            {
                "name": "ai4s_confidential",
                "supported_entity": "AI4S_CONFIDENTIAL",
                "supported_language": "en",
                "deny_list": [t["value"] for t in latin_terms],
            }
        ],
    }
    req = urllib.request.Request(
        PRESIDIO_URL + "/analyze",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=3) as r:
        results = json.load(r)
    hits = []
    for res in results:
        # 只认我们注入的 ad-hoc 实体的命中；Presidio 内置实体（US_BANK_NUMBER 等低分噪音）忽略
        if res.get("entity_type") != "AI4S_CONFIDENTIAL":
            continue
        snippet = text[res.get("start", 0) : res.get("end", 0)]
        term = next(
            (t for t in latin_terms if t["value"].lower() == snippet.lower()), None
        )
        hits.append({"rule_id": (term or latin_terms[0])["rule_id"], "term": snippet})
    return hits


def is_simple_token(s: str) -> bool:
    """纯字母数字 token（无 CJK、无标点）——可托付 Presidio deny-list。"""
    return s.isascii() and s.replace("-", "").replace("_", "").isalnum()


# PromptGuard 2 注入检测 shadow（issue #30）：检出记日志不阻断，fail-open。
# 开关/阈值走统一 settings（issue #35）；issue #67：promptguard 服务并入——
# HTTP POST $PG_URL 改进程内 pg_engine.score（PG_URL env 退役）。


def pg_guard(text: str):
    """PromptGuard 2 MALICIOUS 概率；异常/未启用返回 None（fail-open）。
    pg.normalize（issue #44）：true 时打分前置归一化（base64 内联解码/零宽清除/
    全角转半角，pg_engine.normalize_for_scoring 进程内单点）——只改打分输入，
    转发原文不动；默认 false 保持现网行为。
    issue #67 进程内化：import pg_engine 在 enabled 判定之后（issue #49 纪律——
    pg.enabled=false 时 import 都不发生，检测路径零 PG 开销）。推理同步阻塞
    （原 HTTP 同为同步；实测 p50≈50ms/p95≈136ms，4000 字符截断 + 512 token 截断封顶，
    无超时概念）；issue #97 起 /request 调用点经 pg_guard_async 有界异步执行器提交，
    本函数本体（判定语义）不变；异常=放行+记日志（与原 HTTP 错误同语义，只记异常类型不含文本）。"""
    if not text:
        return None
    settings = load_settings()
    if not setting_value(settings, "pg", "enabled", "PG_ENABLED", False):
        return None
    normalize = setting_value(settings, "pg", "normalize", "PG_NORMALIZE", False)
    try:
        import pg_engine

        t = text[:4000]
        if normalize is True:
            t = pg_engine.normalize_for_scoring(t)
        return pg_engine.score(t)
    except Exception as e:
        print(f"[injection.shadow] fail-open: {type(e).__name__}", flush=True)
        return None


# PG 判定异步化（issue #97）：推理（p50≈50ms/p95≈136ms）挪出 /request handler 线程——
# 响应定稿发回后，PG 段（score→shadow_log.record/malicious print，#92 记录形状原样）提交到
# 有界单线程执行器，handler 立即返回释放线程。单 worker 串行推理：PG 仅 shadow 观测，
# 串行封顶 CPU（onnxruntime 会话进程级单例，无需并发）；积压超上限丢弃并 print 一行——
# 不抛、不落 shadow_log error 条（丢弃非层异常，同 #93 skip 语义，不污染 error_rate）。
# latency_ms 口径与同步版一致：纯判定耗时（pg_guard 调用全程，含 settings 重读+推理），
# 排队等待不计。惰性首用启动 + daemon 线程：import app 不起执行器（同 alert_poller
# 「import 不起线程」纪律，单测环境安全），不阻止进程退出。
PG_ASYNC_BACKLOG = 64  # 积压上限（shadow 观测丢条可接受，内存/线程占用封顶优先）
_pg_async_queue = None  # None=未启动
_pg_async_lock = threading.Lock()


def _pg_worker(q) -> None:
    """执行器消费循环：逐 job 执行；job 异常吞没只 print（pg_guard 自身已 fail-open，此为防御双保险）。"""
    while True:
        job = q.get()
        try:
            job()
        except Exception as e:
            print(f"[injection.shadow] executor 吞没异常: {type(e).__name__}", flush=True)


def _pg_executor():
    """取执行器队列（惰性首用启动 daemon worker；双重检查锁保证单 worker）。"""
    global _pg_async_queue
    if _pg_async_queue is None:
        with _pg_async_lock:
            if _pg_async_queue is None:
                q = queue.Queue(maxsize=PG_ASYNC_BACKLOG)
                threading.Thread(target=_pg_worker, args=(q,), name="pg-shadow", daemon=True).start()
                _pg_async_queue = q
    return _pg_async_queue


def pg_guard_async(text: str, threshold, q=None) -> bool:
    """提交一次 PG shadow 判定（score→落条/print，判定语义与记录形状和同步版逐字段一致）。
    返回 True=已受理；False=积压满丢弃（print 一行，不落条、不抛）。q 可注入测试队列（直测 seam）。"""
    dst = q if q is not None else _pg_executor()

    def job():
        _t0 = time.monotonic()
        score = pg_guard(text)
        _ms = int((time.monotonic() - _t0) * 1000)  # latency_ms=纯判定耗时，排队不计（头注口径）
        if score is not None:
            shadow_log.record("pg", hit=score >= threshold, score=score, latency_ms=_ms)
            if score >= threshold:
                print(f"[injection.shadow] malicious={score:.3f} >= {threshold}", flush=True)
        else:
            shadow_log.record("pg", error="unavailable", latency_ms=_ms)

    try:
        dst.put_nowait(job)
        return True
    except queue.Full:
        print(f"[injection.shadow] dropped (async backlog full {PG_ASYNC_BACKLOG})", flush=True)
        return False


# ---- 归一化前置（issue #22）----
# 只用于检测：全角→半角、词表字符繁简映射、空白/横线/下划线分隔容忍；
# mask 经 index map 映射回原文位置，原文结构不丢。纯 stdlib，无新故障面。
_FULLWIDTH = {i: chr(i - 0xFEE0) for i in range(0xFF01, 0xFF5F)}
_FULLWIDTH[0x3000] = " "
# 繁简映射：覆盖当前词表用字（词表扩词时按需补字，宁缺勿滥——错映射会误伤）
_TRAD2SIMP = dict(zip("鳳計劃鯨藍號話統雲網內級鳳", "凤计划鲸蓝号话统云网内级凤"))
_SEP = set(" \t\r\n-_")

# L1/L1.5 格式规则统一源（issue #33）：每请求重读（与 load_terms 同纪律，热更新免重启）；
# fail-open：文件缺失/损坏 → 空规则（格式检测层失效但不 500，同契约 fail-open 分级）。
# shim_patterns 为归一化检测变体（分隔符已剔除，Python regex 支持 lookaround）；
# 无 shim_patterns 的规则不参与归一化检测（review #5：不静默回退 gateway_patterns）。
FORMAT_RULES_PATH = os.environ.get("FORMAT_RULES_PATH", "/dlp/format-rules.json")


def load_format_rules() -> list:
    try:
        with open(FORMAT_RULES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        # fail-open 必须留痕（契约硬性配套）：只记路径与错误类型，不含敏感值
        print(f"[format-rules] fail-open: {FORMAT_RULES_PATH}: {type(e).__name__}", flush=True)
        return []
    rules = data.get("rules", []) if isinstance(data, dict) else []
    return rules if isinstance(rules, list) else []


def _norm_compiled(rule: dict) -> list:
    """编译单条规则的 shim 侧归一化 pattern（shim_patterns）；坏 pattern 跳过。
    无 shim_patterns 的规则不参与归一化检测（review #5：删 gateway_patterns 静默回退——
    两栈 pattern 语义不同（归一化文本 vs 原文），静默回退是漏检面，显式不写才可见）。"""
    out = []
    for p in rule.get("shim_patterns") or []:
        try:
            out.append(re.compile(p))
        except re.error:
            continue
    return out


def normalize_hard(s: str):
    """返回 (归一化文本, idx_map)：idx_map[归一化下标] = 原文下标。全角→半角、繁→简、剔除分隔符。"""
    out, idx = [], []
    for i, ch in enumerate(s):
        c = _FULLWIDTH.get(ord(ch), ch)
        c = _TRAD2SIMP.get(c, c)
        if c in _SEP:
            continue
        out.append(c)
        idx.append(i)
    return "".join(out), idx


def norm_secret_hits(norm: str, rules: list = None) -> list:
    """归一化文本上的 L1 reject 命中（issue #33 起读 format-rules.json）。
    逐 pattern 命中逐条 append（同规则多 pattern 全中会出现多次，下游 set 化，行为同原硬编码表）。
    rules 缺省内部加载；调用方在循环中使用时传入一次加载的结果（review #4）。"""
    if rules is None:
        rules = load_format_rules()
    hits = []
    for rule in rules:
        if not rule.get("enabled") or rule.get("action") != "reject" or not rule.get("code"):
            continue
        for rgx in _norm_compiled(rule):
            if rgx.search(norm):
                hits.append(rule["code"])
    return hits


def norm_term_hits(norm_low: str, terms: list) -> list:
    hits = []
    for t in terms:
        nv, _ = normalize_hard(t["value"])
        if nv and nv.lower() in norm_low:
            hits.append(t)
    return hits


def norm_pii_mask_in_text(s: str, rules: list = None):
    """对单段原文做归一化 PII 检测并回映 mask；返回 (新文本, 命中实体列表)。
    issue #33 起规则读 format-rules.json（action=mask 项；entity/replacement 取自规则）。"""
    norm, idx = normalize_hard(s)
    if not norm:
        return s, []
    if rules is None:
        rules = load_format_rules()
    spans = []  # (orig_start, orig_end, replacement, entity)
    for rule in rules:
        if not rule.get("enabled") or rule.get("action") != "mask":
            continue
        entity = rule.get("entity") or rule.get("code") or "PII"
        repl = rule.get("replacement") or f"【PII:{entity}】"
        for rgx in _norm_compiled(rule):
            for m in rgx.finditer(norm):
                spans.append((idx[m.start()], idx[m.end() - 1] + 1, repl, entity))
    if not spans:
        return s, []
    spans.sort(key=lambda x: x[0], reverse=True)
    out, entities = s, []
    last_start = len(s) + 1
    for st, en, repl, entity in spans:
        if en > last_start:  # 重叠跨度跳过（先长后短原则已由排序保证大致正确）
            continue
        out = out[:st] + repl + out[en:]
        last_start = st
        entities.append(entity)
    return out, entities


def norm_mask_messages(messages, rules: list = None):
    """逐消息归一化 PII mask（issue #22）；返回 (new_messages, any_masked, entities)。
    规则一次加载传入各消息（issue #33：避免逐消息重读文件）；
    rules 缺省内部加载，调用方在循环中使用时传入一次加载的结果（review #4）。"""
    if rules is None:
        rules = load_format_rules()
    out, any_masked, all_entities = [], False, set()
    for m in messages or []:
        m2 = dict(m)
        c = m.get("content")
        if isinstance(c, str):
            new_c, ents = norm_pii_mask_in_text(c, rules)
            if ents:
                m2["content"] = new_c
                any_masked = True
                all_entities |= set(ents)
        elif isinstance(c, list):
            parts = []
            for p in c:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    new_t, ents = norm_pii_mask_in_text(p["text"], rules)
                    if ents:
                        p = {**p, "text": new_t}
                        any_masked = True
                        all_entities |= set(ents)
                parts.append(p)
            m2["content"] = parts
        out.append(m2)
    return out, any_masked, sorted(all_entities)


def mask_response_body(body, l1_enabled: bool = True, l2_enabled: bool = True):
    """响应侧 mask（issue #23）：对 completion JSON 的 choices[].message.content/reasoning_content
    做归一化 secrets/词表/PII 检测，命中字段整体替换为掩码。返回 (新body, 命中实体/规则列表)。
    响应侧不 reject（员工看到莫名错误比截断更糟）；命中只记规则，不落原文。
    分层总开关（issue #40）：模块关=处处关——l1_enabled=False 跳过 secrets/格式 PII 检测，
    l2_enabled=False 跳过词表检测（缺省 True 保旧调用方行为）。"""
    if not isinstance(body, dict):
        return body, []
    choices = body.get("choices")
    if not isinstance(choices, list):
        return body, []
    terms = load_terms()
    rules = load_format_rules()  # 响应级一次加载（review #4）：scan 按字段调用，避免逐字段重读+编译
    hits = set()

    def scan(text):
        found = []
        if not isinstance(text, str) or not text:
            return found
        norm, _ = normalize_hard(text)
        if l1_enabled:
            found += norm_secret_hits(norm, rules)
            _, _, ents = norm_mask_messages([{"role": "assistant", "content": text}], rules)
            found += ents
        if l2_enabled:
            found += [t["rule_id"] for t in norm_term_hits(norm.lower(), terms)]
        return found

    out = json.loads(json.dumps(body))  # 深拷贝
    for ch in out.get("choices", []):
        # 非流式 message.*；流式分片 delta.*（issue #23 实测：流式 chunk 走 delta）
        for container in (ch.get("message"), ch.get("delta")):
            if not isinstance(container, dict):
                continue
            for field in ("content", "reasoning_content"):
                v = container.get(field)
                if isinstance(v, str) and v:
                    f = scan(v)
                    if f:
                        hits.update(f)
                        container[field] = f"【ai4s DLP：应答含敏感内容已屏蔽（{', '.join(sorted(set(f)))}）】"
    return out, sorted(hits)


def analyze(text: str, terms: list) -> list:
    # Presidio deny-list 对含标点/中文的词会过度或不足匹配（实测）：
    # 仅简单 token 走 Presidio；其余一律 shim 子串直配（词表语义本来就是确定性子串）。
    via_presidio = [t for t in terms if is_simple_token(t["value"])]
    via_substring = [t for t in terms if not is_simple_token(t["value"])]
    hits = presidio_hits(text, via_presidio) if via_presidio else []
    low = text.lower()
    for t in via_substring:
        if t["value"].lower() in low:
            hits.append({"rule_id": t["rule_id"], "term": t["value"]})
    return hits


# ---- 阻断记录增强（issue #134）：身份指纹 + 命中摘录 ----

def bearer_token(headers) -> str:
    """从 Authorization 头取 Bearer token（webhook headers CEL 注入的客户端原始 API key）。
    缺失/非 Bearer/空串 → ''（调用方按 fail-closed 处理）。"""
    auth = headers.get("Authorization") or ""
    parts = auth.split(None, 1)
    return parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""


def key_hash_from_headers(headers):
    """请求 key 的 SHA-256 指纹（与 bypass_keys 同纪律：不明文落盘；
    身份反查在 shadow-verdicts 读侧经 admin GraphQL 哈希比对完成）。无 token → None。"""
    tok = bearer_token(headers)
    return hashlib.sha256(tok.encode()).hexdigest() if tok else None


def _mask_excerpt(s: str) -> str:
    """命中内容掩码摘录：≤4 字符全掩，更长留头尾各 2 字符；整体截 30 字符防超长。"""
    s = (s or "").strip()
    if not s:
        return ""
    if len(s) <= 4:
        return "****"
    masked = s[:2] + "***" + s[-2:]
    return masked[:30]


def block_excerpts(norm: str, term_hits: list, hits: list, secret_codes: list,
                   rules: list = None, limit: int = 5) -> list:
    """451 落条的命中摘录（issue #134）：[{rule, text}]。
    词表命中（confidential.*）原样保留——词表为管理员自配清单，非用户敏感数据；
    secrets 命中串掩码留头尾（不存完整密钥）；EDM/其他族无摘录（无可安全展示内容）。
    不含原文上下文；去重后至多 limit 条。"""
    out, seen = [], set()

    def _push(rule_id, text):
        key = (rule_id, text)
        if text and key not in seen and len(out) < limit:
            seen.add(key)
            out.append({"rule": rule_id, "text": text})

    for t in term_hits or []:  # 归一化词表命中（dict 带 value/rule_id）
        _push(t.get("rule_id") or "", t.get("value") or "")
    for h in hits or []:  # analyze() 词表命中（term 即命中片段/词值）
        _push(h.get("rule_id") or "", h.get("term") or "")
    if secret_codes:
        rules = load_format_rules() if rules is None else rules
        for code in secret_codes:
            for rule in rules:
                if rule.get("code") != code:
                    continue
                for rgx in _norm_compiled(rule):
                    m = rgx.search(norm or "")
                    if m:
                        _push(code, _mask_excerpt(m.group(0)))
                        break
                break
    return out



# ---- privacy-filter（OPF）第二检测器（issue #127）----
# L2 内嵌：l2 触发时 Presidio 之外追加 OPF 中文 NER，span 合并（重叠取长）后统一回替换。
# 不进生产默认路径（#122 实测：质量 F1 0.92 但 torch CPU 延迟不可用）——开关缺省关，
# sidecar compose profile 默认不启动，等有 GPU/Q4 机型随时启用。
# fail-open：sidecar 不可达/超时/坏响应 = 放行 + 日志（对齐 judge/PG 纪律，不落原文）。
# OPF 标签 → (中文实体名, 替换文本)（#122 评测语料实测全量 8 标签）
_OPF_LABELS = {
    "private_person": ("姓名", "【PII:姓名】"),
    "private_phone": ("电话", "【PII:电话】"),
    "private_email": ("邮箱", "【PII:邮箱】"),
    "private_address": ("地址", "【PII:地址】"),
    "private_date": ("日期", "【PII:日期】"),
    "account_number": ("证件/账号", "【PII:证件/账号】"),
    "private_url": ("链接", "【PII:链接】"),
    "secret": ("密钥", "【PII:密钥】"),
}


def opf_config(settings: dict) -> dict | None:
    """l2.opf 子节 → 运行配置（issue #127）。关/缺席/类型坏 → None（不启用）。
    env 回退：OPF_ENABLED=1 仅在子节缺席时启用（settings 显式关优先，对齐
    setting_value 三级语义）；url 键缺席回退模块级 OPF_URL（env OPF_URL/内置默认，
    对齐 judge.base_url 的 settings>env>默认层级）。"""
    sec = settings.get("l2")
    opf = sec.get("opf") if isinstance(sec, dict) else None
    if opf is None:
        if os.environ.get("OPF_ENABLED") == "1":
            return {"url": OPF_URL, "timeout": 8.0, "max_chars": 4000}
        return None
    if not isinstance(opf, dict) or opf.get("enabled") is not True:
        return None
    timeout_ms = opf.get("timeout_ms")
    max_chars = opf.get("max_chars")
    url = opf.get("url")
    return {
        "url": url if isinstance(url, str) and url else OPF_URL,
        "timeout": timeout_ms / 1000 if _is_positive_number(timeout_ms) else 8.0,
        "max_chars": max_chars if isinstance(max_chars, int) and not isinstance(max_chars, bool)
        and max_chars > 0 else 4000,
    }


def _is_positive_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def opf_analyze(text: str, cfg: dict) -> list:
    """OPF sidecar /analyze（issue #127）：返回 span 元组 [(start, end, replacement, entity)]。
    fail-open：任何线路异常（不可达/超时/断连/坏 JSON）→ 日志 + []（放行，不拖垮 L2）。
    max_chars 截断：超长只检前段（torch CPU 延迟随长度爆炸，#122 实测 5.1k 字 55s+）。"""
    text = text[: cfg["max_chars"]]
    if not text:
        return []
    req = urllib.request.Request(
        cfg["url"] + "/analyze",
        data=json.dumps({"text": text}, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
            data = json.load(resp)
    except Exception as e:
        print(f"[opf] sidecar 调用失败放行: {type(e).__name__}", flush=True)
        return []
    spans = []
    for h in (data.get("spans") if isinstance(data, dict) else None) or []:
        if not isinstance(h, dict):
            continue
        zh = _OPF_LABELS.get(h.get("label"))
        if zh is None:
            continue  # 未知标签忽略（不落原文）
        try:
            st, en = int(h["start"]), int(h["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= st < en <= len(text)):
            continue
        spans.append((st, en, zh[1], zh[0]))
    return spans


def merge_spans(spans: list) -> list:
    """重叠跨度合并（issue #127，重叠取长）：长度降序贪心收不重叠者（等长取 start 早者），
    返回 start 降序（供 apply_spans 自尾向首回替换不错位）。"""
    kept = []
    for sp in sorted(spans, key=lambda s: (-(s[1] - s[0]), s[0])):
        if any(sp[0] < k[1] and sp[1] > k[0] for k in kept):
            continue
        kept.append(sp)
    kept.sort(key=lambda s: s[0], reverse=True)
    return kept


def apply_spans(text: str, spans: list) -> tuple:
    """按 span（start 降序）回替换；返回 (masked_text, sorted_entities)。空 spans 原样返回。"""
    if not spans:
        return text, []
    out = text
    for st, en, repl, _entity in spans:
        out = out[:st] + repl + out[en:]
    return out, sorted({sp[3] for sp in spans})


def presidio_pii_spans(text: str, recs: list) -> list:
    """Presidio ad-hoc pattern recognizer PII 检测（issue #15）：返回 span 元组列表。
    recs 为空时不调 sidecar 直接返回 []。"""
    if not recs or not text:
        return []
    adhoc = [
        {
            "name": r["name"],
            "supported_entity": r["entity"],
            "supported_language": "en",
            "patterns": r["patterns"],
            "context": r.get("context", []),
        }
        for r in recs
    ]
    body = {"text": text, "language": "en", "ad_hoc_recognizers": adhoc}
    req = urllib.request.Request(
        PRESIDIO_URL + "/analyze",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=3) as resp:
        results = json.load(resp)
    # 只认我们注入的 PII 实体的命中（内置实体噪音忽略）
    own = {r["entity"] for r in recs}
    repl = {r["entity"]: r.get("replacement", f"【PII:{r['entity']}】") for r in recs}
    spans = []
    for h in results:
        ent = h.get("entity_type", "")
        if ent not in own:
            continue
        spans.append((h["start"], h["end"], repl.get(ent, "【PII】"), ent))
    return spans


def pii_analyze_and_mask(text: str, recs: list) -> tuple:
    """PII 识别 + 脱敏（issue #15）：Presidio 单检测器版 = presidio_pii_spans + 合并回替换。
    返回 (masked_text, hit_entities)。recs 为空时原样返回。
    issue #127 起重叠 span 合并取长（旧版逐命中回替换，重叠时会按原偏移错位）。"""
    return apply_spans(text, merge_spans(presidio_pii_spans(text, recs)))


def mask_message_contents(messages, recs, opf_cfg=None):
    """逐消息脱敏；返回 (new_messages, any_masked, entities)。
    issue #127：opf_cfg 非 None 时同文本追加 OPF 检测（fail-open），与 Presidio span
    合并（重叠取长）后统一回替换；recs 空但 opf_cfg 出席时仍走 OPF。"""
    if not recs and not opf_cfg:
        return messages, False, []

    def mask_text(text):
        spans = presidio_pii_spans(text, recs)
        if opf_cfg:
            spans += opf_analyze(text, opf_cfg)
        return apply_spans(text, merge_spans(spans))

    all_entities = set()
    any_masked = False
    out = []
    for m in messages or []:
        m2 = dict(m)
        c = m.get("content")
        if isinstance(c, str):
            masked, ents = mask_text(c)
            if ents:
                m2["content"] = masked
                any_masked = True
                all_entities |= set(ents)
        elif isinstance(c, list):
            parts = []
            for p in c:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    masked, ents = mask_text(p["text"])
                    if ents:
                        p = {**p, "text": masked}
                        any_masked = True
                        all_entities |= set(ents)
                parts.append(p)
            m2["content"] = parts
        out.append(m2)
    return out, any_masked, sorted(all_entities)


def mask_pipeline(messages, l1_on: bool, l2_on: bool):
    """L1/L2 掩码管线（/request 掩码段原顺序，issue #93 起 judge 外发输入同口径复用）：
    先归一化 mask（l1），无命中再走 Presidio PII mask（l2）。返回 (masked_msgs, any_masked, entities)。
    issue #127：l2 分支内嵌 OPF 第二检测器（l2.opf.enabled 开才调，fail-open）。"""
    masked_msgs, any_masked, entities = messages, False, []
    if l1_on:
        masked_msgs, any_masked, entities = norm_mask_messages(messages)
    if not any_masked and l2_on:
        masked_msgs, any_masked, entities = mask_message_contents(
            messages, load_pii_recognizers(), opf_config(load_settings()))
    return masked_msgs, any_masked, entities


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默（命中敏感值不进 shim 日志，契约）
        pass

    def _json(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _req_model(self, payload):
        """请求模型名（issue #116）：webhook 请求体协议不含 model 字段（MaskAction 只能
        替换 messages），生产链路的模型名靠 agentgateway webhook headers CEL 注入的
        x-model 头（llmRequest.model，上游 webhook.rs 测试实证形状）——头优先；
        回退 body.model（/judge-test 直测与旧调用方形状）；再回退 payload 顶层 model
        （issue #129 /bv1-authz：extAuthz includeRequestBody 透传的是原始 OpenAI
        请求体，model 在顶层）。三处都缺 → None，shadow_log 键级省略纪律不变
        （非 None 才写入）。"""
        return self.headers.get("x-model") or (payload.get("body") or {}).get("model") or payload.get("model")

    def _bypass_entry(self):
        """Key 级 DLP 绕行（issue #129）：webhook headers CEL 注入的 Authorization 头取 Bearer
        token → bypass_keys.lookup（SHA-256 比对，不明文落盘）。头缺失/未登记/已停用 → None
        （fail-closed：任何不确定都不绕行）。token 提取与 key_hash_from_headers 同一辅助件。"""
        return bypass_keys.lookup(bearer_token(self.headers))

    @staticmethod
    def _bypass_overlay(settings, entry):
        """按层绕行 = 把覆盖层的 enabled 强制 False 的 settings 副本（下游门控全部经
        setting_value 读 settings，副本覆盖即整层跳过；不改盘、不影响其他请求）。"""
        out = dict(settings)
        for layer in entry.get("layers") or []:
            sec = dict(out.get(layer) or {})
            sec["enabled"] = False
            out[layer] = sec
        return out

    def _bypass_audit(self, payload, detail):
        """绕行审计（issue #129）：shadow_log 落条（layer=bypass，只带模型名与范围，不落原文
        不记 token）+ stdout 一行。/bv1-authz 与 /request //response 同一通道。"""
        model = self._req_model(payload) if isinstance(payload, dict) else None
        shadow_log.record("bypass", model=model, reason=detail)
        print(f"[dlp.bypass] {detail}", flush=True)

    def _bv1_authz(self, payload):
        """/bv1 全绕入口的 extAuthz 门（issue #129）：仅 scope=all 且启用中的名单 key 放行，
        其余一律 403（非 2xx 由网关直回客户端）。fail-closed：网关侧 failureMode=deny，
        shim 宕机时本入口拒绝服务——绕行特权不享受检测链 fail-open 待遇。"""
        bp = self._bypass_entry()
        if bp and bp.get("scope") == "all":
            self._bypass_audit(payload or {}, "entry=/bv1 scope=all（全量绕行，含 L1 密钥红线）")
            self._json(200, {"ok": True})
        else:
            self._json(403, {"error": "key not authorized for bypass entry"})

    def _playground_authz(self):
        """playground 模型调用闸门（issue #128）：beta6 上游 /admin/playground/chat 零 scope
        校验（2026-09-02 实证：空 scopes 的邀请注册用户 200 直通渠道出真实应答），网关侧补
        extAuthz 门——Bearer JWT 内省后 owner/系统 write_requests/请求 X-Project-ID 项目成员
        scopes 含 write_requests 放行，其余一律 403；token/项目头缺失 403。
        fail-closed：网关 failureMode=deny + 内省失败非 2xx（同 /bv1 纪律，shim 宕机时
        playground 拒绝服务——playground 是控制台测试工具，不适用检测链 fail-open）。"""
        auth = self.headers.get("Authorization") or ""
        parts = auth.split(None, 1)
        token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""
        pid = (self.headers.get("X-Project-ID") or "").strip()
        if not token or not pid:
            self._json(403, {"error": "playground requires bearer token and X-Project-ID"})
            return
        me, err = admin_api._introspect(token, admin_api._ME_PROJECTS_QUERY)
        if err is not None:
            self._json(err, {"error": "introspection unavailable" if err == 503 else "unauthorized"})
            return
        if admin_api.playground_allowed(me, pid):
            self._json(200, {"ok": True})
        else:
            print(f"[dlp.playground] 403 deny user={me.get('email')} project={pid}", flush=True)
            self._json(403, {"error": "playground requires write_requests in the selected project"})

    def _graphql_authz(self):
        """/admin/graphql extAuthz 门（2026-09-03，上游未修网关先行）：beta6 四实体无 ent
        policy + 两操作无 scope 校验（模式表与背景见 admin_api._GRAPHQL_* 注释）。正常查询
        仅正则扫描零内省放行；命中受限模式才 Bearer 内省核系统 scope。fail-closed：body
        缺失/超限/不可解码一律 403（超限网关侧 allowPartialMessage=false 已先拒，本端兜底
        「危险字段推到截断点之后」的绕行）。GET 无法检查（extAuthz 子请求不带原始 query
        string）且控制台 GraphQL 只走 POST，由 do_GET 分支直接 403。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_GRAPHQL_AUTHZ_BODY:
            self._json(403, {"error": "graphql-authz requires a bounded request body"})
            return
        try:
            text = self.rfile.read(length).decode("utf-8")
        except Exception:
            self._json(403, {"error": "graphql-authz body undecodable"})
            return
        required = admin_api.graphql_required_scopes(text)
        if not required:
            self._json(200, {"ok": True})
            return
        auth = self.headers.get("Authorization") or ""
        parts = auth.split(None, 1)
        token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""
        if not token:
            self._json(403, {"error": f"restricted graphql pattern requires system scope: {', '.join(required)}"})
            return
        me, err = admin_api._introspect(token)  # _ME_QUERY 已含 isOwner+scopes（判定不需要项目成员档）
        if err is not None:
            self._json(err, {"error": "introspection unavailable" if err == 503 else "unauthorized"})
            return
        if admin_api.graphql_authz_allowed(me, required):
            self._json(200, {"ok": True})
        else:
            print(f"[dlp.graphql] 403 deny user={me.get('email')} need={required}", flush=True)
            self._json(403, {"error": f"restricted graphql pattern requires system scope: {', '.join(required)}"})

    def do_GET(self):
        if admin_api.handle(self, "GET"):  # admin 平面（issue #31）：/dlp-admin/* 优先分流
            return
        if self_api.handle(self, "GET"):  # 员工自助平面（issue #74）：/self/*
            return
        if self.path == "/bv1-authz":
            self._bv1_authz(None)
            return
        if self.path == "/graphql-authz":
            # GET 无法检查（extAuthz 子请求不带原始 query string，query 参数里的受限模式
            # 看不到）且控制台 GraphQL 只走 POST——直接 403（fail-closed，见 _graphql_authz）
            self._json(403, {"error": "graphql-authz: method not inspectable"})
            return
        if self.path == "/classify":
            # extAuthz 会对 /v1 路由的全部方法发起同方法授权调用（如 GET /v1/models →
            # GET /classify）；非 2xx 会被网关当 deny 决策直接回给客户端（failureMode 只管
            # 传输错误）。GET 无 body 可分类 → 200 不带响应头（网关 CEL 回退原 model）。
            self._json(200, {"resolved_model": None})
            return
        self._json(200 if self.path == "/healthz" else 404, {"ok": self.path == "/healthz"})

    def do_POST(self):
        if admin_api.handle(self, "POST"):  # admin 平面（issue #31）：/dlp-admin/* 优先分流
            return
        if self_api.handle(self, "POST"):  # 员工自助平面（issue #74 评审 P2）：已鉴权 POST → 显式 404
            return
        if self.path == "/bv1-authz":
            # /bv1 全绕入口鉴权（issue #129）；body 只为审计读模型名，解析失败不挡鉴权
            payload = None
            try:
                length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                payload = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                payload = {}
            self._bv1_authz(payload)
            return
        if self.path == "/playground-authz":
            # playground extAuthz 门（issue #128）：只读头鉴权不读 body（网关不开 includeRequestBody）
            self._playground_authz()
            return
        if self.path == "/graphql-authz":
            # /admin/graphql extAuthz 门（2026-09-03）：读 body 扫受限模式（网关开 includeRequestBody）
            self._graphql_authz()
            return
        if self.path == "/classify":
            # auto 智能路由（issue #117 真实分类器；#115 spike 桩已退役）：
            # agentgateway extAuthz HTTP 授权服务形态。协议语义不变：2xx=放行（本端点只
            # 分类不鉴权，任何输入都 200，永不阻断——非 2xx 会被网关当 deny 决策）；
            # 改写结论经响应头 x-resolved-model 回传（网关同路由 ai.transformations CEL
            # 读 extauthz.resolved_model 改写 model，缺头回退原值 + modelAliases
            # auto→gpt-5.6-luna 静态兜底）。
            # 行为：routing.enabled=false（缺省/节缺席）→ 所有 model 200 无头（现网零
            # 变化）；enabled=true 且 model=="auto" → route_resolve 分类定档（两档映射/
            # 会话继承/强置信升档/tool-loop 锁/thinking 锁；fail-open 路径不给结论=200
            # 无头落旗舰）；其他 model → 200 无头（桩的原值回显退役——transformations
            # 无头回退原 model 语义等价）。enabled=true 的每个 auto 请求落一条
            # shadow_log layer="router" 决策条（无原文无会话 key，供阈值校准回放与
            # router 层异常率巡检）。
            try:
                length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                payload = json.loads(self.rfile.read(length) or b"{}")
                model = payload.get("model") if isinstance(payload, dict) else None
            except Exception:
                payload, model = {}, None
            resolved = None
            settings = load_settings()
            if model == "auto" and setting_value(settings, "routing", "enabled", "ROUTING_ENABLED", False):
                r = route_resolve(payload, self.headers, settings)
                resolved = r["resolved_model"]
                shadow_log.record(
                    "router", model="auto", resolved_model=resolved, tier=r["tier"],
                    p_complex=r["p_complex"], reason=r["reason"], session=r["session"],
                    error=r["error"], latency_ms=r["latency_ms"])
                if r["error"]:
                    print(f"[router] fail-open: 分类器不可用落旗舰（reason={r['reason']}）", flush=True)
                elif resolved:
                    _p = "" if r["p_complex"] is None else f" p_complex={r['p_complex']:.2f}"
                    print(f"[router] auto -> {resolved} (tier={r['tier']} reason={r['reason']}{_p})", flush=True)
            body = json.dumps({"resolved_model": resolved}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if resolved:
                self.send_header("x-resolved-model", resolved)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/feishu-alert":
            # axonhub webhook → 飞书适配（issue #17）
            try:
                length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                payload = json.loads(self.rfile.read(length) or b"{}")
                ok = send_feishu_text(format_axonhub_alert(payload))
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})
                return
            self._json(200 if ok else 502, {"ok": ok})
            return
        if self.path == "/judge-test":
            # 语义层直测端点（issue #21）：不进请求链，供回归脚本测 judge 准确率/延迟
            # issue #93：与 /request 链路同口径——text 包成单条 messages 过同一掩码管线再送 judge
            # （semantic-eval 量的即生产输入）；直测显式触发，不走采样/并发预算
            # issue #105：可选 duty="inject" 走注入第二职责判定（judge_inject_text，同一掩码
            # 管线同口径；受 inject_enabled 门控——关态 verdict=null，与商密 duty 受 enabled
            # 门控同语义），缺省/省略 duty=商密判定（既有形状不变）；非法 duty 显式 400
            try:
                length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                payload = json.loads(self.rfile.read(length) or b"{}")
                duty = payload.get("duty") or "commercial"
                if duty not in ("commercial", "inject"):
                    self._json(400, {"error": "duty 必须是 inject 或缺省（commercial 商密判定）"})
                    return
                t0 = time.time()
                settings = load_settings()
                masked_msgs, _, _ = mask_pipeline(
                    [{"role": "user", "content": payload.get("text", "")}],
                    setting_value(settings, "l1", "enabled", "L1_ENABLED", True),
                    setting_value(settings, "l2", "enabled", "L2_ENABLED", True),
                )
                judge_input = judge_pre_decode(extract_text(masked_msgs))  # 前置单趟解码（issue #107）
                verdict = (judge_inject_text(judge_input) if duty == "inject"
                           else judge_text(judge_input))
                self._json(200, {"verdict": verdict, "latency_ms": round((time.time() - t0) * 1000)})
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})
            return
        if self.path == "/response":
            # 响应侧 DLP（issue #23）：模型应答 secrets/词表/PII 命中 → 451 拒绝。
            # 语义说明：流式（SSE）下 agentgateway 会缓冲整流后评估，mask 改写对流式无效（实测），
            # 故响应侧命中统一拒绝——应答含敏感内容本身就是异常信号，阻断比部分遮盖更正确。
            # 总开关（issue #40）：response.enabled=false → 整个分支直接放行不检测；
            # l1/l2 总开关在响应侧同样生效（模块关=处处关）。
            try:
                length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                payload = json.loads(self.rfile.read(length) or b"{}")
                resp_body = payload.get("body")
                settings = load_settings()
                _layer_switch_observe(settings)  # 开关状态变化 warn（issue #40 review）
                # Key 绕行（issue #129）：scope=all 或覆盖 response 层 → 直接放行；覆盖 l1/l2
                # 则响应侧对应检测族同步跳过（与请求侧「模块关=处处关」同语义）。layers 只含
                # 请求侧层（pg/rules/judge/edm）时响应侧照常检测、不落「跳过」审计条——
                # 审计只记本侧真实发生的跳过。
                bp = self._bypass_entry()
                if bp and (bp["scope"] == "all" or bypass_keys.covers(bp, "response")):
                    self._bypass_audit(payload, "response 侧跳过（scope=%s）" % bp["scope"])
                    self._json(200, {"action": {"reason": "pass"}})
                    return
                resp_layers = [l for l in (bp.get("layers") or []) if l in ("l1", "l2")] if bp else []
                if resp_layers:
                    settings = self._bypass_overlay(settings, bp)
                    self._bypass_audit(payload, "response 侧按层跳过：layers=%s" % ",".join(resp_layers))
                if not setting_value(settings, "response", "enabled", "RESPONSE_ENABLED", True):
                    self._json(200, {"action": {"reason": "pass"}})
                    return
                _, entities = mask_response_body(
                    resp_body,
                    setting_value(settings, "l1", "enabled", "L1_ENABLED", True),
                    setting_value(settings, "l2", "enabled", "L2_ENABLED", True),
                )
                if entities:
                    # issue #134：响应侧 451 同槽落 block 条（此前只请求侧落——响应侧拦截曾无留痕）；
                    # 响应侧无摘录（mask_response_body 只回规则族，values withheld 纪律不变）
                    shadow_log.record("block", hit=True, blocked=True, rule_ids=entities,
                                      side="response", model=self._req_model(payload),
                                      key_hash=key_hash_from_headers(self.headers))
                    print(f"[dlp.block] 451 side=response rules={','.join(entities)}", flush=True)
                    err = json.dumps(
                        {"error": {"message": "Blocked by ai4s DLP: response contained sensitive content",
                                   "type": "content_policy_violation",
                                   "code": "response." + entities[0]}},
                        ensure_ascii=False,
                    )
                    self._json(200, {"action": {"body": err, "status_code": 451,
                                                "reason": f"response sensitive hit: {', '.join(entities)} (values withheld)"}})
                else:
                    self._json(200, {"action": {"reason": "pass"}})
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})
            return
        if self.path != "/request":
            self._json(404, {})
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
            payload = json.loads(self.rfile.read(length) or b"{}")
            messages = (payload.get("body") or {}).get("messages") or []
            text = extract_text(messages)
            terms = load_terms()
            # 统一配置（issue #35）：settings.json > env > 内置默认，每请求重读热生效；本段一次读多键用
            settings = load_settings()
            _layer_switch_observe(settings)  # 开关状态变化 warn（issue #40 review）
            # Key 绕行（issue #129）：scope=all → shim 侧全层跳过直接放行；scope=layers →
            # 覆盖层 enabled 强制 False 的 settings 副本继续走链路（下游门控统一经
            # setting_value 读 settings：l1/l2/edm/rules/pg/judge 全覆盖）。两路都落审计条；
            # layers 只含 response 时本侧无可跳层，照常检测且不落「按层跳过」条。
            bp = self._bypass_entry()
            if bp and bp["scope"] == "all":
                self._bypass_audit(payload, "request 侧全层跳过（scope=all）")
                self._json(200, {"action": {"reason": "pass"}})
                return
            req_layers = [l for l in (bp.get("layers") or []) if l != "response"] if bp else []
            if req_layers:
                settings = self._bypass_overlay(settings, bp)
                self._bypass_audit(payload, "request 侧按层跳过：layers=%s" % ",".join(req_layers))
            # 分层总开关（issue #40，默认 True 保现网行为）：l1 关 → 格式规则全族（secrets reject +
            # 归一化 PII mask）整层跳过；l2 关 → 词表/Presidio PII 阶段整体跳过
            l1_on = setting_value(settings, "l1", "enabled", "L1_ENABLED", True)
            l2_on = setting_value(settings, "l2", "enabled", "L2_ENABLED", True)
            # 归一化预检（issue #22）：全角/繁简/分隔归一后查 secrets + 词表
            norm, _ = normalize_hard(text)
            pre_rules = []
            _term_hits = []  # issue #134：451 落条摘录取词表命中值（管理员自配词表可原样展示）
            _secret_codes = []
            if text:
                if l1_on:
                    _secret_codes = norm_secret_hits(norm)
                    pre_rules += _secret_codes
                if l2_on:
                    _term_hits = norm_term_hits(norm.lower(), terms)
                    pre_rules += [t["rule_id"] for t in _term_hits]
            # EDM 文档指纹（issue #29，L3）：整段粘贴商密文档 → 命中阈值即拦
            edm_enabled = setting_value(settings, "edm", "enabled", "EDM_ENABLED", False)
            edm_min_hits = setting_value(settings, "edm", "min_hits", "EDM_MIN_HITS", 2)
            if edm_enabled and not pre_rules and edm_hit_count(text, load_edm_fps()) >= edm_min_hits:
                pre_rules = ["edm.doc_match"]
            hits = [] if (pre_rules or not l2_on) else (analyze(text, terms) if text else [])
        except Exception as e:
            # shim 自身异常 → 500，由 agentgateway failureMode=failOpen 放行（契约分级）
            self._json(500, {"error": str(e)[:200]})
            return
        if pre_rules or hits:
            rule_ids = sorted(set(pre_rules) | {h["rule_id"] for h in hits})
            # 观测闭环（issue #130）：内容阻断条先于应答落盘——alert_poller 巡检项 5
            # 复用阻断通道消费发飞书；rule_ids 为规则族标识（confidential.*/secrets.*/
            # edm.doc_match——规则标识非敏感值）。
            # issue #134 增强：side=request + key_hash（Bearer SHA-256 指纹，不明文落盘，
            # 读侧哈希比对反查 key 名/用户）+ excerpts（词表命中原样、secrets 掩码，
            # 绝无完整原文上下文）。record 永不抛（shadow_log 纪律）。
            shadow_log.record("block", hit=True, blocked=True, rule_ids=rule_ids,
                              model=self._req_model(payload), side="request",
                              key_hash=key_hash_from_headers(self.headers),
                              excerpts=block_excerpts(norm, _term_hits, hits,
                                                      [c for c in rule_ids if c.startswith("secrets.")]) or None)
            print(f"[dlp.block] 451 rules={','.join(rule_ids)}", flush=True)
            body = json.dumps(
                {
                    "error": {
                        "message": f"Blocked by ai4s DLP: confidential term detected ({', '.join(rule_ids)})",
                        "type": "content_policy_violation",
                        "code": rule_ids[0],
                    }
                },
                ensure_ascii=False,
            )
            self._json(
                200,
                {
                    "action": {
                        "body": body,
                        "status_code": 451,
                        "reason": f"confidential term hit: {', '.join(rule_ids)} (values withheld)",
                    }
                },
            )
            return
        # 注入规则层（issue #104，#100 路线② 生产落点）：inject_rules 语义模式组判定——
        # 归一化扩清除表 + 迭代 base64 解码探针 + 模式组命中（µs 级全文扫描，无 4000 截断，
        # 实测 p50≈24µs 同步无感，不占异步执行器）。位置在词表/EDM 451 之后、PG 阻断段之前：
        # 规则命中是确定性布尔（便宜），先于 PG 模型推理（p95≈136ms）拦下可省一次同步推理。
        # 开关语义：rules.enabled=false（默认）→ 零开销零落条（新层先进场 shadow 观察）；
        # enabled=true → 每请求落 rules 层判定条（hit=True 带 groups 命中模式组名——脱敏字段；
        # hit=False 不带 groups，jsonl 体积纪律）；rules.block 开 + 命中 → 451（应答形状对齐
        # #103 PG 阻断：code=rules.injection、body/reason 不含原文）+ blocked=True 落条先于
        # 应答（告警由 alert_poller 巡检项 5 复用 pg 阻断通道消费，shim 不新增同步外发）。
        # fail-open：规则层自身异常必须放行不阻断——捕获后放行 + error 落条（与 PG 段同语义）。
        # 空 text / 词表已 451 的请求不进本段（上方已 return，与 PG 段既有口径一致）。
        if text and setting_value(settings, "rules", "enabled", "RULES_ENABLED", False):
            _rules_t0 = time.monotonic()
            try:
                _r_hit, _r_groups, _r_depth = inject_rules.rule_match(text)
                _r_err = None
            except Exception as e:
                _r_hit, _r_groups, _r_err = False, [], type(e).__name__
                print(f"[injection.rules] fail-open: {_r_err}", flush=True)
            _r_ms = int((time.monotonic() - _rules_t0) * 1000)  # 口径同 PG 段：纯判定耗时
            if _r_err is not None:
                shadow_log.record("rules", error="unavailable", latency_ms=_r_ms)
            elif _r_hit and setting_value(settings, "rules", "block", "RULES_BLOCK", False):
                # 阻断条先于应答落盘（告警巡检消费即见）；record 永不抛（shadow_log 纪律）
                # issue #131：key_hash（Bearer SHA-256 指纹）随阻断条落盘——告警卡身份反查来源
                shadow_log.record("rules", hit=True, groups=_r_groups, latency_ms=_r_ms,
                                  blocked=True, model=self._req_model(payload),
                                  key_hash=key_hash_from_headers(self.headers))
                print(f"[injection.rules] 451 groups={','.join(_r_groups)}", flush=True)
                body = json.dumps(
                    {
                        "error": {
                            "message": "Blocked by ai4s DLP: prompt injection detected (rules.injection)",
                            "type": "content_policy_violation",
                            "code": "rules.injection",
                        }
                    },
                    ensure_ascii=False,
                )
                self._json(
                    200,
                    {
                        "action": {
                            "body": body,
                            "status_code": 451,
                            "reason": f"prompt injection blocked: rules hit {', '.join(_r_groups)} (values withheld)",
                        }
                    },
                )
                return
            else:
                shadow_log.record("rules", hit=_r_hit, groups=_r_groups or None, latency_ms=_r_ms)
                if _r_hit:
                    print(f"[injection.rules] hit groups={','.join(_r_groups)}", flush=True)
        # PG 高分档阻断试点（issue #103）：pg.block_enabled 开 → 应答前同步跑一次 pg_guard。
        # 明示代价：PG 本地推理（实测 p50≈50ms/p95≈136ms）回到请求路径——试点期只拦高分档
        # （block_threshold 默认 0.9，v3 水位 14/46 检出、四档零误报），延迟换注入拦阻能力；
        # block_enabled 关（默认）= 现状纯异步零变化（下方 shadow 段仍走 pg_guard_async）。
        # 判定结果三分支：≥ 阻断阈值 → 451（应答体对齐词表/EDM 451 形状，不含原文）+
        #   blocked 条落 shadow_log（脱敏：score/阈值/模型名，飞书告警由 alert_poller 巡检消费，
        #   shim 不新增同步外发）；< 阻断阈值 → 放行，本次 score 留给应答后 shadow 落条复用
        #   （只跑一次推理）；None（fail-open）→ 放行 + error 落条（与异步版同语义）。
        # 词表/EDM 已 451 的请求不进本段（上方已 return，与 shadow 段既有口径一致）；
        # 空 text / pg.enabled=false 整体跳过（总开关优先，不打分不落条）。
        pg_checked = False  # 本请求是否已同步判定（score 见 pg_score，None=fail-open）
        pg_score = None
        pg_ms = None
        if (setting_value(settings, "pg", "block_enabled", "PG_BLOCK_ENABLED", False)
                and text and setting_value(settings, "pg", "enabled", "PG_ENABLED", False)):
            pg_block_threshold = setting_value(settings, "pg", "block_threshold", "PG_BLOCK_THRESHOLD", 0.9)
            _pg_t0 = time.monotonic()
            pg_score = pg_guard(text)
            pg_ms = int((time.monotonic() - _pg_t0) * 1000)  # 口径与异步版一致：纯判定耗时
            pg_checked = True
            if pg_score is None:
                shadow_log.record("pg", error="unavailable", latency_ms=pg_ms)
            elif pg_score >= pg_block_threshold:
                # 阻断条先于应答落盘（告警巡检消费即见）；record 永不抛（shadow_log 纪律）
                # issue #131：key_hash（Bearer SHA-256 指纹）随阻断条落盘——告警卡身份反查来源
                shadow_log.record("pg", hit=True, score=pg_score, latency_ms=pg_ms,
                                  blocked=True, block_threshold=pg_block_threshold,
                                  model=self._req_model(payload),
                                  key_hash=key_hash_from_headers(self.headers))
                print(f"[injection.block] 451 score={pg_score:.3f} >= {pg_block_threshold}", flush=True)
                body = json.dumps(
                    {
                        "error": {
                            "message": "Blocked by ai4s DLP: prompt injection detected (pg.injection)",
                            "type": "content_policy_violation",
                            "code": "pg.injection",
                        }
                    },
                    ensure_ascii=False,
                )
                self._json(
                    200,
                    {
                        "action": {
                            "body": body,
                            "status_code": 451,
                            "reason": f"prompt injection blocked: score>={pg_block_threshold} (values withheld)",
                        }
                    },
                )
                return
        # PII 脱敏（issue #15）：命中不阻断，返回改写后的消息体（MaskAction）
        # issue #22：先走归一化 mask（分隔/全角变形）；无命中再走 Presidio context 流程
        # issue #40：归一化 mask 属 l1（格式规则全族），Presidio PII 属 l2，各自随总开关跳过
        try:
            masked_msgs, any_masked, entities = mask_pipeline(messages, l1_on, l2_on)
        except Exception as e:
            self._json(500, {"error": str(e)[:200]})
            return
        if any_masked:
            self._json(
                200,
                {
                    "action": {
                        "body": {"messages": masked_msgs},
                        "reason": f"PII masked: {', '.join(entities)}",
                    }
                },
            )
        else:
            self._json(200, {"action": {"reason": "pass"}})
        # 语义层 shadow（issue #21）：响应已定后发 judge，只记录 verdict（不含原文），不影响本请求
        # issue #92：判定持久化 shadow_log（实体只存命中数，不存字符串）——观测闭环供巡检/统计消费；
        # enabled 但无 verdict（prompt 缺失/API 异常等）记 error 条，可用率巡检才有数据；
        # 空 text（纯图片/工具调用请求无可判输入）整体跳过不落条——否则误计「层不可用」污染异常率
        # issue #93：judge 输入改用掩码后文本（masked_msgs 提取，未命中时==messages 语义不变）——
        # 涉密候选原文不外发；采样率/并发预算只作用于本链路（/judge-test 直测不受限），
        # 未中采样/预算占满 = 跳过判定，不落 shadow_log 条（skip 非层异常，不污染 error_rate）
        # issue #101 action 四档消费（judge.action 三级取值，默认 shadow）：
        #   off=链路不送判定（即使 enabled=true 也跳过不落条——档位语义 ≡ enabled=false，
        #       只管 /request 链路消费；/judge-test 直测不受影响，留显式人肉调试通道）；
        #   shadow=现状仅记录（绝不带 warned 键）；
        #   warn=shadow 全部 + confidential 且 confidence ≥ judge.threshold 时落 warned=True 条
        #       （带请求模型名 model 脱敏字段，alert_poller 巡检项 6 消费发飞书；
        #       告警不拦截——契约「语义层永不阻断」，响应早已定稿发回）；
        #   reject=schema 存在（#94）但契约不支持消费：按 shadow 同等处理 + 状态变化 print
        #       提示（_judge_action_observe），绝不阻断。契约门槛（换内网模型+观察期误报
        #       可控+回归达标三者齐）满足前 reject 档不实现阻断语义，头注记账。
        # issue #105 注入判定第二职责（#100 路线③生产落点，judge_inject 层 shadow 观测）：
        #   inject_enabled=true 时在同一门槛内追加第二次调用（judge_inject_text，同一份脱敏
        #   输入 judge_input）——采样率/并发预算与商密判定**共享一次门槛**（judge API 总预算
        #   语义：未中采样/预算占满/action=off/enabled=false → 两个判定都跳过，不独立采样，
        #   否则 sample_rate 总预算语义翻倍失真）；一次预算占位覆盖两次串行调用。
        #   成本口径：注入判定使每请求 judge API 调用 ×2——#100 实测 ≈$0.00021/次
        #   （gpt-5.6-luna 刊例），sample_rate=1.0 双职责全开时 ≈$0.00042/请求，头注记账。
        #   契约纪律：注入判定**永不阻断、永不落 warned 条、永不发卡**（#101 warn 消费是商密
        #   判定专属）——观测价值只在 shadow 水位统计（judge_inject 层 hits/error_rate/
        #   attack_type 分布）；future 要告警走规则层/PG 既有通道，不在本层加消费。
        judge_input = judge_pre_decode(extract_text(masked_msgs))  # 前置单趟解码（issue #107）：商密/注入两职责同受益
        judge_action = setting_value(settings, "judge", "action", "JUDGE_ACTION", "shadow")
        _judge_action_observe(judge_action)  # reject 档提示（issue #101，状态变化才打）
        if judge_input and judge_action != "off" and setting_value(settings, "judge", "enabled", "JUDGE_ENABLED", False):
            if random.random() >= setting_value(settings, "judge", "sample_rate", "JUDGE_SAMPLE_RATE", 1.0):
                print("[semantic.shadow] skipped (sampling)", flush=True)
            elif not judge_budget_try_enter(setting_value(settings, "judge", "max_concurrency", "JUDGE_MAX_CONCURRENCY", 2)):
                print("[semantic.shadow] skipped (concurrency budget)", flush=True)
            else:
                try:
                    _t0 = time.monotonic()
                    v = judge_text(judge_input)
                    _ms = int((time.monotonic() - _t0) * 1000)
                    if v is not None:
                        # warn 档超阈值 → warned 事件条（对齐 #103 blocked 模式：键只在告警条写入，
                        # 普通判定条形状逐字节不变；model=请求模型名脱敏字段，无原文无 key）
                        judge_threshold = setting_value(settings, "judge", "threshold", "JUDGE_THRESHOLD", 0.8)
                        warned = (judge_action == "warn" and v["confidential"]
                                  and v["confidence"] >= judge_threshold)
                        shadow_log.record("judge", hit=v["confidential"], confidence=v["confidence"],
                                          latency_ms=_ms, entities=len(v["entities"]),
                                          warned=True if warned else None,
                                          model=(self._req_model(payload) if warned else None))
                        print(f"[semantic.shadow] confidential={v['confidential']} entities={','.join(v['entities']) or '-'} confidence={v['confidence']:.2f}", flush=True)
                        if warned:
                            print(f"[semantic.warn] confidence={v['confidence']:.2f} >= {judge_threshold}（告警巡检消费，未拦截）", flush=True)
                    else:
                        shadow_log.record("judge", error="unavailable", latency_ms=_ms)
                    # 注入第二职责（issue #105）：商密判定完成后同门槛内追加；异常/未启用返回
                    # None → error 条（fail-open，绝不影响上面的商密结果与本请求响应——
                    # 响应早已定稿发回）。落独立层 judge_inject 与商密分层统计。
                    if setting_value(settings, "judge", "inject_enabled", "JUDGE_INJECT_ENABLED", False):
                        _t1 = time.monotonic()
                        iv = judge_inject_text(judge_input)
                        _ims = int((time.monotonic() - _t1) * 1000)
                        if iv is not None:
                            shadow_log.record("judge_inject", hit=iv["injection"],
                                              confidence=iv["confidence"], latency_ms=_ims,
                                              attack_type=iv["attack_type"] or None)
                            print(f"[injection.judge.shadow] injection={iv['injection']} attack_type={iv['attack_type'] or '-'} confidence={iv['confidence']:.2f}", flush=True)
                        else:
                            shadow_log.record("judge_inject", error="unavailable", latency_ms=_ims)
                finally:
                    judge_budget_exit()
        # 注入检测 shadow（issue #30）：PromptGuard 2 评分 ≥阈值记日志，不阻断
        # issue #92：同上持久化；调用点显式判 enabled——enabled 且 score 为 None 即本次判定不可用；
        # 空 text 跳过不落条（同 judge 侧，code-review 修复）
        # issue #97：判定段（score→落条/print，形状语义不变）整体提交有界异步执行器 pg_guard_async——
        # 推理不再占 handler 线程预算（p50≈50ms/p95≈136ms），响应发完即释放；
        # 积压满丢弃=跳过判定，不落 error 条（同 #93 skip 语义）
        # issue #103：阻断试点同步判定过（pg_checked）→ 直接复用本次 score 落条
        # （形状语义与异步版逐字段一致，只跑一次推理）；score None 的 error 条已在阻断段落；
        # 未同步判定（阻断关/未启用/空 text 之外）→ 现状纯异步路径零变化
        pg_threshold = setting_value(settings, "pg", "threshold", "PG_THRESHOLD", 0.7)
        if text and setting_value(settings, "pg", "enabled", "PG_ENABLED", False):
            if pg_checked and pg_score is not None:
                shadow_log.record("pg", hit=pg_score >= pg_threshold, score=pg_score, latency_ms=pg_ms)
                if pg_score >= pg_threshold:
                    print(f"[injection.shadow] malicious={pg_score:.3f} >= {pg_threshold}", flush=True)
            elif not pg_checked:
                pg_guard_async(text, pg_threshold)

    def do_PUT(self):
        # 非 admin 路径回 404 而非 BaseHTTPRequestHandler 默认 501：有意语义——
        # 对外统一"未知写操作路径 404"，不暴露服务未实现 PUT 的内部细节。
        if admin_api.handle(self, "PUT"):  # admin 平面（issue #32）：/dlp-admin/* 写端点
            return
        if self_api.handle(self, "PUT"):  # 员工自助平面（issue #74 评审 P2）：已鉴权 PUT → 显式 404
            return
        if self.path == "/classify":
            # issue #117 按 #115 坑 1 收口：extAuthz 按原请求方法转发授权调用——
            # /classify 全方法恒 200 无头（非 2xx 会被网关当 deny 决策直回客户端，
            # failureMode 只管传输层错误）。
            self._json(200, {"resolved_model": None})
            return
        self._json(404, {})

    def do_DELETE(self):
        # 同 do_PUT：非 admin 路径有意回 404（非 501）。
        if admin_api.handle(self, "DELETE"):  # admin 平面（issue #32）
            return
        if self_api.handle(self, "DELETE"):  # 员工自助平面（issue #74 评审 P2）：已鉴权 DELETE → 显式 404
            return
        if self.path == "/classify":
            self._json(200, {"resolved_model": None})  # 同 do_PUT 注释：/classify 全方法恒 200
            return
        self._json(404, {})

    def do_OPTIONS(self):
        # issue #117：/classify 全方法恒 200 兜底（extAuthz 同方法转发授权调用，#115 坑 1）；
        # 其余路径与 do_PUT/do_DELETE 同语义回 404（非 BaseHTTPRequestHandler 默认 501）。
        self._json(200 if self.path == "/classify" else 404,
                   {"resolved_model": None} if self.path == "/classify" else {})

    def do_HEAD(self):
        # 评审 P2-3：HEAD 同收口——缺省时 BaseHTTPRequestHandler 默认 501 会被网关当 deny。
        # /classify 恒 200；HEAD 无响应体（Content-Length: 0，不走 _json 写 body）。
        if self.path == "/classify":
            self.send_response(200)
        else:
            self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


if __name__ == "__main__":
    # 启动即打一行生效来源（issue #35）：settings.json 可读 → 配置来自 JSON 覆盖层；否则全量 env/内置默认
    _s = load_settings()
    print(f"[settings] 配置来源: {'settings.json' if _s else 'env/内置默认（settings.json 缺失/损坏）'} path={SETTINGS_PATH}", flush=True)
    # 告警巡检 daemon 线程（issue #56：alert-poller 并入）：与检测路径隔离——
    # 循环体整体 try/except，单轮异常只记日志；daemon 线程随主进程退出。
    # 先实例化 server 再 start_daemon（issue #57 P2-1 启动竞态）：ThreadingHTTPServer 构造即完成
    # bind+listen，巡检线程首轮自探活 localhost:8080/healthz 不会抢在 bind 前误报 shim 不可达
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    alert_poller.start_daemon()
    server.serve_forever()
