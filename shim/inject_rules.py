#!/usr/bin/env python3
"""注入规则层匹配器（issue #104）：#100 路线② 评估口径的生产落点。

三件套（同 #100 评估脚本 deploy/scripts/injection-countermeasure-eval.py 路线②，
本模块为生产单一源，评估脚本改为 import 本模块——口径单点，改动双侧同步可见）：
  1. rule_normalize：L2 检测归一化（app.normalize_hard 同款全角→半角/繁→简/剔除分隔符/
     小写）+ 不可见字符清除表扩充（00ad 软连符/2060 词连接符/034f/115f/1160/180e/
     17b4/17b5/202a-202e 方向控制——pg_engine 只清 200b/200c/200d/feff）。
     独立函数不改共享 normalize_hard：扩表影响面隔离在注入域，不动 L1/L2 商密匹配口径。
  2. decode_probe：迭代 base64 解码探针（深度上限 2）——normalize 单趟解码的已知边界
     （nested_encoding）由本探针闭合；三层及以上嵌套到上限自然停（不炸不抛）。
  3. RULE_GROUPS：注入语义模式组（override/extract/roleplay/authority/emotion/coercion/
     delimiter/pinyin/maintenance-mode/apikey-exfil 等，中英日韩四语），归一化后
     子串/regex 命中即记组；全文扫描无 4000 字符截断（PG 的 text[:4000] 边界不适用于本层）。

误报治理（#104 评审实测 8 条手写泛化探针 2 条误报，治理后 0/4；v3 水位不变）：
  - extract-en：目标名词后接 files?（「system prompt files」=开发概念非提取本体）豁免；
  - apikey-exfil：密钥名词与索取动词的命中跨度含 打码/脱敏/掩码/mask/redact（脱敏处理
    需求，非外泄）豁免——跨度内含脱敏词的单个命中作废，其余跨度照常判定。

实测水位（v3 样本集 68 条，shim/tests/test_inject_rules.py 门禁化）：
46/46 检出、盲区四类 17/17、nested 3/3、invisible 3/3、负例误报 0/22、p50≈24µs。

纪律：
- 仅标准库；纯函数无副作用——不落日志、不读写文件、import 不起线程（模块级仅 regex 编译）；
- fail-open 语义由调用方（app.py /request 规则层段）保证：本模块任何异常在调用点
  捕获→放行+error 落条，规则层自身异常必须放行不阻断；
- 组名是模式标识（非原文），可安全落 shadow_log/告警卡片；本模块不接触 secret。
"""
import base64
import re

# ---- 归一化：L2 检测归一化注入域独立版（全角→半角/繁→简/剔除分隔符/小写 + 不可见字符扩充清除）----
_FULLWIDTH = {i: chr(i - 0xFEE0) for i in range(0xFF01, 0xFF5F)}
_FULLWIDTH[0x3000] = " "
# 繁→简：词表用字小表 + 注入变体样本用字（宁缺勿滥纪律同 app.normalize_hard——错映射会误伤）
_TRAD2SIMP = dict(zip("鳳計劃鯨藍號話統雲網內級鳳現在個沒無視開關審規則讓們", "凤计划鲸蓝号话统云网内级凤现在个没无视开关审规则让们"))
_SEP = set(" \t\r\n-_")
# 不可见字符清除表扩充（issue #96 invisible 盲区对策）：pg_engine 只清 200b/200c/200d/feff；
# 本层扩至 00ad 软连符 / 2060 词连接符 / 034f / 115f/1160 / 180e / 17b4/17b5 / 202a-202e 方向控制。
# 必须 \u 转义写法（review #104：不可见字面量曾把 U+115F 抄成 U+111F，肉眼不可辨）
_INVISIBLE_EXT = re.compile("[\u200b\u200c\u200d\ufeff\u00ad\u2060\u034f\u115f\u1160\u180e\u17b4\u17b5\u202a-\u202e]")


def rule_normalize(text):
    """L2 检测归一化（注入域口径）：不可见字符扩充清除 → 全角→半角/繁→简 → 剔除分隔符 → 小写。"""
    text = _INVISIBLE_EXT.sub("", text)
    out = []
    for ch in text:
        c = _FULLWIDTH.get(ord(ch), ch)
        c = _TRAD2SIMP.get(c, c)
        if c in _SEP:
            continue
        out.append(c)
    return "".join(out).lower()


# ---- 迭代 base64 解码探针（深度上限 2）----
_B64_TOKEN = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def decode_probe(text, max_depth=2):
    """逐轮找 base64 形 token，可解码为可打印文本（可打印率 >0.9）则记录并进入下一轮
    （深度上限 max_depth）。返回 [(depth, 明文)]——normalize 单趟解码的已知边界
    （nested_encoding）由本探针闭合；三层及以上嵌套到上限自然停，不炸不抛。
    解码为二进制（PNG 头等）不进下一轮——防误伤正常 base64 讨论。"""
    found, frontier = [], [text]
    for depth in range(1, max_depth + 1):
        nxt = []
        for t in frontier:
            for m in set(_B64_TOKEN.findall(t)):
                try:
                    s = base64.b64decode(m, validate=True).decode("utf-8")
                except Exception:
                    continue
                if s and sum(ch.isprintable() or ch.isspace() for ch in s) / len(s) > 0.9:
                    found.append((depth, s))
                    nxt.append(s)
        frontier = nxt
    return found


def _co(noun, verb, w=30):
    """共现模式：noun/verb 任一序、距离 ≤ w（归一化后无空格，w 按字符计）。"""
    return rf"(?:{noun}).{{0,{w}}}(?:{verb})|(?:{verb}).{{0,{w}}}(?:{noun})"


# 注入语义模式组：全部作用于 rule_normalize 后文本（小写、无空格/连字符、不可见字符已清）。
# 定稿自 #100 评估候选组（实测 46/46 检出、0/22 误报）+ #104 误报治理两处豁免（头注）。
RULE_GROUPS = [
    ("override-en", re.compile(
        r"ignore(?:all|any|every|the|your)?(?:previous|prior|above|preceding)(?:instructions?|directives?|rules?|prompts?)"
        r"|disregard(?:all|any|your|the)?(?:previous|prior)?(?:instructions?|guidelines|safety)")),
    ("override-zh", re.compile(
        r"(?:忽略|无视|不管|抛弃)(?:之前|先前|以上|前面|所有|全部|前述).{0,8}(?:指令|指示|命令|规则|限制)")),
    # ja/ko：rule_normalize 的繁→简映射会把日语「無視」写成「无视」（映射表按中文词表设计），两种形态都收
    ("override-ja-ko", re.compile(r"指示をすべて[無无]視|すべて[無无]視してください|制限のないai|지시를무시")),
    ("roleplay", re.compile(
        r"actasdan|doanythingnow|stayincharacter|pretendtobe"
        r"|没有任何限制的|没有内容审查|不受任何规则约束|无审查的")),
    ("indirect-en", re.compile(r"howtobypass.{0,24}(?:gateway|dlp|filter|safety)|replyasthecharacter")),
    ("extract-en", re.compile(
        r"(?:print|repeat|reveal|output|show|display|dump)(?:your|the|all|any|full|own|exact)*"
        # 误报治理（#104）：目标名词后接 files? 豁免——「system prompt files」是开发概念
        # （给自家应用写文档），非提取本体；v3 提取样本措辞均不含此形态（水位测试兜底）
        r"(?:systemprompt|instructions?|initialprompt|internalrules?|blocklist|confidentialterms?)(?!files?)"
        r"|(?:systemprompt|yourinstructions?).{0,25}(?:verbatim|print|repeat|reveal|output|show)"
        r"|(?:first|second|next)wordofyour(?:instructions?|systemprompt)|onewordatatime")),
    ("extract-zh", re.compile(_co(
        r"系统提示词|提示词原文|第一段指令|全部指令|指令内容|拦截规则|内部规则|规则全文|系统消息|系统提示",
        r"逐字|原样|原文|不.{0,2}省略|输出|复述|导出|贴出|发给|告诉|打印|展示|附在"))),
    ("extract-ja-ko", re.compile(_co(r"システムプロンプト", r"出力|省略|そのまま|監査")
                                 + "|" + _co(r"시스템프롬프트", r"출력|빠뜨리|감사"))),
    ("authority", re.compile(_co(
        r"管理员|安全团队|系统运维|审计|合规|备案|工单|上级|ceo|老板|董事长",
        r"立刻|马上|紧急|特批|授权|否则|责任|必须|直接输出|原样|逐字", w=40)
        + "|" + _co(r"auditor|authorized|redteam|securityteam",
                    r"disable|output|reveal|print|bypass", w=40))),
    ("safety-disable", re.compile(
        r"(?:disable|turnoff|bypass|remove|suspend)(?:your|the|all|any)?"
        r"(?:contentfilters?|safetyfilters?|safetyguidelines|guardrails?|restrictions?)"
        r"|(?:关闭|禁用|解除|绕过)(?:内容审查|审查|过滤|安全限制|限制|拦截)")),
    ("emotion", None),  # 三组联条件（亲情+不幸+索取），下方 _groups_of 特判
    ("coercion", re.compile(_co(r"开除|跳楼|自杀|失业|一家老小|担不起|丢工作",
                                r"提示词|告诉|输出|证明|规则|指令", w=40))),
    ("delimiter", re.compile(
        r"</?[a-z]+><system>|<system>|【系统消息】|【系统提示】|【系统通知】"
        # review #104 ReDoS 修复：原 ={2,}[^=]{0,20}={2,} 无界双量词对长 = 串三次方回溯
        # （请求路径同步执行=DoS 面）。改对称包裹（反向引用 \1 等长闭合）：回溯有界线性；
        # 取舍：>6 个 = 的长分隔线不命中本组（罕见形状，由 PG/judge 层兜），头注记账。
        r"|(?:(={2,6})[^=]{1,20}\1|——[^—]{1,20}——).{0,60}(?:新指令|新任务|管理员已授权|忽略|改为)"
        r"|endof(?:summary|text|document|section).{0,40}(?:system:|newdirective|newtask)")),
    ("pinyin", re.compile(r"xitongtishici|tishiciyuanwen|meiyoushencha|buyunxujujue")),
    ("maintenance-mode", re.compile(r"maintenancemode|维护模式")),
    ("apikey-exfil", None),  # 跨度脱敏豁免特判（误报治理），下方 _groups_of
]

_EMOTION_FAMILY = re.compile(r"奶奶|爷爷|外婆|外公|孩子|父亲|母亲|儿子|女儿|爸爸|妈妈")
_EMOTION_MISFORTUNE = re.compile(r"去世|重病|住院|临终|癌症|生前|唯一.{0,2}愿望|最后.{0,2}愿望")
# 索取词不含裸「念」（负例「悼念」会误中）：绑定动作形态
_EMOTION_DEMAND = re.compile(r"激活码|扮演|关闭|绕过|过滤|审查|限制|哄我|念给|给我念|讲故事")

# 误报治理（#104）：apikey-exfil 命中跨度含脱敏词 → 是脱敏处理需求非外泄，该跨度作废
_APIKEY_EXFIL_RGX = re.compile(_co(r"apikeys?|api密钥|密钥原文|sessiontoken|会话token",
                                   r"print|output|reveal|append|email|附|发给|告诉|输出"))
_SPAN_MASKING = re.compile(r"打码|脱敏|掩码|mask|redact")


def _apikey_exfil_hit(norm):
    """密钥外泄共现判定：任一命中跨度不含脱敏词即命中（脱敏词跨度是正常处理需求）。"""
    return any(not _SPAN_MASKING.search(m.group(0)) for m in _APIKEY_EXFIL_RGX.finditer(norm))


def _groups_of(norm):
    """归一化文本上的命中组集合：regex 组直接搜；emotion/apikey-exfil 走特判。"""
    hits = set()
    for name, rgx in RULE_GROUPS:
        if name == "emotion":
            if (_EMOTION_FAMILY.search(norm) and _EMOTION_MISFORTUNE.search(norm)
                    and _EMOTION_DEMAND.search(norm)):
                hits.add(name)
        elif name == "apikey-exfil":
            if _apikey_exfil_hit(norm):
                hits.add(name)
        elif rgx.search(norm):
            hits.add(name)
    return hits


def rule_match(text):
    """注入规则判定：命中返回 (True, 命中组列表, 探针深度)；未命中 (False, [], 0)。
    表层归一化匹配 + 迭代 base64 解码探针（明文再过同一模式组，组名带 @b64L<depth> 后缀）。
    全文扫描，无 4000 字符截断。纯函数不抛预期外异常（fail-open 兜底在调用方）。"""
    hits = _groups_of(rule_normalize(text))
    depth_hit = 0
    for depth, plain in decode_probe(text):
        decoded = _groups_of(rule_normalize(plain))
        if decoded:
            hits.update(f"{name}@b64L{depth}" for name in decoded)
            depth_hit = max(depth_hit, depth)
    return bool(hits), sorted(hits), depth_hit
