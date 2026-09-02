import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { graphqlRequest } from '@/gql/graphql';
import { useSelectedProjectId } from '@/stores/projectStore';
import { useErrorHandler } from '@/hooks/use-error-handler';
import { TraceDetail, traceDetailSchema } from './trace-schema';

// traces 特征删除后，threads 详情抽屉仍需按 trace 拉取 rawRootSegment 渲染时间线；
// 该 hook 从原 features/traces/data/traces.ts 迁入（仅保留 segments 查询，列表/归档类 hook 随 traces 一并删除）。
function buildTraceWithSegmentsQuery() {
  return `query GetTraceWithSegments($id: ID!) {
      node(id: $id) {
        ... on Trace {
          id
          traceID
          status
          createdAt
          updatedAt
          usageMetadata {
            totalInputTokens
            totalOutputTokens
            totalTokens
            totalCost
            totalCachedTokens
            totalCachedWriteTokens
          }
          project {
            id
            name
          }
          thread {
            id
            threadID
          }
          requests(where: { status: completed }) {
            totalCount
          }
          rawRootSegment
        }
      }
    }
  `;
}

export function useTraceWithSegments(id: string) {
  const { handleError } = useErrorHandler();
  const { t } = useTranslation();
  const selectedProjectId = useSelectedProjectId();

  return useQuery({
    queryKey: ['trace-with-segments', id, selectedProjectId],
    queryFn: async () => {
      try {
        const query = buildTraceWithSegmentsQuery();
        const headers = selectedProjectId ? { 'X-Project-ID': selectedProjectId } : undefined;
        const data = await graphqlRequest<{ node: TraceDetail }>(query, { id }, headers);
        if (!data.node) {
          throw new Error('Trace not found');
        }
        return traceDetailSchema.parse(data.node);
      } catch (error) {
        handleError(error, t('common.errors.internalServerError'));
        throw error;
      }
    },
    enabled: !!id,
  });
}
