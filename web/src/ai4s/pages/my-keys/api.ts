/**
 * 「我的 Key」数据层（issue #74 列表 / issue #79 控制台申请通道）。
 * 端点语义：caller Bearer 经 axonhub me 内省确认身份，admin token 服务端按 userID=me.id 过滤，
 * 只回白名单字段——绝不含他人 key；issue #81 起含本人 key 明文（唯一闸门=服务端本人过滤，
 * 页面默认掩码展示，点「显示」查看）。
 * 鉴权/错误：401=未登录或 token 失效；503=内省或查询暂不可用（不降级）。
 * issue #79：申请提交后 30s 轮询本人申请列表（状态翻转经审批卡/私信异步发生，与巡检节奏一致）。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api-client';

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
}

interface MyKeysResponse {
  keys: MyKey[];
}

export function useMyKeys() {
  return useQuery({
    queryKey: ['self', 'keys'],
    queryFn: () => apiRequest<MyKeysResponse>('/self/keys', { requireAuth: true }),
    retry: false,
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
}

interface KeyRequestsResponse {
  requests: KeyRequest[];
}

export function useMyKeyRequests() {
  return useQuery({
    queryKey: ['self', 'key-requests'],
    queryFn: () => apiRequest<KeyRequestsResponse>('/self/key-requests', { requireAuth: true }),
    retry: false,
    refetchInterval: 30_000, // 审批结果异步到达（管理员点批/超时清扫），与巡检节奏一致
  });
}

export function useCreateKeyRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { kind: 'new' | 'upgrade'; purpose?: string; tier?: string }) =>
      apiRequest<{ request: KeyRequest }>('/self/key-requests', {
        method: 'POST',
        requireAuth: true,
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
