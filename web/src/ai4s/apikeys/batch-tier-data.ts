import { useQuery, useQueryClient } from '@tanstack/react-query';
import { graphqlRequest } from '@/gql/graphql';
import { useRequestPermissions } from '@/hooks/useRequestPermissions';

import type { BatchTierKey, BatchTierTemplate } from './batch-tier';

/**
 * 批量换档数据层（issue #64）：复用 web 前端既有 graphqlRequest client 与
 * X-Project-ID 头约定（对齐 features/apikeys/data/apikeys.ts）。
 * key 列表查询是 ai4s 自有字段集——vendor 列表查询不含 profiles.activeProfile，
 * 而筛选/预览都要它；字段名与 vendor buildApiKeysQuery 保持同源口径。
 */

function buildBatchTierKeysQuery(canViewUsers: boolean) {
  const userFields = canViewUsers
    ? `
            user {
              id
              firstName
              lastName
              email
            }`
    : '';
  return `
    query Ai4sBatchTierKeys($first: Int, $where: APIKeyWhereInput) {
      apiKeys(first: $first, where: $where) {
        edges {
          node {
            id
            name
            status
            projectID
            userID${userFields}
            profiles {
              activeProfile
            }
          }
        }
        totalCount
      }
    }
  `;
}

export interface BatchTierKeyNode extends BatchTierKey {
  status: string;
  user?: { id: string; firstName?: string | null; lastName?: string | null; email?: string | null } | null;
}

const NOAUTH_API_KEY_TYPE = 'noauth';

/** 拉取候选 key（PoC 规模 first:200，员工/当前档维度客户端再筛） */
export function useBatchTierKeys(projectId?: string) {
  const permissions = useRequestPermissions();

  return useQuery({
    queryKey: ['ai4sBatchTierKeys', projectId, permissions.canViewUsers],
    queryFn: async () => {
      const data = await graphqlRequest<{ apiKeys: { edges: { node: BatchTierKeyNode }[]; totalCount: number } }>(
        buildBatchTierKeysQuery(permissions.canViewUsers),
        {
          first: 200,
          where: {
            statusIn: ['enabled', 'disabled'],
            typeNotIn: [NOAUTH_API_KEY_TYPE],
            ...(projectId ? { projectID: projectId } : {}),
          },
        },
        projectId ? { 'X-Project-ID': projectId } : undefined
      );
      return data.apiKeys.edges.map((e) => e.node);
    },
    enabled: !!projectId,
  });
}

const BATCH_TIER_TEMPLATES_QUERY = `
  query Ai4sBatchTierTemplates {
    apiKeyProfileTemplates(first: 100) {
      edges {
        node {
          id
          name
          description
          profile {
            name
            quota {
              requests
              totalTokens
              cost
              period {
                type
                pastDuration { value unit }
                calendarDuration { unit }
              }
            }
          }
        }
      }
    }
  }
`;

export interface BatchTierTemplateNode extends BatchTierTemplate {
  description?: string | null;
}

/** 目标档模板列表（限额档=quota 模板，体验档/标准档/高档…） */
export function useBatchTierTemplates(projectId?: string) {
  return useQuery({
    queryKey: ['ai4sBatchTierTemplates', projectId],
    queryFn: async () => {
      const data = await graphqlRequest<{ apiKeyProfileTemplates: { edges: { node: BatchTierTemplateNode }[] } }>(
        BATCH_TIER_TEMPLATES_QUERY,
        {},
        projectId ? { 'X-Project-ID': projectId } : undefined
      );
      return data.apiKeyProfileTemplates.edges.map((e) => e.node);
    },
    enabled: !!projectId,
  });
}

const UPDATE_APIKEY_PROFILES_MUTATION = `
  mutation UpdateAPIKeyProfiles($id: ID!, $input: UpdateAPIKeyProfilesInput!) {
    updateAPIKeyProfiles(id: $id, input: $input) {
      id
      profiles {
        activeProfile
      }
    }
  }
`;

export interface BatchTierResult {
  id: string;
  name: string;
  ok: boolean;
  error?: string;
}

/** 逐条换档：一条失败不中断后续，逐条回报（PoC 规模前端循环直调，不加 shim 端点） */
export async function executeBatchTierChange(
  keys: BatchTierKey[],
  template: BatchTierTemplate,
  profileInput: Record<string, unknown>,
  projectId?: string
): Promise<BatchTierResult[]> {
  const headers = projectId ? { 'X-Project-ID': projectId } : undefined;
  const results: BatchTierResult[] = [];
  for (const key of keys) {
    try {
      await graphqlRequest(
        UPDATE_APIKEY_PROFILES_MUTATION,
        { id: key.id, input: { activeProfile: template.name, profiles: [profileInput] } },
        headers
      );
      results.push({ id: key.id, name: key.name, ok: true });
    } catch (error) {
      results.push({ id: key.id, name: key.name, ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  }
  return results;
}

/** 换档完成后失效 key 列表缓存（告警轮询读的 apiKeyQuotaUsages 也一并失效，按新档配额计算） */
export function useInvalidateAfterBatchTier() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ['apiKeys'] });
    queryClient.invalidateQueries({ queryKey: ['ai4sBatchTierKeys'] });
    queryClient.invalidateQueries({ queryKey: ['apiKeyQuotaUsages'] });
  };
}
