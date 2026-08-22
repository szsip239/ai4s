/**
 * 员工「我的 Key」页（issue #74 列表 / issue #79 控制台申请通道）。
 * 背景：#68 收掉员工 key 自管权限后员工看不到自己名下的 key，也没有审批入口。
 * 本页列本人名下全部 key（名称/明文/状态/档位/创建时间）。
 * issue #81：明文对本人可见（/self/keys 服务端 userID=me.id 过滤下发），默认掩码展示，
 * 点「显示」查看、可复制——审批私信之外的查看兜底。
 * issue #79：两个按钮从指引对话框升级为真实发起——填用途/目标档提交 → shim 落待办申请并
 * 推管理员飞书审批卡，管理员在控制台审批页点批后自动执行；「我的申请」区展示本人
 * 申请的 pending/approved/rejected/expired 状态与结果（30s 轮询，与巡检节奏一致）。
 * issue #80：pending 行加「撤回」（确认后 POST cancel）——仅本人 pending 可撤，
 * 撤回置 canceled 并回执管理员（审批卡更新/群文本），状态新增 canceled 态。
 * 原飞书审批定义通道并存（飞书里直接提单仍可用，两通道共用执行体）。
 * 交付分流：飞书身份（email 为 ou_*@casdoor.oidc）→ 批准后明文私信本人；
 * 非飞书（本地/钉钉/企微账号）→ 明文私信管理员备付；两种身份都可在本页查看明文。
 * issue #82：页底内嵌配置指南折叠卡（KeyGuide）——接入地址/客户端示例/档位说明/FAQ，
 * 员工零 scope 可看；入口地址取 window.location.origin 自适应（tailnet 规范名/localhost）。
 * issue #83：每把 key 展示当前档用量（进度条+数字：cost 点/token/请求次数），可展开看各档
 * 用量与周期/重置时间（窗口边界为北京时间自然月，issue #83 B）；数据来自 /self/keys 内嵌
 * usage（shim 代查 apiKeyQuotaUsages，与管理员侧 profiles 对话框同源），只读。
 * issue #85：提额入口收窄——「申请提额」按本人 enabled key 当前最高档门控（已是高档/无
 * enabled key 禁用，提示走 Tooltip：disabled 按钮 pointer-events-none，原生 title 不可达，
 * 故包 span 作 trigger），弹窗选项只列秩次更高的档；档位秩次/门态/选项过滤抽在
 * tier-rank.ts 纯函数（与 shim alert_poller.TIER_RANK 双向同源），组件只接线。
 */
import { useEffect, useState } from 'react';
import { format } from 'date-fns';
import { IconChevronDown, IconEye, IconEyeOff, IconKey, IconPlus, IconTrendingUp } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { CopyButton } from '@/components/ui/copy-button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Header } from '@/components/layout/header';
import { Main } from '@/components/layout/main';
import { useMe } from '@/features/auth/data/auth';
import { KeyGuide } from './KeyGuide';
import { useCancelKeyRequest, useCreateKeyRequest, useMyKeyRequests, useMyKeys, type KeyRequest, type MyKey } from './api';
import { activeUsageEntry, formatCredits, formatTokenCount, quotaProgress, type UsageEntry } from './key-usage';
import { currentHighestTier, upgradeButtonBlock, upgradeOptions, type TierName } from './tier-rank';

type ApplyKind = 'new' | 'upgrade' | null;

function statusVariant(status: string): 'default' | 'secondary' | 'outline' {
  if (status === 'enabled') return 'default';
  if (status === 'disabled') return 'secondary';
  return 'outline';
}

function reqStatusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'approved') return 'default';
  if (status === 'pending') return 'secondary';
  if (status === 'rejected') return 'destructive';
  return 'outline';
}

/** issue #83：单档用量数字行——cost 点 / token / 请求次数，只显示配额非空的维度 */
function UsageNumbers({ entry, zh }: { entry: UsageEntry; zh: boolean }) {
  const { t } = useTranslation();
  const q = entry.quota ?? {};
  const u = entry.usage ?? {};
  const parts: string[] = [];
  if (q.cost != null) {
    parts.push(`${formatCredits(Number(u.totalCost ?? 0))} / ${formatCredits(Number(q.cost))} ${t('ai4s.myKeys.usage.credits')}`);
  }
  if (q.totalTokens != null) {
    parts.push(`${formatTokenCount(Number(u.totalTokens ?? 0), zh)} / ${formatTokenCount(Number(q.totalTokens), zh)} Token`);
  }
  parts.push(t('ai4s.myKeys.usage.requests', { count: Number(u.requestCount ?? 0) }));
  return <span>{parts.join(' · ')}</span>;
}

/** issue #83：各档用量展开明细（对齐上游 profiles 对话框粒度，只读；含周期与重置时间） */
function UsageDetailRows({ k }: { k: MyKey }) {
  const { t, i18n } = useTranslation();
  const zh = i18n.language.startsWith('zh');
  return (
    <TableRow>
      <TableCell colSpan={6} className='bg-muted/30'>
        <div className='space-y-4 py-1'>
          {(k.usage ?? []).map((e) => {
            const p = quotaProgress(e);
            return (
              <div key={e.profileName} className='space-y-1'>
                <div className='flex items-center gap-2 text-sm'>
                  <span className='font-medium'>{e.profileName}</span>
                  {e.profileName === k.profiles?.activeProfile && <Badge variant='secondary'>{t('ai4s.myKeys.usage.current')}</Badge>}
                  {!p && <span className='text-muted-foreground text-xs'>{t('ai4s.myKeys.usage.unlimited')}</span>}
                  {p && <span className='text-muted-foreground text-xs'>{Math.round(p.pct)}%</span>}
                </div>
                {p && <Progress value={Math.min(p.pct, 100)} className='h-1.5 max-w-72' />}
                <div className='text-muted-foreground text-xs'>
                  <UsageNumbers entry={e} zh={zh} />
                </div>
                {(e.window?.start || e.window?.end) && (
                  <div className='text-muted-foreground text-xs'>
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
      </TableCell>
    </TableRow>
  );
}

function KeyRow({ k }: { k: MyKey }) {
  const { t, i18n } = useTranslation();
  const zh = i18n.language.startsWith('zh');
  const [showKey, setShowKey] = useState(false);
  const [showUsage, setShowUsage] = useState(false);
  const activeProfile = k.profiles?.activeProfile;
  const tier = activeProfile || t('ai4s.myKeys.noTier');
  // 掩码：保前缀 ah- 与尾 4 位便于辨认，中间打码（issue #81 明文本人可见，默认不裸露）
  const masked = k.key ? `${k.key.slice(0, 3)}••••••••${k.key.slice(-4)}` : '—';
  const activeEntry = activeUsageEntry(k.usage, activeProfile);
  const progress = activeEntry ? quotaProgress(activeEntry) : null;
  const hasUsageEntries = (k.usage?.length ?? 0) > 0;
  return (
    <>
      <TableRow>
        <TableCell className='font-medium'>{k.name}</TableCell>
        <TableCell>
          {k.key ? (
            <span className='flex items-center gap-1'>
              <code className='text-xs'>{showKey ? k.key : masked}</code>
              <Button
                size='icon'
                variant='ghost'
                className='h-6 w-6'
                title={t(showKey ? 'ai4s.myKeys.key.hide' : 'ai4s.myKeys.key.show')}
                onClick={() => setShowKey((v) => !v)}
              >
                {showKey ? <IconEyeOff className='h-3.5 w-3.5' /> : <IconEye className='h-3.5 w-3.5' />}
              </Button>
              {/* 复制走仓库既有 CopyButton（useCopyToClipboard：成功/失败 toast + 已复制态翻转） */}
              <CopyButton content={k.key} />
            </span>
          ) : (
            '—'
          )}
        </TableCell>
        <TableCell>
          <Badge variant={statusVariant(k.status)}>{t(`ai4s.myKeys.status.${k.status}`, k.status)}</Badge>
        </TableCell>
        <TableCell>{tier}</TableCell>
        <TableCell>
          {/* issue #83 用量列：未挂档 —（档位列已显示「未挂档」）；usage=null 降级「暂不可用」 */}
          {!activeProfile ? (
            <span className='text-muted-foreground'>—</span>
          ) : k.usage == null ? (
            <span className='text-muted-foreground text-xs'>{t('ai4s.myKeys.usage.unavailable')}</span>
          ) : !activeEntry ? (
            <span className='text-muted-foreground'>—</span>
          ) : (
            <div className='flex items-center gap-1'>
              <div className='min-w-36 flex-1'>
                {progress ? (
                  <>
                    <div className='flex items-center gap-2'>
                      <Progress value={Math.min(progress.pct, 100)} className='h-1.5 flex-1' />
                      <span className='text-muted-foreground text-xs'>{Math.round(progress.pct)}%</span>
                    </div>
                    <div className='text-muted-foreground mt-1 text-xs'>
                      <UsageNumbers entry={activeEntry} zh={zh} />
                    </div>
                  </>
                ) : (
                  <span className='text-muted-foreground text-xs'>{t('ai4s.myKeys.usage.unlimited')}</span>
                )}
              </div>
              {hasUsageEntries && (
                <Button
                  size='icon'
                  variant='ghost'
                  className='h-6 w-6'
                  aria-label={t('ai4s.myKeys.usage.expand')}
                  onClick={() => setShowUsage((v) => !v)}
                >
                  <IconChevronDown className={cn('h-3.5 w-3.5 transition-transform', showUsage && 'rotate-180')} />
                </Button>
              )}
            </div>
          )}
        </TableCell>
        <TableCell className='text-muted-foreground'>{k.createdAt ? format(new Date(k.createdAt), 'yyyy-MM-dd HH:mm') : '—'}</TableCell>
      </TableRow>
      {showUsage && hasUsageEntries && <UsageDetailRows k={k} />}
    </>
  );
}

function RequestRow({ r, onCancel }: { r: KeyRequest; onCancel: (r: KeyRequest) => void }) {
  const { t } = useTranslation();
  return (
    <TableRow>
      <TableCell className='font-medium'>{t(`ai4s.myKeys.requests.kind.${r.kind}`)}</TableCell>
      <TableCell className='text-muted-foreground'>{r.kind === 'new' ? r.purpose : r.tier}</TableCell>
      <TableCell>
        <Badge variant={reqStatusVariant(r.status)}>{t(`ai4s.myKeys.requests.status.${r.status}`, r.status)}</Badge>
      </TableCell>
      <TableCell className='text-muted-foreground'>{r.createdAt ? format(new Date(r.createdAt), 'yyyy-MM-dd HH:mm') : '—'}</TableCell>
      <TableCell className='text-muted-foreground max-w-64 truncate' title={r.result || ''}>
        {r.result || '—'}
      </TableCell>
      <TableCell>
        {r.status === 'pending' && (
          <Button size='sm' variant='outline' onClick={() => onCancel(r)}>
            {t('ai4s.myKeys.requests.cancel')}
          </Button>
        )}
      </TableCell>
    </TableRow>
  );
}

export default function Ai4sMyKeysPage() {
  const { t } = useTranslation();
  const { data: me } = useMe();
  const myKeys = useMyKeys();
  const myRequests = useMyKeyRequests();
  const createRequest = useCreateKeyRequest();
  const cancelRequest = useCancelKeyRequest();
  const [applyKind, setApplyKind] = useState<ApplyKind>(null);
  const [cancelTarget, setCancelTarget] = useState<KeyRequest | null>(null);
  const [purpose, setPurpose] = useState('');
  const [tier, setTier] = useState('');

  // JIT/飞书绑定账号 email 形如 ou_*@casdoor.oidc；其余（如 user@example.com）为本地账号
  const isFeishuBound = (me?.email || '').endsWith('@casdoor.oidc');
  const keys = myKeys.data?.keys ?? [];
  const requests = myRequests.data?.requests ?? [];

  // issue #85 提额入口收窄：已是最高档/无 enabled key 时禁用按钮，提示走 Tooltip（评审 P1：
  // shadcn Button disabled:pointer-events-none，原生 title 永不显示也不可键盘 focus——
  // 禁用时包 span 作 TooltipTrigger）；数据未加载完成时不挡（门态只看实证数据；
  // shim 申请侧方向守卫兜底，API 直调绕不过）
  const upgradeBlock = myKeys.isSuccess ? upgradeButtonBlock(keys) : null;
  const currentTier = currentHighestTier(keys);
  const upgradeHint =
    upgradeBlock === 'maxed'
      ? t('ai4s.myKeys.upgradeBlockedMaxed')
      : upgradeBlock === 'no-enabled-key'
        ? t('ai4s.myKeys.upgradeBlockedNoKey')
        : undefined;

  // issue #85 评审 P2 边角：keys 到达/档位变化后，已选 tier 可能已被选项过滤掉（竞态）——
  // 置空让 placeholder 出现，不携带失效选项
  useEffect(() => {
    if (tier && !upgradeOptions(currentTier).includes(tier as TierName)) setTier('');
  }, [currentTier, tier]);

  const closeDialog = () => {
    setApplyKind(null);
    setPurpose('');
    setTier('');
  };

  const submit = () => {
    if (applyKind === null) return;
    createRequest.mutate(applyKind === 'new' ? { kind: 'new', purpose } : { kind: 'upgrade', tier }, {
      onSuccess: () => {
        toast.success(t('ai4s.myKeys.submitOk'));
        closeDialog();
      },
      onError: (e) => toast.error(e.message),
    });
  };

  const doCancel = () => {
    if (!cancelTarget) return;
    cancelRequest.mutate(cancelTarget.id, {
      onSuccess: () => {
        toast.success(t('ai4s.myKeys.requests.cancelOk'));
        setCancelTarget(null);
      },
      onError: (e) => toast.error(e.message),
    });
  };

  return (
    <>
      <Header />
      <Main>
        <Card>
          <CardHeader>
            <div className='flex flex-wrap items-start justify-between gap-3'>
              <div>
                <CardTitle className='flex items-center gap-2'>
                  <IconKey className='h-5 w-5' />
                  {t('ai4s.myKeys.title')}
                </CardTitle>
              </div>
              <div className='flex gap-2'>
                <Button size='sm' onClick={() => setApplyKind('new')}>
                  <IconPlus className='mr-1 h-4 w-4' />
                  {t('ai4s.myKeys.applyNew')}
                </Button>
                {upgradeBlock !== null ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span tabIndex={0} className='cursor-not-allowed'>
                        <Button size='sm' variant='outline' disabled>
                          <IconTrendingUp className='mr-1 h-4 w-4' />
                          {t('ai4s.myKeys.applyUpgrade')}
                        </Button>
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>{upgradeHint}</TooltipContent>
                  </Tooltip>
                ) : (
                  <Button size='sm' variant='outline' onClick={() => setApplyKind('upgrade')}>
                    <IconTrendingUp className='mr-1 h-4 w-4' />
                    {t('ai4s.myKeys.applyUpgrade')}
                  </Button>
                )}
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {myKeys.isError ? (
              <Alert variant='destructive'>
                <AlertDescription>{t('ai4s.myKeys.loadError')}</AlertDescription>
              </Alert>
            ) : myKeys.isLoading ? (
              <div className='text-muted-foreground text-sm'>{t('common.loading', '加载中…')}</div>
            ) : keys.length === 0 ? (
              <div className='py-10 text-center'>
                <p className='text-muted-foreground text-sm'>{t('ai4s.myKeys.empty')}</p>
                <Button className='mt-4' size='sm' onClick={() => setApplyKind('new')}>
                  <IconPlus className='mr-1 h-4 w-4' />
                  {t('ai4s.myKeys.applyNew')}
                </Button>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t('ai4s.myKeys.columns.name')}</TableHead>
                    <TableHead>{t('ai4s.myKeys.columns.key')}</TableHead>
                    <TableHead>{t('ai4s.myKeys.columns.status')}</TableHead>
                    <TableHead>{t('ai4s.myKeys.columns.tier')}</TableHead>
                    <TableHead>{t('ai4s.myKeys.columns.usage')}</TableHead>
                    <TableHead>{t('ai4s.myKeys.columns.createdAt')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {keys.map((k) => (
                    <KeyRow key={k.id} k={k} />
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card className='mt-4'>
          <CardHeader>
            <CardTitle>{t('ai4s.myKeys.requests.title')}</CardTitle>
            <CardDescription>{t('ai4s.myKeys.requests.description')}</CardDescription>
          </CardHeader>
          <CardContent>
            {requests.length === 0 ? (
              <p className='text-muted-foreground py-6 text-center text-sm'>{t('ai4s.myKeys.requests.empty')}</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t('ai4s.myKeys.requests.columns.kind')}</TableHead>
                    <TableHead>{t('ai4s.myKeys.requests.columns.detail')}</TableHead>
                    <TableHead>{t('ai4s.myKeys.requests.columns.status')}</TableHead>
                    <TableHead>{t('ai4s.myKeys.requests.columns.createdAt')}</TableHead>
                    <TableHead>{t('ai4s.myKeys.requests.columns.result')}</TableHead>
                    <TableHead>{t('ai4s.myKeys.requests.columns.actions')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {requests.map((r) => (
                    <RequestRow key={r.id} r={r} onCancel={setCancelTarget} />
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* issue #82：员工侧配置指南折叠卡（接入地址/示例/档位/FAQ） */}
        <KeyGuide />

        <Dialog open={applyKind !== null} onOpenChange={(open) => !open && closeDialog()}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{applyKind === 'new' ? t('ai4s.myKeys.dialog.newTitle') : t('ai4s.myKeys.dialog.upgradeTitle')}</DialogTitle>
              <DialogDescription>
                {applyKind === 'new' ? t('ai4s.myKeys.dialog.newDesc') : t('ai4s.myKeys.dialog.upgradeDesc')}
              </DialogDescription>
            </DialogHeader>
            <div className='space-y-3'>
              {applyKind === 'new' ? (
                <Textarea
                  placeholder={t('ai4s.myKeys.dialog.purposePlaceholder')}
                  value={purpose}
                  onChange={(e) => setPurpose(e.target.value)}
                  rows={3}
                  maxLength={200}
                />
              ) : (
                <Select value={tier} onValueChange={setTier}>
                  <SelectTrigger>
                    <SelectValue placeholder={t('ai4s.myKeys.dialog.tierPlaceholder')} />
                  </SelectTrigger>
                  <SelectContent>
                    {/* issue #85：只列秩次 > 当前档的选项（标准档用户只见高档） */}
                    {upgradeOptions(currentTier).map((opt) => (
                      <SelectItem key={opt} value={opt}>
                        {t(opt === '高档' ? 'ai4s.myKeys.dialog.tierPremium' : 'ai4s.myKeys.dialog.tierStandard')}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              <Alert>
                <AlertDescription>
                  {isFeishuBound ? t('ai4s.myKeys.dialog.deliverFeishu') : t('ai4s.myKeys.dialog.deliverNonFeishu')}
                </AlertDescription>
              </Alert>
            </div>
            <DialogFooter>
              <Button variant='outline' onClick={closeDialog}>
                {t('common.cancel', '取消')}
              </Button>
              <Button onClick={submit} disabled={createRequest.isPending || (applyKind === 'new' ? !purpose.trim() : !tier)}>
                {t('ai4s.myKeys.dialog.submit')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <AlertDialog open={cancelTarget !== null} onOpenChange={(open) => !open && setCancelTarget(null)}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t('ai4s.myKeys.requests.cancelConfirmTitle')}</AlertDialogTitle>
              <AlertDialogDescription>
                {t('ai4s.myKeys.requests.cancelConfirmDesc')}
                {cancelTarget && (
                  <span className='mt-2 block'>
                    {t(`ai4s.myKeys.requests.kind.${cancelTarget.kind}`)} ·{' '}
                    {cancelTarget.kind === 'new' ? cancelTarget.purpose : cancelTarget.tier}
                  </span>
                )}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>{t('common.cancel', '取消')}</AlertDialogCancel>
              <AlertDialogAction disabled={cancelRequest.isPending} onClick={doCancel}>
                {t('ai4s.myKeys.requests.cancel')}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </Main>
    </>
  );
}
