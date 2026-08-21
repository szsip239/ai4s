/**
 * 「我的 Key」数据层（issue #74）：GET /self/keys（shim 员工自助端点）。
 * 端点语义：caller Bearer 经 axonhub me 内省确认身份，admin token 服务端按 userID=me.id 过滤，
 * 只回安全字段（name/status/createdAt/profiles）——绝不含 key 明文，绝不含他人 key。
 * 鉴权/错误：401=未登录或 token 失效；503=内省或查询暂不可用（不降级）。
 */
import { useQuery } from '@tanstack/react-query';
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
