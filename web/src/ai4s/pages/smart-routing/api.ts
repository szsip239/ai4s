/**
 * 智能路由管理页数据层（issue #120）：auto 智能路由（shim #117 schema / #119 扩展键）的配置与观测。
 * 配置读写复用 /dlp-admin/settings 通路（与 rules 页同源同鉴权：apiRequest requireAuth，
 * 页面与 API 同源经网关）：GET（rules/api useSettings）→ 只改 routing 节 → 整体 PUT
 * （buildSettingsWithRouting；shim 全量严校，其他段靠 normalize 补齐——selectSettings 先例）。
 * routing 为可选节：缺席=关态合法，读侧经 normalizeRouting 补默认（缺省值与 shim app.py
 * ROUTER_* 常量/setting_value 缺省逐点对齐）；扩展键（#119）normalize 后总是出席，
 * 保存即显式落盘（shim 出席才校验，值合法则 200）。
 * 决策日志：GET /dlp-admin/shadow-verdicts?layer=router&n=50（shim #92 出口、#117 router 层
 * 决策条带 resolved_model/tier/p_complex/reason/session）——该出口的首个前端消费，手动刷新不轮询。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { apiRequest } from '@/lib/api-client';
import type { DlpSettings, RoutingSettings } from '../rules/api';

/** 分类系统提示缺省值：与 shim/app.py ROUTER_PROMPT_SYSTEM 逐字一致（#114 pcomplex 评测获胜版，
 * 评测口径 227 样本 98.2% @thr0.5；#119 起 routing.prompt 缺省=该常量。api.test.mjs 有逐字对账测试，
 * 改动须两侧同步） */
export const ROUTER_DEFAULT_PROMPT = `你是 LLM 网关的路由分类器。把用户请求分为两档：
- simple：单步、有确定答案、无需长链推理或跨上下文综合——事实问答、短翻译、一句话润色、单行代码修改、简单命令、单工具直取。
- complex：多步推理、设计/权衡、长上下文代码任务、跨文件依赖、专业领域翻译、实验/建模设计、多轮工具编排与错误恢复。
注意：输入文本长短不代表难度；带 [SYSTEM]/[USER]/[TOOL] 标记的是 coding agent 多轮会话形态，按最后待完成的任务定档。
只输出 JSON：{"p_complex": 0 到 1 的小数}，表示「该请求需要旗舰模型（complex 档）」的概率。`;

/** routing 节读侧缺段/缺键补默认（issue #120，先例见 normalizePg/normalizeInjectRules）：
 * routing 为可选节（shim #117：缺席=关态合法，运行侧 routing.enabled 缺省 false）；
 * #119 扩展键节内出席才校验，normalize 补默认后总是出席（值=运行侧内置默认，落盘行为不变）。
 * 不补则面板整体 PUT 时出席节缺字段被服务端 400。 */
export function normalizeRouting(routing: Partial<RoutingSettings> | undefined): RoutingSettings {
  return {
    enabled: routing?.enabled ?? false,
    threshold: routing?.threshold ?? 0.5,
    tiers: {
      simple: routing?.tiers?.simple ?? 'deepseek-v4-flash',
      complex: routing?.tiers?.complex ?? 'gpt-5.6-luna',
    },
    timeout: routing?.timeout ?? 4,
    max_concurrency: routing?.max_concurrency ?? 2,
    prompt: routing?.prompt ?? ROUTER_DEFAULT_PROMPT,
    escalate_conf: routing?.escalate_conf ?? 0.85,
    session_ttl: routing?.session_ttl ?? 3600,
    tool_loop_lock: routing?.tool_loop_lock ?? true,
    thinking_lock: routing?.thinking_lock ?? true,
  };
}

/** 保存载荷组装（issue #120）：整体 PUT 只改 routing 节；l1/l2/response 旧文件可能缺段
 * （SettingsPanel 草稿基线 ?? {enabled:true} 同处置——shim 缺段默认 true），
 * 不补则服务端必填校验 400；其余段原样透传（rules 页 selectSettings 已 normalize） */
export function buildSettingsWithRouting(doc: DlpSettings, routing: RoutingSettings): DlpSettings {
  return {
    ...doc,
    l1: doc.l1 ?? { enabled: true },
    l2: doc.l2 ?? { enabled: true },
    response: doc.response ?? { enabled: true },
    routing,
  };
}

// ---- 决策日志类型（对齐 shim shadow_log router 层记录形状：五决策字段非 None 才写，读侧可选）----

export interface RouterVerdict {
  ts: number; // epoch 秒
  layer: string;
  model?: string; // 请求原值（auto）
  resolved_model?: string; // 改写目标
  tier?: string; // simple / complex
  p_complex?: number; // 分类器校准分数（0~1）
  reason?: string; // classify/session_inherit/escalate/tool_loop_lock/thinking_lock/fail_open
  session?: boolean; // 会话命中位
  latency_ms?: number | null;
  error?: string | null;
}

export interface RouterVerdictsResp {
  stats: Record<string, unknown>; // 各层聚合（本页只展示 records，stats 留给后续水位面板）
  records: RouterVerdict[]; // 新到旧
}

// ---- query keys（settings 与 rules/api.ts QK.settings 同键：保存后两页缓存同刷新）----
const QK = {
  settings: ['dlp-admin', 'settings'],
  routerVerdicts: ['dlp-admin', 'shadow-verdicts', 'router'],
  bypassVerdicts: ['dlp-admin', 'shadow-verdicts', 'bypass'],
  blockVerdicts: ['dlp-admin', 'shadow-verdicts', 'block'],
} as const;

const BASE = '/dlp-admin';

// ---- 写操作（整体 PUT 语义与 rules/api usePutSettings 一致：写后 invalidate 重取，热生效）----

export function usePutRoutingSettings() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (doc: DlpSettings) =>
      apiRequest<DlpSettings>(`${BASE}/settings`, { method: 'PUT', body: doc, requireAuth: true }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: QK.settings });
      toast.success(t('ai4s.smartRouting.saveOk'));
    },
    onError: (e) =>
      toast.error(t('ai4s.smartRouting.saveFail'), {
        description: e instanceof Error ? e.message : String(e),
      }),
  });
}

// ---- 决策日志查询（手动刷新：不轮询，refetch 由页面刷新按钮触发）----

export function useRouterVerdicts() {
  return useQuery({
    queryKey: QK.routerVerdicts,
    queryFn: () =>
      apiRequest<RouterVerdictsResp>(`${BASE}/shadow-verdicts?layer=router&n=50`, { requireAuth: true }),
  });
}

// ---- Key 绕行审计（issue #129，shim shadow_log bypass 层：model/reason 非 None 才写，不落原文不记 token）----

export interface BypassVerdict {
  ts: number; // epoch 秒
  layer: string;
  model?: string; // 请求模型名（/bv1 入口读顶层 body.model，/v1 读 x-model 头）
  reason?: string; // 绕行说明（入口/scope/跳过层）
}

export interface BypassVerdictsResp {
  stats: Record<string, unknown>;
  records: BypassVerdict[]; // 新到旧
}

export function useBypassVerdicts() {
  return useQuery({
    queryKey: QK.bypassVerdicts,
    queryFn: () =>
      apiRequest<BypassVerdictsResp>(`${BASE}/shadow-verdicts?layer=bypass&n=50`, { requireAuth: true }),
  });
}

// ---- 内容阻断审计（issue #130，shim shadow_log block 层：rule_ids 命中规则族标识/model，不落原文；
// issue #134 增强：side 侧别 + key_hash 读侧回填 key_name/user_email + excerpts 命中摘录（词表原样/secrets 掩码））----

export interface BlockVerdict {
  ts: number; // epoch 秒
  layer: string;
  model?: string; // 请求模型名（x-model 头，issue #116）
  rule_ids?: string[]; // 命中规则族标识（confidential.*/secrets.*/edm.doc_match——非原文）
  side?: 'request' | 'response'; // issue #134：请求侧/响应侧阻断
  key_name?: string; // issue #134：读侧按 key_hash 回填的 key 名（对不上不标注）
  user_email?: string; // issue #134：读侧回填的 key 属主邮箱
  excerpts?: Array<{ rule: string; text: string }>; // issue #134：命中摘录（词表原样/secrets 掩码）
}

export interface BlockVerdictsResp {
  stats: Record<string, unknown>;
  records: BlockVerdict[]; // 新到旧
}

export function useBlockVerdicts() {
  return useQuery({
    queryKey: QK.blockVerdicts,
    queryFn: () =>
      apiRequest<BlockVerdictsResp>(`${BASE}/shadow-verdicts?layer=block&n=50`, { requireAuth: true }),
  });
}
