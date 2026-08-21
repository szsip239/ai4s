/**
 * 员工「我的 Key」页（issue #74）。
 * 背景：#68 收掉员工 key 自管权限后员工看不到自己名下的 key，也没有审批入口。
 * 本页列本人名下全部 key（名称/状态/档位/创建时间，无明文——上游本就不可取），
 * 并提供「申请新 Key」「申请提额」两个飞书审批指引对话框（不做一键代提，下一迭代再议）。
 * 权限：所有登录用户可见（routeConfigs 无 requiredScopes；admin 语义即「我的」）。
 * 飞书身份判定：JIT 账号 email 为 ou_*@casdoor.oidc（sso-oidc.md 约定）；否则视为本地账号，
 * 对话框提示联系管理员（本地账号无法走飞书审批身份链）。
 */
import { useState } from 'react';
import { format } from 'date-fns';
import { useTranslation } from 'react-i18next';
import { IconKey, IconPlus, IconTrendingUp } from '@tabler/icons-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Header } from '@/components/layout/header';
import { Main } from '@/components/layout/main';
import { useMe } from '@/features/auth/data/auth';
import { useMyKeys, type MyKey } from './api';

type ApplyKind = 'new' | 'upgrade' | null;

function statusVariant(status: string): 'default' | 'secondary' | 'outline' {
  if (status === 'enabled') return 'default';
  if (status === 'disabled') return 'secondary';
  return 'outline';
}

function KeyRow({ k }: { k: MyKey }) {
  const { t } = useTranslation();
  const tier = k.profiles?.activeProfile || t('ai4s.myKeys.noTier');
  return (
    <TableRow>
      <TableCell className='font-medium'>{k.name}</TableCell>
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

export default function Ai4sMyKeysPage() {
  const { t } = useTranslation();
  const { data: me } = useMe();
  const myKeys = useMyKeys();
  const [applyKind, setApplyKind] = useState<ApplyKind>(null);

  // JIT/飞书绑定账号 email 形如 ou_*@casdoor.oidc；其余（如 user@example.com）为本地账号
  const isFeishuBound = (me?.email || '').endsWith('@casdoor.oidc');
  const keys = myKeys.data?.keys ?? [];

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

        <Dialog open={applyKind !== null} onOpenChange={(open) => !open && setApplyKind(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {applyKind === 'new' ? t('ai4s.myKeys.dialog.newTitle') : t('ai4s.myKeys.dialog.upgradeTitle')}
              </DialogTitle>
              <DialogDescription>
                {applyKind === 'new' ? t('ai4s.myKeys.dialog.newDesc') : t('ai4s.myKeys.dialog.upgradeDesc')}
              </DialogDescription>
            </DialogHeader>
            <div className='space-y-2 text-sm'>
              {(applyKind === 'new'
                ? ['ai4s.myKeys.dialog.newStep1', 'ai4s.myKeys.dialog.newStep2', 'ai4s.myKeys.dialog.newStep3']
                : ['ai4s.myKeys.dialog.upStep1', 'ai4s.myKeys.dialog.upStep2', 'ai4s.myKeys.dialog.upStep3']
              ).map((key, i) => (
                <p key={key}>{`${i + 1}. ${t(key)}`}</p>
              ))}
              {!isFeishuBound && (
                <Alert>
                  <AlertDescription>{t('ai4s.myKeys.dialog.localAccountHint')}</AlertDescription>
                </Alert>
              )}
            </div>
          </DialogContent>
        </Dialog>
      </Main>
    </>
  );
}
