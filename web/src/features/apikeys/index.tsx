import React, { useState } from 'react';
import type { SortingState } from '@tanstack/react-table';
import { useTranslation } from 'react-i18next';
import { useDebounce } from '@/hooks/use-debounce';
import { usePaginationSearch } from '@/hooks/use-pagination-search';
import { usePermissions } from '@/hooks/usePermissions';
import { type DateTimeRangeValue } from '@/utils/date-range';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Header } from '@/components/layout/header';
import { Main } from '@/components/layout/main';
import { Ai4sBatchTierDialog } from '@/ai4s/apikeys/Ai4sBatchTierDialog';
import { Ai4sKeyRequestsPanel } from '@/ai4s/pages/key-requests/Ai4sKeyRequestsPage';
import { usePendingKeyRequestCount } from '@/ai4s/pages/key-requests/api';
import { useMyProjects } from '@/features/projects/data/projects';
import { createColumns } from './components/apikeys-columns';
import { ApiKeysDialogs } from './components/apikeys-dialogs';
import { ApiKeysPrimaryButtons } from './components/apikeys-primary-buttons';
import { ApiKeysTable } from './components/apikeys-table';
import ApiKeysProvider from './context/apikeys-context';
import { useApiKeys } from './data/apikeys';
import { ApiKeyType } from './data/schema';

type ApiKeyTabKey = ApiKeyType | 'all';

// issue #66 去 Tab 化：类型 Tab 条不再渲染（在用 key 几乎全项目级，空 Tab 无信息量；
// 分类由表格「类型」列承载）。activeTab 状态与 whereClause 过滤逻辑保留、默认停 'all'；
// 恢复 Tab 条时把开关改回 true 即可。
const SHOW_TYPE_TABS = false;

// issue #134 方案 A：Key 审批并入 Key 管理——单路由页内 Tab「Key 列表 | Key 审批」。
// 待审批 tab 仅 read_channels（system 级，与 /key-requests 路由 RouteGuard 同门槛）可见；
// tab 标签带 pending 计数 badge（共享审批聚合查询，30s 轮询，tab 未打开时数字也在）。
type ApiKeysPageTab = 'list' | 'approvals';

/** 页内 Tab 栏（issue #134）：label 复用 apikeys.tabs.keyList / sidebar.items.keyRequests 既有键；
 * 有待审批单时数字 badge 在标签文字前（2026-09-03 owner 口径） */
function ApiKeysPageTabs({ value, onChange }: { value: ApiKeysPageTab; onChange: (tab: ApiKeysPageTab) => void }) {
  const { t } = useTranslation();
  const { data: myProjects } = useMyProjects();
  const pendingCount = usePendingKeyRequestCount((myProjects ?? []).map((p) => p.id));

  return (
    <Tabs value={value} onValueChange={(v) => onChange(v as ApiKeysPageTab)} className='mb-4 flex-shrink-0'>
      <TabsList>
        <TabsTrigger value='list'>{t('apikeys.tabs.keyList')}</TabsTrigger>
        <TabsTrigger value='approvals'>
          {pendingCount > 0 && (
            <Badge variant='destructive' className='mr-1.5 rounded-full px-1.5 py-0 text-xs'>
              {pendingCount}
            </Badge>
          )}
          {t('sidebar.items.keyRequests')}
        </TabsTrigger>
      </TabsList>
    </Tabs>
  );
}

const DEFAULT_SORTING: SortingState = [{ id: 'createdAt', desc: true }];
const SORTABLE_COLUMN_IDS = new Set(['name', 'createdAt', 'updatedAt']);

function loadSorting(): SortingState {
  const fallback = () => DEFAULT_SORTING.map((item) => ({ ...item }));
  try {
    const stored = localStorage.getItem('apikeys-table-sorting');
    if (!stored) return fallback();

    const parsed: unknown = JSON.parse(stored);
    if (!Array.isArray(parsed) || parsed.length !== 1) return fallback();

    const [primary] = parsed;
    if (
      typeof primary !== 'object' ||
      primary === null ||
      !('id' in primary) ||
      !('desc' in primary) ||
      typeof primary.id !== 'string' ||
      !SORTABLE_COLUMN_IDS.has(primary.id) ||
      typeof primary.desc !== 'boolean'
    ) {
      return fallback();
    }

    return [{ id: primary.id, desc: primary.desc }];
  } catch {
    return fallback();
  }
}

function ApiKeysContent() {
  const { t } = useTranslation();
  const { apiKeyPermissions, hasSystemScope } = usePermissions();
  const { startCursor, endCursor, cursorHistory, pageSize, setCursors, setPageSize, resetCursor, paginationArgs } =
    usePaginationSearch({
      defaultPageSize: 20,
      pageSizeStorageKey: 'apikeys-table-page-size',
    });

  const [activeTab, setActiveTab] = useState<ApiKeyTabKey>('all');
  const [sorting, setSorting] = useState<SortingState>(loadSorting);
  const [sortingCursorResetPending, setSortingCursorResetPending] = useState(false);

  // Filter states - following the same pattern as roles and users
  const [searchFilter, setSearchFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [userFilter, setUserFilter] = useState<string[]>([]);
  const [dateRange, setDateRange] = useState<DateTimeRangeValue | undefined>();

  const debouncedSearchFilter = useDebounce(searchFilter, 300);

  React.useEffect(() => {
    try {
      localStorage.setItem('apikeys-table-sorting', JSON.stringify(sorting));
    } catch {
      // Storage may be unavailable; sorting still works for the current session.
    }
  }, [sorting]);

  const hasPaginationCursor = Boolean(startCursor || endCursor || cursorHistory.length > 0);

  React.useEffect(() => {
    if (sortingCursorResetPending && !hasPaginationCursor) {
      setSortingCursorResetPending(false);
    }
  }, [hasPaginationCursor, sortingCursorResetPending]);

  // Build where clause for API filtering
  const whereClause = (() => {
    const where: Record<string, unknown> = {};
    
    // Use OR condition for searching both name and key
    if (debouncedSearchFilter) {
      where.or = [
        { nameContainsFold: debouncedSearchFilter },
        { keyContainsFold: debouncedSearchFilter },
      ];
    }
    
    if (activeTab !== 'all') {
      where.typeIn = [activeTab];
    }
    if (statusFilter.length > 0) {
      where.statusIn = statusFilter;
    } else {
      // By default, exclude archived API keys when no status filter is applied
      where.statusIn = ['enabled', 'disabled'];
    }
    if (userFilter.length > 0 && userFilter[0]) {
      where.userID = userFilter[0]; // API expects single userID
    }
    
    // Add AND condition to combine OR search with other filters
    if (where.or && (where.typeIn || where.statusIn || where.userID)) {
      const orCondition = where.or;
      delete where.or;
      return {
        and: [
          { or: orCondition },
          where,
        ],
      };
    }
    
    return Object.keys(where).length > 0 ? where : undefined;
  })();

  const currentOrderBy = React.useMemo(() => {
    if (sorting.length === 0) {
      return { field: 'CREATED_AT', direction: 'DESC' } as const;
    }

    const [primary] = sorting;
    switch (primary.id) {
      case 'name':
        return { field: 'NAME', direction: primary.desc ? 'DESC' : 'ASC' } as const;
      case 'updatedAt':
        return { field: 'UPDATED_AT', direction: primary.desc ? 'DESC' : 'ASC' } as const;
      case 'createdAt':
      default:
        return { field: 'CREATED_AT', direction: primary.desc ? 'DESC' : 'ASC' } as const;
    }
  }, [sorting]);

  const { data, isLoading } = useApiKeys({
    ...(sortingCursorResetPending ? { first: pageSize } : paginationArgs),
    where: whereClause,
    orderBy: currentOrderBy,
  });

  const tableData = React.useMemo(
    () => (data?.edges?.map((edge) => edge.node) ?? []),
    [data?.edges]
  );

  // Reset cursor when filters change
  React.useEffect(() => {
    resetCursor();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearchFilter, activeTab, statusFilter, userFilter, dateRange]);

  const handleNextPage = () => {
    if (data?.pageInfo?.hasNextPage && data?.pageInfo?.endCursor) {
      setCursors(data.pageInfo.startCursor ?? undefined, data.pageInfo.endCursor ?? undefined, 'after');
    }
  };

  const handlePreviousPage = () => {
    if (data?.pageInfo?.hasPreviousPage) {
      setCursors(data.pageInfo.startCursor ?? undefined, data.pageInfo.endCursor ?? undefined, 'before');
    }
  };

  const handlePageSizeChange = (newPageSize: number) => {
    setPageSize(newPageSize);
  };

  const handleSortingChange = (updater: SortingState | ((previous: SortingState) => SortingState)) => {
    if (hasPaginationCursor) {
      setSortingCursorResetPending(true);
    }
    setSorting((previous) => (typeof updater === 'function' ? updater(previous) : updater));
    resetCursor();
  };

  const handleResetFilters = () => {
    setSearchFilter('');
    setStatusFilter([]);
    setUserFilter([]);
    setDateRange(undefined);
    resetCursor();
  };

  const canViewCreators = hasSystemScope('read_users');

  const columns = React.useMemo(
    () => createColumns(t, apiKeyPermissions.canWrite, canViewCreators),
    [t, apiKeyPermissions.canWrite, canViewCreators]
  );

  return (
    <div className='flex min-h-0 flex-1 flex-col overflow-hidden'>
      {/* issue #66：类型 Tab 条整体不渲染，默认 'all' 视图（开关见文件顶部 SHOW_TYPE_TABS） */}
      {SHOW_TYPE_TABS && (
        <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as ApiKeyTabKey)} className='w-full'>
          <TabsList className='shadow-soft border-border bg-background grid w-full grid-cols-4 rounded-2xl border'>
            <TabsTrigger value='all' data-value='all'>
              {t('apikeys.tabs.all')}
            </TabsTrigger>
            <TabsTrigger value='user' data-value='user'>
              {t('apikeys.type.user')}
            </TabsTrigger>
            <TabsTrigger value='personal' data-value='personal'>
              {t('apikeys.type.personal')}
            </TabsTrigger>
            <TabsTrigger value='service_account' data-value='service_account'>
              {t('apikeys.type.service_account')}
            </TabsTrigger>
          </TabsList>
        </Tabs>
      )}
      <div className='mt-6 flex min-h-0 flex-1 flex-col overflow-hidden'>
        <ApiKeysTable
          data={tableData}
          loading={isLoading}
          columns={columns}
          pageInfo={data?.pageInfo}
          pageSize={pageSize}
          totalCount={data?.totalCount}
          searchFilter={searchFilter}
          statusFilter={statusFilter}
          userFilter={userFilter}
          dateRange={dateRange}
          sorting={sorting}
          onNextPage={handleNextPage}
          onPreviousPage={handlePreviousPage}
          onPageSizeChange={handlePageSizeChange}
          onSearchFilterChange={setSearchFilter}
          onStatusFilterChange={setStatusFilter}
          onUserFilterChange={setUserFilter}
          onDateRangeChange={setDateRange}
          onSortingChange={handleSortingChange}
          onResetFilters={handleResetFilters}
          canWrite={apiKeyPermissions.canWrite}
          canViewCreators={canViewCreators}
        />
      </div>
    </div>
  );
}

export default function ApiKeysManagement() {
  const { t } = useTranslation();
  const { hasSystemScope } = usePermissions();
  const [pageTab, setPageTab] = useState<ApiKeysPageTab>('list');
  // issue #134：待审批 tab 门槛与 /key-requests 路由一致（read_channels，system 级）；
  // 无此 scope 的用户不渲染 Tab 栏，只见 Key 列表（不报错）
  const canViewApprovals = hasSystemScope('read_channels');

  return (
    <ApiKeysProvider>
      <Header fixed>
        <div className='flex flex-1 items-center justify-between'>
          <div>
            <h2 className='text-xl font-bold tracking-tight'>{t('apikeys.title')}</h2>
            <p className='text-sm text-muted-foreground'>{t('apikeys.description')}</p>
          </div>
          {/* ai4s 挂载（issue #64 批量换档入口，内部按 write_api_keys 权限自隐藏）；与既有按钮同组右对齐 */}
          <div className='flex items-center gap-2'>
            <ApiKeysPrimaryButtons />
            <Ai4sBatchTierDialog />
          </div>
        </div>
      </Header>

      <Main fixed>
        {canViewApprovals && <ApiKeysPageTabs value={pageTab} onChange={setPageTab} />}
        {canViewApprovals && pageTab === 'approvals' ? (
          // issue #134：内嵌审批表格区（复用 /key-requests 页同一份 Panel，保留 30s 轮询与审批操作）
          <div className='flex min-h-0 flex-1 flex-col overflow-auto'>
            <Ai4sKeyRequestsPanel />
          </div>
        ) : (
          <ApiKeysContent />
        )}
      </Main>
      <ApiKeysDialogs />
    </ApiKeysProvider>
  );
}
