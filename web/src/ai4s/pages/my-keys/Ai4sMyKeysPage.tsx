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
 */
import { useState } from 'react';
import { format } from 'date-fns';
import { useTranslation } from 'react-i18next';
import { IconEye, IconEyeOff, IconKey, IconPlus, IconTrendingUp } from '@tabler/icons-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CopyButton } from '@/components/ui/copy-button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Header } from '@/components/layout/header';
import { Main } from '@/components/layout/main';
import { useMe } from '@/features/auth/data/auth';
import { useCancelKeyRequest, useCreateKeyRequest, useMyKeyRequests, useMyKeys, type KeyRequest, type MyKey } from './api';

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
  const { t } = useTranslation();
  const [showKey, setShowKey] = useState(false);
  const tier = k.profiles?.activeProfile || t('ai4s.myKeys.noTier');
  // 掩码：保前缀 ah- 与尾 4 位便于辨认，中间打码（issue #81 明文本人可见，默认不裸露）
  const masked = k.key ? `${k.key.slice(0, 3)}••••••••${k.key.slice(-4)}` : '—';
  return (
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
      <TableCell className='text-muted-foreground'>
        {k.createdAt ? format(new Date(k.createdAt), 'yyyy-MM-dd HH:mm') : '—'}
      </TableCell>
    </TableRow>
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
      <TableCell className='text-muted-foreground'>
        {r.createdAt ? format(new Date(r.createdAt), 'yyyy-MM-dd HH:mm') : '—'}
      </TableCell>
      <TableCell className='max-w-64 truncate text-muted-foreground' title={r.result || ''}>
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

  const closeDialog = () => {
    setApplyKind(null);
    setPurpose('');
    setTier('');
  };

  const submit = () => {
    if (applyKind === null) return;
    createRequest.mutate(
      applyKind === 'new' ? { kind: 'new', purpose } : { kind: 'upgrade', tier },
      {
        onSuccess: () => {
          toast.success(t('ai4s.myKeys.submitOk'));
          closeDialog();
        },
        onError: (e) => toast.error(e.message),
      }
    );
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
                <CardDescription>{t('ai4s.myKeys.description')}</CardDescription>
              </div>
              <div className='flex gap-2'>
                <Button size='sm' onClick={() => setApplyKind('new')}>
                  <IconPlus className='mr-1 h-4 w-4' />
                  {t('ai4s.myKeys.applyNew')}
                </Button>
                <Button size='sm' variant='outline' onClick={() => setApplyKind('upgrade')}>
                  <IconTrendingUp className='mr-1 h-4 w-4' />
                  {t('ai4s.myKeys.applyUpgrade')}
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {myKeys.isError ? (
              <Alert variant='destructive'>
                <AlertDescription>{t('ai4s.myKeys.loadError')}</AlertDescription>
              </Alert>
            ) : myKeys.isLoading ? (
              <div className='text-sm text-muted-foreground'>{t('common.loading', '加载中…')}</div>
            ) : keys.length === 0 ? (
              <div className='py-10 text-center'>
                <p className='text-sm text-muted-foreground'>{t('ai4s.myKeys.empty')}</p>
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
              <p className='py-6 text-center text-sm text-muted-foreground'>{t('ai4s.myKeys.requests.empty')}</p>
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

        <Dialog open={applyKind !== null} onOpenChange={(open) => !open && closeDialog()}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {applyKind === 'new' ? t('ai4s.myKeys.dialog.newTitle') : t('ai4s.myKeys.dialog.upgradeTitle')}
              </DialogTitle>
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
                    <SelectItem value='标准档'>{t('ai4s.myKeys.dialog.tierStandard')}</SelectItem>
                    <SelectItem value='高档'>{t('ai4s.myKeys.dialog.tierPremium')}</SelectItem>
                  </SelectContent>
                </Select>
              )}
              <Alert>
                <AlertDescription>
                  {isFeishuBound
                    ? t('ai4s.myKeys.dialog.deliverFeishu')
                    : t('ai4s.myKeys.dialog.deliverNonFeishu')}
                </AlertDescription>
              </Alert>
            </div>
            <DialogFooter>
              <Button variant='outline' onClick={closeDialog}>
                {t('common.cancel', '取消')}
              </Button>
              <Button
                onClick={submit}
                disabled={createRequest.isPending || (applyKind === 'new' ? !purpose.trim() : !tier)}
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
