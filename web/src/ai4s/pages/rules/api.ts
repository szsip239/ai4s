/**
 * DLP 统一配置 admin API 数据层（issue #36）：对接 shim /dlp-admin/*（契约 docs/contracts/dlp-webhook-shim.md）。
 * 鉴权：Bearer 取 localStorage axonhub_access_token（apiRequest requireAuth）；页面与 API 同源（都经网关 :3000）。
 * React Query 惯用法：写操作成功后 invalidate 重取；错误形状 {"error": "原因"} 由 apiRequest 提取进 Error.message。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { apiRequest } from '@/lib/api-client';

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

/** 统一 settings（issue #35）：PUT 整体替换（三段必填且字段齐全） */
export interface JudgeSettings {
  enabled: boolean;
  model: string;
  base_url: string;
  timeout: number;
  prompt_system: string;
  prompt_fewshot: string;
}
export interface EdmSettings {
  enabled: boolean;
  min_hits: number;
}
export interface PgSettings {
  enabled: boolean;
  threshold: number;
}
export interface DlpSettings {
  version: number;
  _comment?: string;
  judge: JudgeSettings;
  edm: EdmSettings;
  pg: PgSettings;
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

export function useSettings() {
  // retry: false——settings.json 缺失回 404（env 兜底态，合法），交由面板即时展示而非重试
  return useQuery({ queryKey: QK.settings, queryFn: () => get<DlpSettings>('/settings'), retry: false });
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
