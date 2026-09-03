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
 * issue #83：每把 key 展示当前档用量（进度条+数字：cost 点/token/请求次数）；2026-09-03 起
 * 用量明细从行内展开改为弹窗（MyKeyUsageDialog，对齐管理端「token 使用」小窗口形态——
 * tokens 页复用管理端展示主体 ApiKeyTokenUsageView，数据走 shim /self/key-usage-stats；
 * quota 页为各档配额/周期/重置时间），未挂档 key 也可开弹窗看 token 用量。
 * issue #85：提额入口收窄——「申请提额」按本人 enabled key 当前最高档门控（已是高档/无
 * enabled key 禁用，提示走 Tooltip：disabled 按钮 pointer-events-none，原生 title 不可达，
 * 故包 span 作 trigger），弹窗选项只列秩次更高的档；档位秩次/门态/选项过滤抽在
 * tier-rank.ts 纯函数（与 shim alert_poller.TIER_RANK 双向同源），组件只接线。
 * issue #86：提额按 Key 勾选——弹窗内列 enabled key 复选（默认全选，名称+当前档），
 * 选项按所选 Key 的最低档过滤，提交带 keyIds；申请列表详情列展示所选 Key
 * （upgradeDetailLabel：名称快照优先，fail-open 回退 id，存量申请回退目标档）。
 * 评审 P1-1：按钮门态 maxed 收窄为「全部 enabled key 均高档」（混档可开弹窗只勾低档提档）。
 * issue #89：多项目隔离——本页跟随顶部项目切换器（useSelectedProjectId）：查询/提交带
 * X-Project-ID 头按项目过滤；未选项目（零项目态）时内容区空态引导、不发请求、禁用申请按钮。
 */
import { useEffect, useMemo, useState } from 'react';
import { format } from 'date-fns';
import { IconChartBar, IconEye, IconEyeOff, IconKey, IconPlus, IconTrendingUp } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { useSelectedProjectId } from '@/stores/projectStore';
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
import { Checkbox } from '@/components/ui/checkbox';
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
import {
  useCancelKeyRequest,
  useCreateKeyRequest,
  useMyKeyRequests,
  useMyKeys,
  upgradeDetailLabel,
  type KeyRequest,
  type MyKey,
} from './api';
import { MyKeyUsageDialog, UsageNumbers } from './MyKeyUsageDialog';
import {
  activeUsageEntry,
  quotaProgress,
} from './key-usage';
import { upgradeButtonBlock, upgradeOptions, type TierName } from './tier-rank';

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

function KeyRow({ k }: { k: MyKey }) {
  const { t, i18n } = useTranslation();
  const zh = i18n.language.startsWith('zh');
  const [showKey, setShowKey] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);
  const activeProfile = k.profiles?.activeProfile;
  const tier = activeProfile || t('ai4s.myKeys.noTier');
  // 掩码：保前缀 ah- 与尾 4 位便于辨认，中间打码（issue #81 明文本人可见，默认不裸露）
  const masked = k.key ? `${k.key.slice(0, 3)}••••••••${k.key.slice(-4)}` : '—';
  const activeEntry = activeUsageEntry(k.usage, activeProfile);
  const progress = activeEntry ? quotaProgress(activeEntry) : null;
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
          {/* issue #83 用量列：未挂档 —（档位列已显示「未挂档」）；usage=null 降级「暂不可用」；
              不设限（挂档但无配额条目/配额全空）显示标注。2026-09-03 起明细改弹窗（MyKeyUsageDialog），
              按钮全状态可开——tokens 页走 shim 代查不依赖挂档，quota 页空态降级 */}
          <div className='flex items-center gap-1'>
            <div className='min-w-36 flex-1'>
              {!activeProfile ? (
                <span className='text-muted-foreground'>—</span>
              ) : k.usage == null ? (
                <span className='text-muted-foreground text-xs'>{t('ai4s.myKeys.usage.unavailable')}</span>
              ) : activeEntry && progress ? (
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
                <div>
                  <span className='text-muted-foreground text-xs'>{t('ai4s.myKeys.usage.unlimited')}</span>
                  {activeEntry && (
                    <div className='text-muted-foreground mt-1 text-xs'>
                      <UsageNumbers entry={activeEntry} zh={zh} />
                    </div>
                  )}
                </div>
              )}
            </div>
            <Button
              size='icon'
              variant='ghost'
              className='h-6 w-6'
              aria-label={t('ai4s.myKeys.usage.expand')}
              onClick={() => setUsageOpen(true)}
            >
              <IconChartBar className='h-3.5 w-3.5' />
            </Button>
          </div>
        </TableCell>
        <TableCell className='text-muted-foreground'>{k.createdAt ? format(new Date(k.createdAt), 'yyyy-MM-dd HH:mm') : '—'}</TableCell>
      </TableRow>
      {usageOpen && <MyKeyUsageDialog k={k} open={usageOpen} onOpenChange={setUsageOpen} />}
    </>
  );
}

function RequestRow({ r, onCancel }: { r: KeyRequest; onCancel: (r: KeyRequest) => void }) {
  const { t } = useTranslation();
  return (
    <TableRow>
      <TableCell className='font-medium'>{t(`ai4s.myKeys.requests.kind.${r.kind}`)}</TableCell>
      <TableCell className='text-muted-foreground'>
        {/* issue #86：提额申请显示所选 Key（名称快照优先，fail-open 回退 id）；存量申请回退只显示目标档 */}
        {r.kind === 'new' ? r.purpose : (upgradeDetailLabel(r) ?? r.tier)}
      </TableCell>
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
  const projectId = useSelectedProjectId(); // issue #89：页面数据跟随顶部项目切换器
  const myKeys = useMyKeys(projectId);
  const myRequests = useMyKeyRequests(projectId);
  const createRequest = useCreateKeyRequest(projectId);
  const cancelRequest = useCancelKeyRequest();
  const [applyKind, setApplyKind] = useState<ApplyKind>(null);
  const [cancelTarget, setCancelTarget] = useState<KeyRequest | null>(null);
  const [purpose, setPurpose] = useState('');
  const [tier, setTier] = useState('');
  // issue #86：提额按 Key 勾选——弹窗打开时默认全选 enabled key
  const [selectedKeyIds, setSelectedKeyIds] = useState<string[]>([]);

  // JIT/飞书绑定账号 email 形如 ou_*@casdoor.oidc；其余（如 user@example.com）为本地账号
  const isFeishuBound = (me?.email || '').endsWith('@casdoor.oidc');
  const keys = myKeys.data?.keys ?? [];
  const requests = myRequests.data?.requests ?? [];

  // issue #85 提额入口收窄：已是最高档/无 enabled key 时禁用按钮，提示走 Tooltip（评审 P1：
  // shadcn Button disabled:pointer-events-none，原生 title 永不显示也不可键盘 focus——
  // 禁用时包 span 作 TooltipTrigger）；数据未加载完成时不挡（门态只看实证数据；
  // shim 申请侧方向守卫兜底，API 直调绕不过）
  const upgradeBlock = myKeys.isSuccess ? upgradeButtonBlock(keys) : null;
  // issue #86：所选 Key 对象列表（保持 keys 顺序）；选项按所选最低档过滤。
  // 评审 P2-1：useMemo 稳定引用，避免每渲染新数组导致下方校正 effect 空跑
  const selectedKeys = useMemo(() => keys.filter((k) => selectedKeyIds.includes(k.id)), [keys, selectedKeyIds]);
  const options = useMemo(() => upgradeOptions(selectedKeys), [selectedKeys]);
  const upgradeHint =
    upgradeBlock === 'maxed'
      ? t('ai4s.myKeys.upgradeBlockedMaxed')
      : upgradeBlock === 'no-enabled-key'
        ? t('ai4s.myKeys.upgradeBlockedNoKey')
        : undefined;

  const openDialog = (kind: Exclude<ApplyKind, null>) => {
    setApplyKind(kind);
    if (kind === 'upgrade') {
      setSelectedKeyIds(keys.filter((k) => k.status === 'enabled').map((k) => k.id));
    }
  };

  const toggleKey = (id: string, checked: boolean) => {
    setSelectedKeyIds((prev) => (checked ? [...prev, id] : prev.filter((x) => x !== id)));
  };

  // issue #85 评审 P2 边角：keys 到达/档位变化后，已选 tier 可能已被选项过滤掉（竞态）——
  // 置空让 placeholder 出现，不携带失效选项；issue #86 起选项随所选 Key 变化，勾选变化同样校正
  useEffect(() => {
    if (tier && !options.includes(tier as TierName)) setTier('');
  }, [options, tier]);

  const closeDialog = () => {
    setApplyKind(null);
    setPurpose('');
    setTier('');
    setSelectedKeyIds([]);
  };

  const submit = () => {
    if (applyKind === null || !projectId) return; // issue #89：无项目上下文不提交（shim 侧同形 400 兜底）
    createRequest.mutate(applyKind === 'new' ? { kind: 'new', purpose } : { kind: 'upgrade', tier, keyIds: selectedKeyIds }, {
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
                <Button size='sm' disabled={!projectId} onClick={() => openDialog('new')}>
                  <IconPlus className='mr-1 h-4 w-4' />
                  {t('ai4s.myKeys.applyNew')}
                </Button>
                {upgradeBlock !== null || !projectId ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span tabIndex={0} className='cursor-not-allowed'>
                        <Button size='sm' variant='outline' disabled>
                          <IconTrendingUp className='mr-1 h-4 w-4' />
                          {t('ai4s.myKeys.applyUpgrade')}
                        </Button>
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>{!projectId ? t('ai4s.myKeys.noProject') : upgradeHint}</TooltipContent>
                  </Tooltip>
                ) : (
                  <Button size='sm' variant='outline' onClick={() => openDialog('upgrade')}>
                    <IconTrendingUp className='mr-1 h-4 w-4' />
                    {t('ai4s.myKeys.applyUpgrade')}
                  </Button>
                )}
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {!projectId ? (
              <div className='py-10 text-center'>
                <p className='text-muted-foreground text-sm'>{t('ai4s.myKeys.noProject')}</p>
              </div>
            ) : myKeys.isError ? (
              <Alert variant='destructive'>
                <AlertDescription>{t('ai4s.myKeys.loadError')}</AlertDescription>
              </Alert>
            ) : myKeys.isLoading ? (
              <div className='text-muted-foreground text-sm'>{t('common.loading', '加载中…')}</div>
            ) : keys.length === 0 ? (
              <div className='py-10 text-center'>
                <p className='text-muted-foreground text-sm'>{t('ai4s.myKeys.empty')}</p>
                <Button className='mt-4' size='sm' onClick={() => openDialog('new')}>
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
            {!projectId ? (
              <p className='text-muted-foreground py-6 text-center text-sm'>{t('ai4s.myKeys.noProject')}</p>
            ) : requests.length === 0 ? (
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
                <>
                  {/* issue #86：目标 Key 复选（默认全选 enabled，名称+当前档 badge） */}
                  <div className='space-y-1'>
                    <div className='text-sm font-medium'>{t('ai4s.myKeys.dialog.targetKeys')}</div>
                    <div className='max-h-40 space-y-2 overflow-y-auto rounded-md border p-3'>
                      {keys
                        .filter((k) => k.status === 'enabled')
                        .map((k) => (
                          <label key={k.id} className='flex cursor-pointer items-center gap-2 text-sm'>
                            <Checkbox checked={selectedKeyIds.includes(k.id)} onCheckedChange={(c) => toggleKey(k.id, c === true)} />
                            <span className='flex-1'>{k.name}</span>
                            <Badge variant='secondary'>{k.profiles?.activeProfile || t('ai4s.myKeys.noTier')}</Badge>
                          </label>
                        ))}
                    </div>
                  </div>
                  <Select value={tier} onValueChange={setTier}>
                    <SelectTrigger>
                      <SelectValue placeholder={t('ai4s.myKeys.dialog.tierPlaceholder')} />
                    </SelectTrigger>
                    <SelectContent>
                      {/* issue #86：只列秩次 > 所选 Key 最低档的选项 */}
                      {options.map((opt) => (
                        <SelectItem key={opt} value={opt}>
                          {t(opt === '高档' ? 'ai4s.myKeys.dialog.tierPremium' : 'ai4s.myKeys.dialog.tierStandard')}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {selectedKeys.length > 0 && options.length === 0 && (
                    <p className='text-muted-foreground text-sm'>{t('ai4s.myKeys.dialog.keysMaxed')}</p>
                  )}
                </>
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
              <Button
                onClick={submit}
                disabled={createRequest.isPending || (applyKind === 'new' ? !purpose.trim() : !tier || selectedKeyIds.length === 0)}
              >
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
