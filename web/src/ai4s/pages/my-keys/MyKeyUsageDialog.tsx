/**
 * 员工「我的 Key」用量弹窗（2026-09-03 owner 反馈）：对齐管理端 Key 管理的「token 使用」
 * 小窗口形态——原行内展开（UsageDetailRows）收进 Dialog，顶层两页：
 *   tokens：token 用量统计（复用管理端 ApiKeyTokenUsageView 展示主体，数据走 shim
 *           /self/key-usage-stats 代查同一上游 apiKeyTokenUsageStats，窗口 day/month/all
 *           服务端按本地时区算）；
 *   quota ：各档配额明细（进度/周期/重置时间，原展开行 quota 页内容平移）。
 * 未挂档 key 也可开弹窗看 tokens 页（shim 属主闸门只认 key 归属，不依赖挂档）。
 */
import { useState } from 'react';
import { format } from 'date-fns';
import { useTranslation } from 'react-i18next';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Progress } from '@/components/ui/progress';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ApiKeyTokenUsageView } from '@/features/apikeys/components/api-key-token-usage-view';
import { useSelectedProjectId } from '@/stores/projectStore';
import { useKeyUsageStats, type MyKey } from './api';
import { formatCredits, formatTokenCount, quotaProgress, type UsageEntry, type UsageWindow } from './key-usage';

/** 单档用量数字行——cost 点 / token / 请求次数；配额非空的维度显示「已用 / 上限」，
 * 配额为空（不设限）但有实际用量时显示「已用 X」（本组件同时供 my-keys 列表行内用量列复用） */
export function UsageNumbers({ entry, zh }: { entry: UsageEntry; zh: boolean }) {
  const { t } = useTranslation();
  const q = entry.quota ?? {};
  const u = entry.usage ?? {};
  const parts: string[] = [];
  if (q.cost != null) {
    parts.push(`${formatCredits(Number(u.totalCost ?? 0))} / ${formatCredits(Number(q.cost))} ${t('ai4s.myKeys.usage.credits')}`);
  } else if (u.totalCost != null && Number(u.totalCost) > 0) {
    parts.push(`${formatCredits(Number(u.totalCost))} ${t('ai4s.myKeys.usage.credits')}`);
  }
  if (q.totalTokens != null) {
    parts.push(`${formatTokenCount(Number(u.totalTokens ?? 0), zh)} / ${formatTokenCount(Number(q.totalTokens), zh)} Token`);
  } else if (u.totalTokens != null && Number(u.totalTokens) > 0) {
    parts.push(`${formatTokenCount(Number(u.totalTokens), zh)} Token`);
  }
  parts.push(t('ai4s.myKeys.usage.requests', { count: Number(u.requestCount ?? 0) }));
  return <span>{parts.join(' · ')}</span>;
}

/** 各档配额明细（原 UsageDetailRows 的 quota 页，只读） */
function QuotaUsage({ k }: { k: MyKey }) {
  const { t, i18n } = useTranslation();
  const zh = i18n.language.startsWith('zh');
  return (
    <div className="space-y-4 py-1">
      {(k.usage ?? []).length === 0 && (
        <div className="text-muted-foreground py-1 text-xs">{t('ai4s.myKeys.usage.noQuotaEntries')}</div>
      )}
      {(k.usage ?? []).map((e) => {
        const p = quotaProgress(e);
        return (
          <div key={e.profileName} className="space-y-1">
            <div className="flex items-center gap-2 text-sm">
              <span className="font-medium">{e.profileName}</span>
              {e.profileName === k.profiles?.activeProfile && <Badge variant="secondary">{t('ai4s.myKeys.usage.current')}</Badge>}
              {!p && <span className="text-muted-foreground text-xs">{t('ai4s.myKeys.usage.unlimited')}</span>}
              {p && <span className="text-muted-foreground text-xs">{Math.round(p.pct)}%</span>}
            </div>
            {p && <Progress value={Math.min(p.pct, 100)} className="h-1.5 max-w-72" />}
            <div className="text-muted-foreground text-xs">
              <UsageNumbers entry={e} zh={zh} />
            </div>
            {(e.window?.start || e.window?.end) && (
              <div className="text-muted-foreground text-xs">
                {t('ai4s.myKeys.usage.period')}: {e.window?.start ? format(new Date(e.window.start), 'yyyy-MM-dd HH:mm') : '—'} →{' '}
                {e.window?.end ? format(new Date(e.window.end), 'yyyy-MM-dd HH:mm') : '—'}
                {e.window?.end && (
                  <>
                    {' '}
                    · {t('ai4s.myKeys.usage.resetAt')}: {format(new Date(e.window.end), 'yyyy-MM-dd HH:mm')}
                  </>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** token 用量统计页：窗口切换（今天/本月/累计）+ 共享展示主体 */
function TokenUsage({ keyId }: { keyId: string }) {
  const { t } = useTranslation();
  const [win, setWin] = useState<UsageWindow>('day');
  const projectId = useSelectedProjectId();
  const { data, isLoading, isFetching, isError } = useKeyUsageStats(keyId, win, projectId);
  return (
    <div className="space-y-3">
      <Tabs value={win} onValueChange={(v) => setWin(v as UsageWindow)}>
        <TabsList className="h-8">
          <TabsTrigger value="day" className="text-xs">{t('ai4s.myKeys.usage.window.day')}</TabsTrigger>
          <TabsTrigger value="month" className="text-xs">{t('ai4s.myKeys.usage.window.month')}</TabsTrigger>
          <TabsTrigger value="all" className="text-xs">{t('ai4s.myKeys.usage.window.all')}</TabsTrigger>
        </TabsList>
      </Tabs>
      {isError ? (
        <div className="flex h-[200px] items-center justify-center text-muted-foreground">
          {t('ai4s.myKeys.usage.unavailable')}
        </div>
      ) : (
        <ApiKeyTokenUsageView stat={data?.stats} isLoading={isLoading} isFetching={isFetching} />
      )}
    </div>
  );
}

interface MyKeyUsageDialogProps {
  k: MyKey | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function MyKeyUsageDialog({ k, open, onOpenChange }: MyKeyUsageDialogProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<'tokens' | 'quota'>('tokens');
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] flex flex-col">
        <DialogHeader className="flex flex-col space-y-3 sm:flex-row sm:items-center sm:justify-between sm:space-y-0">
          <DialogTitle className="text-base sm:text-lg">
            {t('apikeys.tokenUsageChart.title')} - {k?.name}
          </DialogTitle>
          <Tabs value={tab} onValueChange={(v) => setTab(v as 'tokens' | 'quota')}>
            <TabsList className="grid w-full grid-cols-2 sm:w-auto sm:mr-6">
              <TabsTrigger value="tokens">{t('ai4s.myKeys.usage.tab.tokens')}</TabsTrigger>
              <TabsTrigger value="quota">{t('ai4s.myKeys.usage.window.quota')}</TabsTrigger>
            </TabsList>
          </Tabs>
        </DialogHeader>
        <div className="space-y-2 overflow-y-auto flex-1 min-h-0 scrollbar-thin -ml-6 pl-6">
          {k && (tab === 'tokens' ? <TokenUsage keyId={k.id} /> : <QuotaUsage k={k} />)}
        </div>
      </DialogContent>
    </Dialog>
  );
}
