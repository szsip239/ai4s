/**
 * 管理员「Key 审批」页（issue #79）：控制台发起通道的点批入口。
 * 流程：员工在「我的 Key」页提交 → 管理员飞书收审批卡（link 按钮直达本页）→ 本页点批 →
 * shim 同步执行（建 Key / 提档作用于申请所选 Key）→ 回执（审批卡更新 + 申请人私信）。
 * issue #81：同意弹窗可选生效档位（默认：新建=体验档、提额=所求档），随 approve body 传 shim。
 * issue #85：提额申请档位收窄为 UPGRADE_TIERS（标准/高档，提额语义不含体验档；shim
 * resolve_request 同收窄，双保险），新建申请仍全集；approveUpgradeNote 改为事实陈述
 * （执行侧已硬拦降档，低于 Key 当前档的目标会被跳过并列出）。
 * issue #86：提额按 Key 勾选——详情列与同意弹窗摘要显示所选 Key（upgradeDetailLabel 共用：
 * 名称快照优先，fail-open 缺快照回退列 id；真存量申请两者皆无回退只显示目标档，
 * 执行作用于申请人全部 enabled Key）。
 * 状态门幂等：仅 pending 行显示操作；已处理行的结果为终态（拒绝理由/执行摘要，绝无明文——
 * 非飞书申请人的明文只私信管理员本人，不落本页）。
 */
import { useState } from 'react';
import { format } from 'date-fns';
import { useTranslation } from 'react-i18next';
import { IconClipboardCheck } from '@tabler/icons-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
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
import { usePermissions } from '@/hooks/usePermissions';
import { useAdminKeyRequests, useResolveKeyRequest, type AdminKeyRequest } from './api';
import { upgradeDetailLabel } from '../my-keys/api';

/** 审批可选档位·新建申请（issue #81 全集）：与 shim key_requests.ALLOWED_TIERS 白名单双向同源，改动需两侧同步 */
const APPROVE_TIERS = ['体验档', '标准档', '高档'] as const;
/** 审批可选档位·提额申请（issue #85 收窄，提额语义不含体验档）：与 shim key_requests.TIERS 双向同源 */
const UPGRADE_TIERS = ['标准档', '高档'] as const;

/** 弹窗默认档：新建=体验档（KEY_INIT_TIER），提额=申请所求档（兜底标准档） */
function defaultApproveTier(r: AdminKeyRequest): string {
  return r.kind === 'new' ? APPROVE_TIERS[0] : r.tier || UPGRADE_TIERS[0];
}

function statusVariant(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'approved') return 'default';
  if (status === 'pending') return 'secondary';
  if (status === 'rejected') return 'destructive';
  return 'outline';
}

export default function Ai4sKeyRequestsPage() {
  const { t } = useTranslation();
  const query = useAdminKeyRequests();
  const resolve = useResolveKeyRequest();
  // 点批是写操作（shim 端 write_channels 鉴权）：只读管理员可见列表但按钮禁用，对齐 channels 等 admin 页惯例
  const { channelPermissions } = usePermissions();
  const canResolve = channelPermissions.canWrite;
  const [approveTarget, setApproveTarget] = useState<AdminKeyRequest | null>(null);
  const [approveTier, setApproveTier] = useState<string>(APPROVE_TIERS[0]);
  const [rejectTarget, setRejectTarget] = useState<AdminKeyRequest | null>(null);
  const [reason, setReason] = useState('');

  const requests = query.data?.requests ?? [];

  const doResolve = (id: string, action: 'approve' | 'reject', rejectReason = '', tier = '') => {
    resolve.mutate(
      { id, action, reason: rejectReason, tier },
      {
        onSuccess: (data) => {
          toast.success(data.request.result || t(`ai4s.keyRequests.${action}Ok`));
          setApproveTarget(null);
          setRejectTarget(null);
          setReason('');
        },
        onError: (e) => toast.error(e.message),
      }
    );
  };

  return (
    <>
      <Header />
      <Main>
        <Card>
          <CardHeader>
            <CardTitle className='flex items-center gap-2'>
              <IconClipboardCheck className='h-5 w-5' />
              {t('ai4s.keyRequests.title')}
            </CardTitle>
            <CardDescription>{t('ai4s.keyRequests.description')}</CardDescription>
          </CardHeader>
          <CardContent>
            {query.isError ? (
              <Alert variant='destructive'>
                <AlertDescription>{t('ai4s.keyRequests.loadError')}</AlertDescription>
              </Alert>
            ) : requests.length === 0 ? (
              <p className='py-10 text-center text-sm text-muted-foreground'>{t('ai4s.keyRequests.empty')}</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t('ai4s.keyRequests.columns.applicant')}</TableHead>
                    <TableHead>{t('ai4s.keyRequests.columns.kind')}</TableHead>
                    <TableHead>{t('ai4s.keyRequests.columns.detail')}</TableHead>
                    <TableHead>{t('ai4s.keyRequests.columns.createdAt')}</TableHead>
                    <TableHead>{t('ai4s.keyRequests.columns.status')}</TableHead>
                    <TableHead>{t('ai4s.keyRequests.columns.result')}</TableHead>
                    <TableHead>{t('ai4s.keyRequests.columns.actions')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {requests.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className='font-medium'>{r.applicant?.email || '—'}</TableCell>
                      <TableCell>{t(`ai4s.keyRequests.kind.${r.kind}`)}</TableCell>
                      <TableCell className='max-w-48 truncate text-muted-foreground'>
                        {/* issue #86：提额申请显示所选 Key（名称快照优先，fail-open 回退 id）；存量申请回退只显示目标档 */}
                        {r.kind === 'new' ? r.purpose : (upgradeDetailLabel(r) ?? r.tier)}
                      </TableCell>
                      <TableCell className='text-muted-foreground'>
                        {r.createdAt ? format(new Date(r.createdAt), 'yyyy-MM-dd HH:mm') : '—'}
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusVariant(r.status)}>
                          {t(`ai4s.keyRequests.status.${r.status}`, r.status)}
                        </Badge>
                      </TableCell>
                      <TableCell className='max-w-64 truncate text-muted-foreground' title={r.result || ''}>
                        {r.result || '—'}
                      </TableCell>
                      <TableCell>
                        {r.status === 'pending' && (
                          <div className='flex gap-2'>
                            <Button
                              size='sm'
                              disabled={!canResolve}
                              onClick={() => {
                                setApproveTarget(r);
                                setApproveTier(defaultApproveTier(r));
                              }}
                            >
                              {t('ai4s.keyRequests.approve')}
                            </Button>
                            <Button size='sm' variant='outline' disabled={!canResolve} onClick={() => setRejectTarget(r)}>
                              {t('ai4s.keyRequests.reject')}
                            </Button>
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <AlertDialog open={approveTarget !== null} onOpenChange={(open) => !open && setApproveTarget(null)}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t('ai4s.keyRequests.approveConfirmTitle')}</AlertDialogTitle>
              <AlertDialogDescription>
                {t('ai4s.keyRequests.approveConfirmDesc')}
                {approveTarget && (
                  <span className='mt-2 block'>
                    {approveTarget.applicant?.email} · {t(`ai4s.keyRequests.kind.${approveTarget.kind}`)} ·{' '}
                    {approveTarget.kind === 'new' ? approveTarget.purpose : (upgradeDetailLabel(approveTarget) ?? approveTarget.tier)}
                  </span>
                )}
              </AlertDialogDescription>
            </AlertDialogHeader>
            {/* issue #81：批准时可选生效档位（默认新建=体验档/提额=所求档），随 approve body 传 shim */}
            <div className='flex items-center gap-3'>
              <span className='text-sm text-muted-foreground'>{t('ai4s.keyRequests.approveTierLabel')}</span>
              <Select value={approveTier} onValueChange={setApproveTier}>
                <SelectTrigger className='w-40'>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {/* issue #85：提额申请档位收窄为标准/高档（shim resolve 同收窄，双保险）；新建仍全集 */}
                  {(approveTarget?.kind === 'upgrade' ? UPGRADE_TIERS : APPROVE_TIERS).map((tier) => (
                    <SelectItem key={tier} value={tier}>
                      {tier}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {approveTarget?.kind === 'upgrade' && (
              <p className='text-sm text-muted-foreground'>{t('ai4s.keyRequests.approveUpgradeNote')}</p>
            )}
            <AlertDialogFooter>
              <AlertDialogCancel>{t('common.cancel', '取消')}</AlertDialogCancel>
              <AlertDialogAction
                disabled={resolve.isPending}
                onClick={() => approveTarget && doResolve(approveTarget.id, 'approve', '', approveTier)}
              >
                {t('ai4s.keyRequests.approve')}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        <Dialog open={rejectTarget !== null} onOpenChange={(open) => !open && (setRejectTarget(null), setReason(''))}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t('ai4s.keyRequests.rejectTitle')}</DialogTitle>
              <DialogDescription>
                {rejectTarget?.applicant?.email} · {rejectTarget && t(`ai4s.keyRequests.kind.${rejectTarget.kind}`)}
              </DialogDescription>
            </DialogHeader>
            <Textarea
              placeholder={t('ai4s.keyRequests.rejectReasonPlaceholder')}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              maxLength={200}
            />
            <DialogFooter>
              <Button variant='outline' onClick={() => (setRejectTarget(null), setReason(''))}>
                {t('common.cancel', '取消')}
              </Button>
              <Button
                variant='destructive'
                disabled={resolve.isPending}
                onClick={() => rejectTarget && doResolve(rejectTarget.id, 'reject', reason)}
              >
                {t('ai4s.keyRequests.reject')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </Main>
    </>
  );
}
