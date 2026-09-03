import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useApiKeyTokenUsageStats } from '../data/apikeys';
import { ApiKeyTokenUsageView } from './api-key-token-usage-view';
import type { ApiKey } from '../data/schema';

type TimeRange = 'today' | 'last7days' | 'all';

interface ApiKeyTokenChartDialogProps {
  apiKey: ApiKey | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ApiKeyTokenChartDialog({ apiKey, open, onOpenChange }: ApiKeyTokenChartDialogProps) {
  const { t } = useTranslation();
  const [timeRange, setTimeRange] = useState<TimeRange>('today');

  const usageDateRangeWhere = useMemo(() => {
    const getDateRange = (range: TimeRange) => {
      const now = new Date();

      switch (range) {
        case 'today': {
          // Get start of today in local timezone
          const todayLocal = new Date(now.getFullYear(), now.getMonth(), now.getDate());
          return {
            createdAtGTE: todayLocal.toISOString(),
            createdAtLTE: now.toISOString(),
          };
        }
        case 'last7days': {
          // Get 7 days ago from start of today in local timezone
          const todayLocal = new Date(now.getFullYear(), now.getMonth(), now.getDate());
          const last7daysLocal = new Date(todayLocal);
          last7daysLocal.setDate(last7daysLocal.getDate() - 7);
          return {
            createdAtGTE: last7daysLocal.toISOString(),
            createdAtLTE: now.toISOString(),
          };
        }
        case 'all':
          return {};
        default:
          return {};
      }
    };

    return getDateRange(timeRange);
  }, [timeRange]);

  const { data: usageStats, isLoading, isFetching } = useApiKeyTokenUsageStats(
    apiKey
      ? {
          apiKeyIds: [apiKey.id],
          ...usageDateRangeWhere,
        }
      : undefined,
    {
      enabled: open && !!apiKey,
    }
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] flex flex-col">
        <DialogHeader className="flex flex-col space-y-3 sm:flex-row sm:items-center sm:justify-between sm:space-y-0">
          <DialogTitle className="text-base sm:text-lg">
            {t('apikeys.tokenUsageChart.title')} - {apiKey?.name}
          </DialogTitle>
          <Tabs value={timeRange} onValueChange={(value) => setTimeRange(value as TimeRange)}>
            <TabsList className="grid w-full grid-cols-3 sm:w-auto sm:mr-6">
              <TabsTrigger value="today">{t('apikeys.tokenUsageChart.today')}</TabsTrigger>
              <TabsTrigger value="last7days">{t('apikeys.tokenUsageChart.last7days')}</TabsTrigger>
              <TabsTrigger value="all">{t('apikeys.tokenUsageChart.all')}</TabsTrigger>
            </TabsList>
          </Tabs>
        </DialogHeader>
        <div className="space-y-2 overflow-y-auto flex-1 min-h-0 scrollbar-thin -ml-6 pl-6">
          <ApiKeyTokenUsageView stat={usageStats?.[0]} isLoading={isLoading} isFetching={isFetching} />
        </div>
      </DialogContent>
    </Dialog>
  );
}
