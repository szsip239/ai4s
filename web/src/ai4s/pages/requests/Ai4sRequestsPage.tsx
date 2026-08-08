import { useState } from 'react';
import { IconShieldExclamation } from '@tabler/icons-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Header } from '@/components/layout/header';
import { Main } from '@/components/layout/main';
import { RequestsTable } from '@/features/requests/components';
import { RequestsProvider } from '@/features/requests/context';
import { useRequests } from '@/features/requests/data';
import { usePaginationSearch } from '@/hooks/use-pagination-search';
import { Ai4sRequestMetaDrawer } from './Ai4sRequestMetaDrawer';

/**
 * ai4s 审计日志页（issue #13）
 * 复用上游表格与数据层；详情改为元数据抽屉（无请求/响应原文、无 chunks、无 curl 预览）。
 * 顶栏 warn 提示条为审计原则的显式呈现。
 */
export default function Ai4sRequestsPage() {
  const { pageSize, setCursors, setPageSize, resetCursor, paginationArgs } = usePaginationSearch({
    defaultPageSize: 20,
    pageSizeStorageKey: 'requests-table-page-size',
  });
  const { data, isLoading, refetch } = useRequests({
    ...paginationArgs,
    orderBy: { field: 'CREATED_AT', direction: 'DESC' },
  });
  const requests = data?.edges?.map((e) => e.node) || [];
  const pageInfo = data?.pageInfo;

  const [detailId, setDetailId] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);

  return (
    <RequestsProvider>
      <Header title="审计日志" />
      <Main>
        <Alert variant="warning" className='mb-4 border-warn/40 bg-warn-soft text-foreground'>
          <IconShieldExclamation className='size-4' />
          <AlertTitle>审计原则</AlertTitle>
          <AlertDescription>
            本页仅展示请求元数据（时间 / 用户 / Key / 模型 / 状态 / 成本），<b>不提供请求与响应原文</b>。原文留痕按
            ai4s 审计纪律收敛，排查请走授权流程。
          </AlertDescription>
        </Alert>

        <RequestsTable
          data={requests}
          loading={isLoading}
          pageInfo={pageInfo}
          pageSize={pageSize}
          statusFilter={[]}
          sourceFilter={[]}
          channelFilter={[]}
          apiKeyFilter={[]}
          modelIDFilter={''}
          onNextPage={() => {
            if (pageInfo?.hasNextPage && pageInfo?.endCursor) {
              setCursors(pageInfo.startCursor ?? undefined, pageInfo.endCursor ?? undefined, 'after');
            }
          }}
          onPreviousPage={() => {
            if (pageInfo?.hasPreviousPage) {
              setCursors(pageInfo.startCursor ?? undefined, pageInfo.endCursor ?? undefined, 'before');
            }
          }}
          onPageSizeChange={(n) => {
            setPageSize(n);
            resetCursor();
          }}
          onFiltersChange={() => {}}
          onDateRangeChange={() => {}}
          onResetFilters={() => {}}
          onViewDetail={(id) => setDetailId(id)}
          onRefresh={() => refetch()}
          showRefresh
          autoRefresh={autoRefresh}
          onAutoRefreshChange={setAutoRefresh}
        />

        <Ai4sRequestMetaDrawer requestId={detailId} onClose={() => setDetailId(null)} />
      </Main>
    </RequestsProvider>
  );
}
