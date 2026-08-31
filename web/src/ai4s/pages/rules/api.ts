/**
 * DLP 统一配置 admin API 数据层（issue #36）：对接 shim /dlp-admin/*（契约 docs/contracts/dlp-webhook-shim.md）。
 * 鉴权：Bearer 取 localStorage axonhub_access_token（apiRequest requireAuth）；页面与 API 同源（都经网关 :3000）。
 * React Query 惯用法：写操作成功后 invalidate 重取；错误形状 {"error": "原因"} 由 apiRequest 提取进 Error.message。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { apiRequest } from '@/lib/api-client';
import { getTokenFromStorage } from '@/stores/authStore';

/**
 * 本层统一错误：携带 HTTP status（如 settings 缺失 404=env 兜底态），供调用方按状态码判定而非文案。
 * 上游 apiRequest 抛 ApiError（带 status 但类未导出），此处归一为本层类型。
 */
export class DlpApiError extends Error {
  constructor(
    message: string,
    public readonly status: number | null
  ) {
    super(message);
    this.name = 'DlpApiError';
  }
}

function statusOf(e: unknown): number | null {
  if (e && typeof e === 'object' && 'status' in e) {
    const st = (e as { status?: unknown }).status;
    if (typeof st === 'number') return st;
  }
  return null;
}

async function call<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (e) {
    throw new DlpApiError(e instanceof Error ? e.message : String(e), statusOf(e));
  }
}

// ---- 类型（对齐 shim admin API 响应形状，字段名与 shim/admin_api.py 校验一致）----

/** 商密词表（issue #32）：PUT 整体替换 terms */
export interface WordlistTerm {
  value: string;
  rule_id: string;
}
export interface WordlistDoc {
  version: number;
  _comment?: string;
  terms: WordlistTerm[];
}

/** PII recognizer（issue #32，结构同 recognizers/pii-zh.json） */
export interface RecognizerPattern {
  name: string;
  regex: string;
  score: number;
}
export interface Recognizer {
  name: string;
  entity: string;
  patterns: RecognizerPattern[];
  context: string[];
  replacement: string;
}
export interface RecognizersDoc {
  version: number;
  _comment?: string;
  recognizers: Recognizer[];
}

/** L1/L1.5 格式规则（issue #33）：PUT 整体替换，保存即渲染网关配置并热重载 */
export interface FormatRule {
  code: string;
  layer: 'L1' | 'L1.5';
  action: 'reject' | 'mask';
  enabled: boolean;
  message?: string;
  gateway_patterns: string[];
  shim_patterns: string[];
}
export interface FormatRulesDoc {
  version: number;
  _comment?: string;
  rules: FormatRule[];
}

/** EDM 语料文档列表项（issue #34）：GET 返回数组 */
export interface EdmDocSummary {
  name: string;
  shingle_count: number;
  line_count: number;
  added_at: string | null;
}

/** 统一 settings（issue #35）：PUT 整体替换（六段必填且字段齐全） */
/** judge 动作分级（issue #94）：off 关 / shadow 仅记录 / warn 告警 / reject 拦截；
 * issue #101 消费落地：warn=超阈值告警不拦截；reject 契约「语义层永不阻断」不支持——
 * 面板灰置不可选、validateJudge 拒绝保存，后端对存量 reject 值按 shadow 处理 */
export type JudgeAction = 'off' | 'shadow' | 'warn' | 'reject';
export interface JudgeSettings {
  enabled: boolean;
  model: string;
  base_url: string;
  timeout: number;
  prompt_system: string;
  prompt_fewshot: string;
  threshold: number; // issue #94 置信度门槛（0~1）
  action: JudgeAction; // issue #94 动作分级；后端 PUT 必填（shim _SETTINGS_JUDGE_KEYS）
  sample_rate: number; // issue #93 判定采样率（0~1）；后端 PUT 必填，面板无控件
  max_concurrency: number; // issue #93 judge 并发上限（≥1）；后端 PUT 必填，面板无控件
  /** issue #105 注入判定第二职责（#100 路线③生产落点）：开关默认 false 先进场 shadow；
   * 专用注入 prompt 单一源=settings.json（web 不内置文本；关态允许空串占位）；
   * 注入判定永不阻断/不告警——观测价值只在 shadow 水位统计（shadow_log judge_inject 层） */
  inject_enabled: boolean;
  inject_prompt_system: string;
  inject_prompt_fewshot: string;
}
export interface EdmSettings {
  enabled: boolean;
  min_hits: number;
}
export interface PgSettings {
  enabled: boolean;
  threshold: number;
  normalize: boolean; // issue #44 打分前置归一化开关；后端 PUT 必填（shim _SETTINGS_PG_KEYS）
  block_enabled: boolean; // issue #103 高分阻断开关（开=≥阻断阈值 451）；后端 PUT 必填
  block_threshold: number; // issue #103 阻断阈值（0~1）；后端 PUT 必填
}
/** 注入规则层（issue #104，#100 路线② 生产落点）：语义模式组命中（布尔无分数——无阈值键）；
 * enabled 默认 false（新层先进场 shadow 观察）、block 默认 false（开=命中即 451）；后端 PUT 必填 */
export interface InjectRulesSettings {
  enabled: boolean;
  block: boolean;
}
/** rules 段读侧缺段/缺键补默认（issue #104，先例见 normalizePg）：#104 前写入的 settings.json
 * 无 rules 段，不补则面板整体 PUT 时后端必填校验 400；缺省值与 shim setting_value 缺省对齐（双关） */
export function normalizeInjectRules(rules: Partial<InjectRulesSettings> | undefined): InjectRulesSettings {
  return {
    enabled: rules?.enabled ?? false,
    block: rules?.block ?? false,
  };
}
/** pg 段读侧缺键补默认（issue #70/#103）：旧 settings.json（issue #44/#103 前写入）可能缺
 * normalize/block_enabled/block_threshold，不补则面板整体 PUT 时后端必填校验 400；
 * 缺省值与 shim setting_value 缺省对齐（enabled/normalize/block_enabled 默认 false、
 * threshold 默认 0.7、block_threshold 默认 0.9） */
export function normalizePg(pg: Partial<PgSettings> | undefined): PgSettings {
  return {
    enabled: pg?.enabled ?? false,
    threshold: pg?.threshold ?? 0.7,
    normalize: pg?.normalize ?? false,
    block_enabled: pg?.block_enabled ?? false,
    block_threshold: pg?.block_threshold ?? 0.9,
  };
}
/** judge 段读侧缺键补默认（issue #94/#93/#105）：旧 settings.json（两票前写入）可能缺
 * threshold/action/sample_rate/max_concurrency；#105 前写入的缺 inject_* 三键，
 * 不补则面板整体 PUT 时后端必填校验 400；
 * 缺省值与 shim setting_value 缺省对齐（0.8 / shadow / 1.0 / 2 / 注入关+空 prompt 占位——
 * prompt 单一源=settings.json，web 不内置 prompt 文本） */
export function normalizeJudge(judge: Partial<JudgeSettings> | undefined): JudgeSettings {
  return {
    enabled: judge?.enabled ?? false,
    model: judge?.model ?? '',
    base_url: judge?.base_url ?? '',
    timeout: judge?.timeout ?? 8,
    prompt_system: judge?.prompt_system ?? '',
    prompt_fewshot: judge?.prompt_fewshot ?? '',
    threshold: judge?.threshold ?? 0.8,
    action: judge?.action ?? 'shadow',
    sample_rate: judge?.sample_rate ?? 1.0,
    max_concurrency: judge?.max_concurrency ?? 2,
    inject_enabled: judge?.inject_enabled ?? false, // issue #105：缺省关=维持商密单职责现状
    inject_prompt_system: judge?.inject_prompt_system ?? '',
    inject_prompt_fewshot: judge?.inject_prompt_fewshot ?? '',
  };
}
/** 智能路由节（issue #117 五键必填 + issue #119 五扩展键可选）：settings 可选节
 * （缺席=关态合法，shim 运行侧 routing.enabled 缺省 false）；出席即五键齐全校验。
 * 面板读侧经 smart-routing/api.ts normalizeRouting 补默认后十键齐全；扩展键在
 * normalize 后总是出席（保存即显式落盘，与 shim 出席才校验语义兼容） */
export interface RoutingSettings {
  enabled: boolean;
  threshold: number; // p_complex ≥ 阈值判 complex（0~1）
  tiers: { simple: string; complex: string }; // 两档→真实模型映射（字符白名单进响应头，shim 严校）
  timeout: number; // 分类调用超时秒（>0）
  max_concurrency: number; // 分类并发预算（≥1 整数，独立于 judge.max_concurrency）
  prompt: string; // issue #119：分类系统提示（缺省=shim ROUTER_PROMPT_SYSTEM）
  escalate_conf: number; // issue #119：升档强置信门槛（0~1，缺省 0.85）
  session_ttl: number; // issue #119：会话档位 TTL 秒（>0，缺省 3600）
  tool_loop_lock: boolean; // issue #119：tool-loop 锁（缺省 true）
  thinking_lock: boolean; // issue #119：thinking 锁（缺省 true）
}
/** 分层总开关（issue #40）：单键段；旧 settings.json 可能缺段（shim 侧缺段默认 true），
 * 故读侧按可选处理、展示缺省回退 true 与服务端语义对齐 */
export interface LayerSwitchSettings {
  enabled: boolean;
}
/** issue #127：L2 内嵌 OPF（privacy-filter）第二检测器可选子节（缺席=关态合法，
 * 运行侧回退 env/内置默认；url 可选增补键，缺席=env OPF_URL/内置默认） */
export interface OpfSettings {
  enabled: boolean;
  timeout_ms: number;
  max_chars: number;
  url?: string;
}
export interface L2Settings extends LayerSwitchSettings {
  opf?: OpfSettings;
}
export interface DlpSettings {
  version: number;
  _comment?: string;
  judge: JudgeSettings;
  edm: EdmSettings;
  pg: PgSettings;
  rules: InjectRulesSettings; // issue #104：#104 前旧文件可能缺段，读侧经 normalizeInjectRules 补齐
  l1?: LayerSwitchSettings;
  l2?: L2Settings;
  response?: LayerSwitchSettings;
  /** issue #117/#119：智能路由可选节（缺席=关态合法，读侧不在本页 normalize——
   * rules 页整体 PUT 原样透传；配置读写走智能路由页（issue #120）normalizeRouting 补默认） */
  routing?: RoutingSettings;
}

// ---- query keys ----
const QK = {
  wordlist: ['dlp-admin', 'wordlist'],
  recognizers: ['dlp-admin', 'recognizers'],
  formatRules: ['dlp-admin', 'format-rules'],
  edmCorpus: ['dlp-admin', 'edm-corpus'],
  settings: ['dlp-admin', 'settings'],
} as const;

const BASE = '/dlp-admin';

function get<T>(path: string): Promise<T> {
  return call(() => apiRequest<T>(`${BASE}${path}`, { requireAuth: true }));
}

function put<T>(path: string, body: unknown): Promise<T> {
  return call(() => apiRequest<T>(`${BASE}${path}`, { method: 'PUT', body, requireAuth: true }));
}

function post<T>(path: string, body: unknown): Promise<T> {
  return call(() => apiRequest<T>(`${BASE}${path}`, { method: 'POST', body, requireAuth: true }));
}

function del<T>(path: string): Promise<T> {
  return call(() => apiRequest<T>(`${BASE}${path}`, { method: 'DELETE', requireAuth: true }));
}

/** 文件直传（issue #48）：raw bytes body（application/octet-stream），apiRequest 只发 JSON 故单独写；
 * 鉴权与 apiRequest requireAuth 同机制（Bearer 取 localStorage），错误形状 {"error": "原因"} 提取进 Error.message */
async function postFile<T>(path: string, file: File): Promise<T> {
  return call(async () => {
    const token = getTokenFromStorage();
    const resp = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/octet-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: file,
    });
    if (!resp.ok) {
      let message = `HTTP ${resp.status}: ${resp.statusText}`;
      try {
        const data: unknown = await resp.json();
        if (data && typeof data === 'object' && 'error' in data && typeof data.error === 'string') {
          message = data.error;
        }
      } catch {
        // 非 JSON 错误体：保留 HTTP 状态文案
      }
      throw new DlpApiError(message, resp.status); // 带 status（issue #49 P2-4），与 apiRequest 的 ApiError 形状一致
    }
    return (await resp.json()) as T;
  });
}

/** 写操作失败统一 toast（API error 原因带在 description） */
function onMutError(action: string) {
  return (e: unknown) =>
    toast.error(`${action}失败`, { description: e instanceof Error ? e.message : String(e) });
}

// ---- 查询 ----

export function useWordlist() {
  return useQuery({ queryKey: QK.wordlist, queryFn: () => get<WordlistDoc>('/wordlist') });
}

export function useRecognizers() {
  return useQuery({ queryKey: QK.recognizers, queryFn: () => get<RecognizersDoc>('/recognizers') });
}

export function useFormatRules() {
  return useQuery({ queryKey: QK.formatRules, queryFn: () => get<FormatRulesDoc>('/format-rules') });
}

export function useEdmCorpus() {
  return useQuery({ queryKey: QK.edmCorpus, queryFn: () => get<EdmDocSummary[]>('/edm/corpus') });
}

// select 提升模块级：内联匿名函数每次渲染新建引用会让 TanStack Query 的 select 记忆化失效（issue #70 评审 P2）
const selectSettings = (d: DlpSettings): DlpSettings => ({
  ...d,
  judge: normalizeJudge(d.judge),
  pg: normalizePg(d.pg),
  rules: normalizeInjectRules(d.rules), // issue #104：旧文件缺 rules 段补默认（双关）
});

export function useSettings() {
  // retry: false——settings.json 缺失回 404（env 兜底态，合法），交由面板即时展示而非重试
  // select：judge 段缺 threshold/action（issue #94）、pg 段缺键（issue #70）补默认，
  // 保存面板整体 PUT 不因缺键被后端 400
  return useQuery({
    queryKey: QK.settings,
    queryFn: () => get<DlpSettings>('/settings'),
    retry: false,
    select: selectSettings,
  });
}

// ---- 写操作（全部写后 invalidate 重取，与"shim 每请求重读热生效"语义对齐）----

export function usePutWordlist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (terms: WordlistTerm[]) => put<WordlistDoc>('/wordlist', { terms }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: QK.wordlist });
      toast.success('词表已保存，即时热生效');
    },
    onError: onMutError('词表保存'),
  });
}

/** recognizer 新增（POST）/替换（PUT /<name>，name 以 URL 为准）二合一 */
export function useSaveRecognizer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ rec, originalName }: { rec: Recognizer; originalName: string | null }) =>
      originalName === null
        ? post<RecognizersDoc>('/recognizers', rec)
        : put<RecognizersDoc>(`/recognizers/${encodeURIComponent(originalName)}`, rec),
    onSuccess: async (_data, vars) => {
      await qc.invalidateQueries({ queryKey: QK.recognizers });
      toast.success(vars.originalName === null ? 'PII 规则已新增' : 'PII 规则已保存');
    },
    onError: onMutError('PII 规则保存'),
  });
}

export function useDeleteRecognizer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => del<RecognizersDoc>(`/recognizers/${encodeURIComponent(name)}`),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: QK.recognizers });
      toast.success('PII 规则已删除');
    },
    onError: onMutError('PII 规则删除'),
  });
}

/** format-rules PUT 整体替换：服务端保存即渲染 agentgateway config 并热重载（失败回滚，两侧不留半更新） */
export function usePutFormatRules() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (doc: FormatRulesDoc) => put<FormatRulesDoc>('/format-rules', doc),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: QK.formatRules });
      toast.success('格式规则已保存，网关配置已渲染并热重载');
    },
    onError: onMutError('格式规则保存'),
  });
}

export function useUploadEdmDoc() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, text }: { name: string; text: string }) =>
      post<{ name: string; shingle_count: number; line_count: number }>('/edm/corpus', { name, text }),
    onSuccess: async (data) => {
      await qc.invalidateQueries({ queryKey: QK.edmCorpus });
      toast.success(`语料 ${data.name} 已入库：shingle ${data.shingle_count} / 行级 ${data.line_count} 指纹`);
    },
    onError: onMutError('语料上传'),
  });
}

/** 文件直传（issue #48）：.pdf/.docx/.xlsx/.pptx 服务端解析提取文本后指纹化，响应形状与粘贴路径一致 */
export function useUploadEdmFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, file }: { name: string; file: File }) =>
      postFile<{ name: string; shingle_count: number; line_count: number }>(
        `/edm/corpus/upload?name=${encodeURIComponent(name)}&filename=${encodeURIComponent(file.name)}`,
        file,
      ),
    onSuccess: async (data) => {
      await qc.invalidateQueries({ queryKey: QK.edmCorpus });
      toast.success(`语料 ${data.name} 已入库：shingle ${data.shingle_count} / 行级 ${data.line_count} 指纹`);
    },
    onError: onMutError('语料上传'),
  });
}

export function useDeleteEdmDoc() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => del<{ deleted: string }>(`/edm/corpus/${encodeURIComponent(name)}`),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: QK.edmCorpus });
      toast.success('语料文档已删除（指纹同步移除）');
    },
    onError: onMutError('语料删除'),
  });
}

export function usePutSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (doc: DlpSettings) => put<DlpSettings>('/settings', doc),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: QK.settings });
      toast.success('开关与阈值已保存，即时热生效');
    },
    onError: onMutError('设置保存'),
  });
}
