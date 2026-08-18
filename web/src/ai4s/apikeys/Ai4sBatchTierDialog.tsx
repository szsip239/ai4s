import { useMemo, useState } from 'react';
import { IconRefresh, IconSwitchHorizontal } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ScrollArea } from '@/components/ui/scroll-area';
import { usePermissions } from '@/hooks/usePermissions';
import { useSelectedProjectId } from '@/stores/projectStore';
import { useProjects } from '@/features/projects/data/projects';
import { useUsers } from '@/features/users/data/users';

import { NO_PROFILE, collectActiveProfiles, filterBatchTierKeys, templateToProfileInput } from './batch-tier';
import {
  type BatchTierResult,
  executeBatchTierChange,
  useBatchTierKeys,
  useBatchTierTemplates,
  useInvalidateAfterBatchTier,
} from './batch-tier-data';

/**
 * 批量配额换档入口（issue #64）：key 管理页「批量换档」按钮 + 对话框。
 * 流程：筛选（项目/员工/当前档）→ 预览命中 key（名称/员工/当前档）→ 选目标档 →
 * 确认执行 → 逐条成功/失败回报。挂载在 vendor apikeys 页头部（MOUNTPOINTS 已登记）。
 */

/** 「全部」筛选哨兵（空串不便做 SelectItem value） */
const FILTER_ALL = '__all__';

function displayUser(user?: { firstName?: string | null; lastName?: string | null; email?: string | null } | null) {
  if (!user) return '—';
  const name = [user.lastName, user.firstName].filter(Boolean).join('');
  return name || user.email || '—';
}

function quotaSummary(tpl: { profile?: { quota?: { requests?: number | null; totalTokens?: number | null; cost?: number | string | null; period?: { calendarDuration?: { unit: string } | null } | null } | null } | null }) {
  const q = tpl.profile?.quota;
  if (!q) return '';
  const parts: string[] = [];
  if (q.requests != null) parts.push(`${q.requests} 次`);
  if (q.totalTokens != null) parts.push(`${q.totalTokens} tokens`);
  if (q.cost != null) parts.push(`$${q.cost}`);
  const unit = q.period?.calendarDuration?.unit === 'day' ? '日' : '月';
  return parts.length > 0 ? `${parts.join(' / ')} 每${unit}` : '';
}

export function Ai4sBatchTierDialog() {
  const { t } = useTranslation();
  const { apiKeyPermissions } = usePermissions();
  const selectedProjectId = useSelectedProjectId();

  const [open, setOpen] = useState(false);
  const [projectId, setProjectId] = useState<string>(FILTER_ALL);
  const [userId, setUserId] = useState<string>(FILTER_ALL);
  const [tierFilter, setTierFilter] = useState<string>(FILTER_ALL);
  const [targetTemplateId, setTargetTemplateId] = useState<string>('');
  const [executing, setExecuting] = useState(false);
  const [results, setResults] = useState<BatchTierResult[] | null>(null);

  // 项目筛选默认落当前选中项目（控制台 key 页本身是项目域）
  const effectiveProjectId = projectId === FILTER_ALL ? (selectedProjectId ?? undefined) : projectId;

  const { data: projects } = useProjects({ first: 100 });
  const { data: users } = useUsers({ first: 200 });
  const { data: keys, isLoading: keysLoading } = useBatchTierKeys(effectiveProjectId);
  const { data: templates } = useBatchTierTemplates(effectiveProjectId);
  const invalidate = useInvalidateAfterBatchTier();

  const tierOptions = useMemo(() => collectActiveProfiles(keys ?? []), [keys]);

  const matched = useMemo(
    () =>
      filterBatchTierKeys(keys ?? [], {
        projectId: effectiveProjectId,
        userId: userId === FILTER_ALL ? undefined : userId,
        activeProfile: tierFilter === FILTER_ALL ? undefined : tierFilter,
      }),
    [keys, effectiveProjectId, userId, tierFilter]
  );

  const targetTemplate = useMemo(
    () => (templates ?? []).find((tpl) => tpl.id === targetTemplateId),
    [templates, targetTemplateId]
  );

  const userNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const u of users?.edges ?? []) {
      map.set(u.node.id, displayUser(u.node));
    }
    return map;
  }, [users]);

  const keyUserName = (key: { userID: string; user?: { firstName?: string | null; lastName?: string | null; email?: string | null } | null }) =>
    displayUser(key.user) !== '—' ? displayUser(key.user) : (userNameById.get(key.userID) ?? '—');

  const resetAndClose = (next: boolean) => {
    setOpen(next);
    if (!next) {
      setResults(null);
      setTargetTemplateId('');
      setTierFilter(FILTER_ALL);
      setUserId(FILTER_ALL);
      setProjectId(FILTER_ALL);
    }
  };

  const handleExecute = async () => {
    if (!targetTemplate || matched.length === 0) return;
    setExecuting(true);
    try {
      const profileInput = templateToProfileInput(targetTemplate);
      const r = await executeBatchTierChange(matched, targetTemplate, profileInput, effectiveProjectId);
      setResults(r);
      invalidate(r.map((x) => x.id));
    } finally {
      setExecuting(false);
    }
  };

  if (!apiKeyPermissions.canWrite) {
    return null;
  }

  const successCount = results?.filter((r) => r.ok).length ?? 0;

  return (
    <>
      <Button variant='outline' size='sm' onClick={() => setOpen(true)}>
        <IconSwitchHorizontal className='mr-2 h-4 w-4' />
        {t('ai4s.batchTier.button')}
      </Button>
      <Dialog open={open} onOpenChange={resetAndClose}>
        <DialogContent className='max-w-2xl'>
          <DialogHeader>
            <DialogTitle>{t('ai4s.batchTier.title')}</DialogTitle>
            <DialogDescription>{t('ai4s.batchTier.description')}</DialogDescription>
          </DialogHeader>

          {results === null ? (
            <>
              {/* 筛选：项目 / 员工 / 当前档 */}
              <div className='grid grid-cols-3 gap-3'>
                <div className='space-y-1.5'>
                  <div className='text-muted-foreground text-xs'>{t('ai4s.batchTier.filterProject')}</div>
                  <Select value={projectId} onValueChange={setProjectId}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={FILTER_ALL}>{t('ai4s.batchTier.currentProject')}</SelectItem>
                      {(projects?.edges ?? []).map((e) => (
                        <SelectItem key={e.node.id} value={e.node.id}>{e.node.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className='space-y-1.5'>
                  <div className='text-muted-foreground text-xs'>{t('ai4s.batchTier.filterUser')}</div>
                  <Select value={userId} onValueChange={setUserId}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={FILTER_ALL}>{t('ai4s.batchTier.all')}</SelectItem>
                      {(users?.edges ?? []).map((e) => (
                        <SelectItem key={e.node.id} value={e.node.id}>{displayUser(e.node)}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className='space-y-1.5'>
                  <div className='text-muted-foreground text-xs'>{t('ai4s.batchTier.filterTier')}</div>
                  <Select value={tierFilter} onValueChange={setTierFilter}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value={FILTER_ALL}>{t('ai4s.batchTier.all')}</SelectItem>
                      {tierOptions.map((tier) => (
                        <SelectItem key={tier} value={tier}>
                          {tier === NO_PROFILE ? t('ai4s.batchTier.noProfile') : tier}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              {/* 预览命中列表 */}
              <div className='space-y-1.5'>
                <div className='text-muted-foreground text-xs'>
                  {t('ai4s.batchTier.preview', { count: matched.length })}
                </div>
                <ScrollArea className='h-44 rounded-md border'>
                  {keysLoading ? (
                    <div className='text-muted-foreground p-3 text-sm'>{t('common.loading')}</div>
                  ) : matched.length === 0 ? (
                    <div className='text-muted-foreground p-3 text-sm'>{t('ai4s.batchTier.previewEmpty')}</div>
                  ) : (
                    <table className='w-full text-sm'>
                      <thead>
                        <tr className='text-muted-foreground border-b text-left text-xs'>
                          <th className='px-3 py-2'>{t('ai4s.batchTier.colName')}</th>
                          <th className='px-3 py-2'>{t('ai4s.batchTier.colUser')}</th>
                          <th className='px-3 py-2'>{t('ai4s.batchTier.colTier')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {matched.map((key) => (
                          <tr key={key.id} className='border-b last:border-0'>
                            <td className='px-3 py-1.5'>{key.name}</td>
                            <td className='px-3 py-1.5'>{keyUserName(key)}</td>
                            <td className='px-3 py-1.5'>
                              {key.profiles?.activeProfile || t('ai4s.batchTier.noProfile')}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </ScrollArea>
              </div>

              {/* 目标档 */}
              <div className='space-y-1.5'>
                <div className='text-muted-foreground text-xs'>{t('ai4s.batchTier.targetTier')}</div>
                <Select value={targetTemplateId} onValueChange={setTargetTemplateId}>
                  <SelectTrigger><SelectValue placeholder={t('ai4s.batchTier.targetPlaceholder')} /></SelectTrigger>
                  <SelectContent>
                    {(templates ?? []).map((tpl) => (
                      <SelectItem key={tpl.id} value={tpl.id}>
                        {tpl.name}
                        {quotaSummary(tpl) ? `（${quotaSummary(tpl)}）` : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <DialogFooter>
                <Button variant='outline' onClick={() => resetAndClose(false)}>{t('common.cancel')}</Button>
                <Button onClick={handleExecute} disabled={executing || matched.length === 0 || !targetTemplate}>
                  {executing && <IconRefresh className='mr-2 h-4 w-4 animate-spin' />}
                  {t('ai4s.batchTier.confirm', { count: matched.length })}
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              {/* 逐条回报 */}
              <div className='text-sm'>
                {t('ai4s.batchTier.resultSummary', { success: successCount, fail: results.length - successCount })}
              </div>
              <ScrollArea className='h-56 rounded-md border'>
                <table className='w-full text-sm'>
                  <tbody>
                    {results.map((r) => (
                      <tr key={r.id} className='border-b last:border-0'>
                        <td className='px-3 py-1.5'>{r.name}</td>
                        <td className='px-3 py-1.5'>
                          {r.ok ? (
                            <span className='text-green-600'>{t('ai4s.batchTier.resultOk')}</span>
                          ) : (
                            <span className='text-destructive'>{t('ai4s.batchTier.resultFail', { reason: r.error })}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ScrollArea>
              <DialogFooter>
                <Button onClick={() => resetAndClose(false)}>{t('common.close')}</Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
