/**
 * 管理员「Key 审批」数据层（issue #79）：对接 shim admin 平面 /dlp-admin/key-requests*。
 * 鉴权：读 read_channels / 写 write_channels（isOwner 直通）——与词表/规则等 admin 端点同例。
 * 点批语义：approve 触发 shim 同步执行（建 Key/提档，复用 #72/#19 执行体），执行失败 502 保持待审批；
 * 非 pending 重复点批幂等返回现状（不重复建 Key）。
 * issue #81：approve 可带 tier 覆盖执行档位（审批弹窗选档；空串=默认：新建体验档/提额所求档），
 * 档位白名单在 shim key_requests.ALLOWED_TIERS。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api-client';
import type { KeyRequest } from '../my-keys/api';

export interface AdminKeyRequest extends KeyRequest {
  applicant?: { id?: string; email?: string; openId?: string | null };
}

interface KeyRequestsResponse {
  requests: AdminKeyRequest[];
}

const QK = ['dlp-admin', 'key-requests'];

export function useAdminKeyRequests() {
  return useQuery({
    queryKey: QK,
    queryFn: () => apiRequest<KeyRequestsResponse>('/dlp-admin/key-requests', { requireAuth: true }),
    retry: false,
    refetchInterval: 30_000, // 新申请异步到达（员工控制台提交），与巡检节奏一致
  });
}

export function useResolveKeyRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, reason, tier }: { id: string; action: 'approve' | 'reject'; reason?: string; tier?: string }) =>
      apiRequest<{ request: AdminKeyRequest }>(`/dlp-admin/key-requests/${action}/${id}`, {
        method: 'POST',
        requireAuth: true,
        // issue #81：approve 带管理员选定档位（空串=shim 端默认）；api-client 统一 JSON.stringify
        body: action === 'reject' ? { reason: reason || '' } : { tier: tier || '' },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK }),
  });
}
