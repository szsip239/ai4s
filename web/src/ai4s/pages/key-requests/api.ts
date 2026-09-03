/**
 * 管理员「Key 审批」数据层（issue #79）：对接 shim admin 平面 /dlp-admin/key-requests*。
 * 鉴权：读 read_channels / 写 write_channels（isOwner 直通）——与词表/规则等 admin 端点同例。
 * 点批语义：approve 触发 shim 同步执行（建 Key/提档，复用 #72/#19 执行体），执行失败 502 保持待审批；
 * 非 pending 重复点批幂等返回现状（不重复建 Key）。
 * issue #81：approve 可带 tier 覆盖执行档位（审批弹窗选档；空串=默认：新建体验档/提额所求档），
 * 档位白名单在 shim key_requests.ALLOWED_TIERS。
 * issue #89：多项目隔离——列表带 X-Project-ID 头（管理员当前项目 gid），queryKey 以项目为维度；
 * projectId 为空时 enabled=false 不发请求；点批不带头（执行落申请单记录的项目）。
 */
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api-client';
import type { KeyRequest } from '../my-keys/api';

export interface AdminKeyRequest extends KeyRequest {
  applicant?: { id?: string; email?: string; openId?: string | null };
}

interface KeyRequestsResponse {
  requests: AdminKeyRequest[];
}

const QK = ['dlp-admin', 'key-requests'];

export function useAdminKeyRequests(projectId: string | null) {
  return useQuery({
    queryKey: [...QK, projectId],
    queryFn: () =>
      apiRequest<KeyRequestsResponse>('/dlp-admin/key-requests', {
        requireAuth: true,
        headers: { 'X-Project-ID': projectId as string },
      }),
    retry: false,
    enabled: !!projectId,
    refetchInterval: 30_000, // 新申请异步到达（员工控制台提交），与巡检节奏一致
  });
}

/**
 * 管理员聚合视图：合并所有可见项目的申请（每项目一条 per-project 查询，复用上面的端点，
 * shim 端零改动）。排序：pending 优先（新到旧），其余新到旧——管理员不再因停留在别的
 * 项目而漏看待审批。id 去重（同一 gid 列表不会重复，防御性）。
 */
export function mergeAdminKeyRequests(lists: AdminKeyRequest[][]): AdminKeyRequest[] {
  const seen = new Set<string>();
  const merged: AdminKeyRequest[] = [];
  for (const list of lists) {
    for (const r of list) {
      if (!seen.has(r.id)) {
        seen.add(r.id);
        merged.push(r);
      }
    }
  }
  const byTimeDesc = (a: AdminKeyRequest, b: AdminKeyRequest) =>
    (b.createdAt || '').localeCompare(a.createdAt || '');
  return [
    ...merged.filter((r) => r.status === 'pending').sort(byTimeDesc),
    ...merged.filter((r) => r.status !== 'pending').sort(byTimeDesc),
  ];
}

export function useAllProjectKeyRequests(projectIds: string[]) {
  return useQueries({
    queries: projectIds.map((pid) => ({
      queryKey: [...QK, pid],
      queryFn: () =>
        apiRequest<KeyRequestsResponse>('/dlp-admin/key-requests', {
          requireAuth: true,
          headers: { 'X-Project-ID': pid },
        }),
      retry: false,
      refetchInterval: 30_000,
    })),
    combine: (results) => ({
      // 部分项目失败不拖垮整页：全部失败才算 error，有数据的照常展示
      data: mergeAdminKeyRequests(
        results.filter((r) => r.isSuccess).map((r) => r.data?.requests ?? [])
      ),
      isLoading: results.some((r) => r.isLoading),
      isError: results.length > 0 && results.every((r) => r.isError),
    }),
  });
}

export function useResolveKeyRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action, reason, tier, projectOverride }: { id: string; action: 'approve' | 'reject'; reason?: string; tier?: string; projectOverride?: string }) =>
      apiRequest<{ request: AdminKeyRequest }>(`/dlp-admin/key-requests/${action}/${id}`, {
        method: 'POST',
        requireAuth: true,
        // issue #81：approve 带管理员选定档位（空串=shim 端默认）；api-client 统一 JSON.stringify
        // issue #128：approve 同带 project_override（项目 gid；空串=按申请单项目原样执行，仅 kind=new 有意义）
        body: action === 'reject' ? { reason: reason || '' } : { tier: tier || '', project_override: projectOverride || '' },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK }),
  });
}
