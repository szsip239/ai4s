/**
 * 「我的 Key」数据层（issue #74 列表 / issue #79 控制台申请通道）。
 * 端点语义：caller Bearer 经 axonhub me 内省确认身份，admin token 服务端按 userID=me.id 过滤，
 * 只回白名单字段——绝不含他人 key；issue #81 起含本人 key 明文（唯一闸门=服务端本人过滤，
 * 页面默认掩码展示，点「显示」查看）。
 * issue #83：响应内嵌 usage（shim 代查 apiKeyQuotaUsages，与管理员侧 profiles 对话框同源）；
 * usage=null 表示用量暂不可用（展示降级，不影响列表）。
 * 鉴权/错误：401=未登录或 token 失效；503=内省或查询暂不可用（不降级）。
 * issue #79：申请提交后 30s 轮询本人申请列表（状态翻转经审批卡/私信异步发生，与巡检节奏一致）。
 * issue #86：提额申请按 Key 勾选——提交带 keyIds，响应带 keyIds/keyNames 快照。
 * issue #89：多项目隔离——查询/提交带 X-Project-ID 头（控制台 projectStore 的 gid），
 * queryKey 以项目为维度；projectId 为空（未选项目）时 enabled=false 不发请求（页面空态引导）。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api-client';
import type { KeyUsageStats, UsageEntry, UsageWindow } from './key-usage';

export interface MyKeyProfileQuota {
  requests?: number | null;
  totalTokens?: number | null;
  cost?: string | null;
}

export interface MyKeyProfile {
  name: string;
  quota?: MyKeyProfileQuota | null;
}

export interface MyKeyProfiles {
  activeProfile?: string | null;
  profiles?: MyKeyProfile[] | null;
}

export interface MyKey {
  id: string;
  name: string;
  /** 明文（issue #81 本人可见；旧 shim 不下发时缺省，页面显示 —） */
  key?: string;
  status: 'enabled' | 'disabled' | 'archived' | string;
  createdAt?: string | null;
  profiles?: MyKeyProfiles | null;
  /** 各档用量（issue #83 shim 代查内嵌）；null=用量暂不可用，展示降级不报错 */
  usage?: UsageEntry[] | null;
}

interface MyKeysResponse {
  keys: MyKey[];
}

export function useMyKeys(projectId: string | null) {
  return useQuery({
    queryKey: ['self', 'keys', projectId],
    queryFn: () =>
      apiRequest<MyKeysResponse>('/self/keys', {
        requireAuth: true,
        headers: { 'X-Project-ID': projectId as string },
      }),
    retry: false,
    enabled: !!projectId,
  });
}

// ---- 时间窗用量（不设限档也显示用量）：shim 代查 apiKeyTokenUsageStats（与管理员侧 token chart 同源） ----

interface KeyUsageStatsResponse {
  stats: KeyUsageStats;
  window: string;
  since?: string | null;
}

/** 本人 key 时间窗 token 用量（day=今日/month=本月/all=累计）；tz 传浏览器偏移（getTimezoneOffset），窗口界按本地时区算 */
export function useKeyUsageStats(keyId: string | null, window: UsageWindow, projectId: string | null) {
  return useQuery({
    queryKey: ['self', 'key-usage-stats', keyId, window, projectId],
    queryFn: () =>
      apiRequest<KeyUsageStatsResponse>(
        `/self/key-usage-stats?key=${encodeURIComponent(keyId as string)}&window=${window}&tz=${new Date().getTimezoneOffset()}`,
        { requireAuth: true, headers: { 'X-Project-ID': projectId as string } }
      ),
    retry: false,
    enabled: !!projectId && !!keyId,
  });
}

// ---- issue #79：控制台发起 key 申请 ----

export type KeyRequestStatus = 'pending' | 'approved' | 'rejected' | 'expired' | 'canceled';

export interface KeyRequest {
  id: string;
  kind: 'new' | 'upgrade';
  purpose?: string;
  tier?: string;
  status: KeyRequestStatus;
  createdAt?: string;
  resolvedAt?: string | null;
  result?: string;
  keyName?: string | null;
  /** issue #86：提额申请所选 Key（id 列表 + 名称快照）；存量申请无此字段 */
  keyIds?: string[] | null;
  keyNames?: string[] | null;
  /** issue #89：申请目标项目（gid + 名称快照）；存量申请无此字段（shim/页面均按 Default 口径） */
  projectId?: string | null;
  projectName?: string | null;
  /** issue #128：批准时管理员改选的目标项目（gid + 名称快照）；有值时展示优先于申请快照 */
  projectOverride?: string | null;
  projectNameOverride?: string | null;
}

/**
 * issue #86 评审 P1-2：提额申请详情显示——名称快照优先；fail-open 缺快照但有 keyIds 回退列 id；
 * 真存量申请（两者皆无）返回 null，调用方回退只显示目标档。员工页/审批页共用。
 */
export function upgradeDetailLabel(r: KeyRequest): string | null {
  if (r.keyNames?.length) return `${r.tier}（${r.keyNames.join(', ')}）`;
  if (r.keyIds?.length) return `${r.tier}（${r.keyIds.join(', ')}）`;
  return null;
}

interface KeyRequestsResponse {
  requests: KeyRequest[];
}

export function useMyKeyRequests(projectId: string | null) {
  return useQuery({
    queryKey: ['self', 'key-requests', projectId],
    queryFn: () =>
      apiRequest<KeyRequestsResponse>('/self/key-requests', {
        requireAuth: true,
        headers: { 'X-Project-ID': projectId as string },
      }),
    retry: false,
    enabled: !!projectId,
    refetchInterval: 30_000, // 审批结果异步到达（管理员点批/超时清扫），与巡检节奏一致
  });
}

export function useCreateKeyRequest(projectId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { kind: 'new' | 'upgrade'; purpose?: string; tier?: string; keyIds?: string[] }) =>
      apiRequest<{ request: KeyRequest }>('/self/key-requests', {
        method: 'POST',
        requireAuth: true,
        headers: { 'X-Project-ID': projectId as string },
        body: input, // api-client 统一 JSON.stringify，此处传对象即可
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['self', 'key-requests'] });
      qc.invalidateQueries({ queryKey: ['self', 'keys'] });
    },
  });
}

// ---- issue #80：撤回本人 pending 申请 ----

export function useCancelKeyRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiRequest<{ request: KeyRequest }>(`/self/key-requests/${id}/cancel`, {
        method: 'POST',
        requireAuth: true,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['self', 'key-requests'] }),
  });
}
