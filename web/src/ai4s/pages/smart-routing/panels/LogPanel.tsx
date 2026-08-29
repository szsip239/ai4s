/**
 * 决策日志面板（智能路由页标签项，不再首页直出）：GET /dlp-admin/shadow-verdicts?layer=router&n=50
 * 只读表格（时间/档位/p_complex/原因/改写目标/延迟/会话），手动刷新不轮询。
 */
import { format } from 'date-fns';
import { IconRefresh } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useRouterVerdicts, type RouterVerdict } from '../api';

/** 决策行（router 层五决策字段非 None 才写，读侧全部可选；error 条=分类失败 fail-open） */
function VerdictRow({ r }: { r: RouterVerdict }) {
  return (
    <TableRow>
      <TableCell className='text-muted-foreground whitespace-nowrap'>
        {typeof r.ts === 'number' ? format(new Date(r.ts * 1000), 'MM-dd HH:mm:ss') : '—'}
      </TableCell>
      <TableCell>{r.tier ? <Badge variant={r.tier === 'complex' ? 'default' : 'secondary'}>{r.tier}</Badge> : '—'}</TableCell>
      <TableCell className='font-mono text-xs'>{typeof r.p_complex === 'number' ? r.p_complex.toFixed(3) : '—'}</TableCell>
      <TableCell className='max-w-56'>
        <span className={r.error ? 'text-destructive' : 'text-muted-foreground'} title={r.error ?? undefined}>
          {r.reason ?? '—'}
          {r.error ? `（${r.error}）` : ''}
        </span>
      </TableCell>
      <TableCell className='max-w-56 truncate font-mono text-xs' title={r.resolved_model ?? undefined}>
        {r.resolved_model ?? '—'}
      </TableCell>
      <TableCell className='text-muted-foreground whitespace-nowrap'>
        {typeof r.latency_ms === 'number' ? `${Math.round(r.latency_ms)} ms` : '—'}
      </TableCell>
      <TableCell>{r.session ? <Badge variant='outline'>✓</Badge> : '—'}</TableCell>
    </TableRow>
  );
}

export function Ai4sRoutingLogPanel() {
  const { t } = useTranslation();
  const verdicts = useRouterVerdicts();
  const records = verdicts.data?.records ?? [];

  return (
    <Card>
      <CardHeader>
        <div className='flex items-center justify-between gap-4'>
          <div>
            <CardTitle>{t('ai4s.smartRouting.log.title')}</CardTitle>
            <CardDescription>{t('ai4s.smartRouting.log.description')}</CardDescription>
          </div>
          <Button variant='outline' size='sm' disabled={verdicts.isFetching} onClick={() => verdicts.refetch()}>
            <IconRefresh className={verdicts.isFetching ? 'animate-spin' : undefined} />
            {t('ai4s.smartRouting.log.refresh')}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {verdicts.isLoading ? (
          <p className='text-muted-foreground py-6 text-center text-sm'>{t('common.loading')}</p>
        ) : verdicts.isError ? (
          <Alert variant='destructive'>
            <AlertTitle>{t('ai4s.smartRouting.log.loadError')}</AlertTitle>
            <AlertDescription>{verdicts.error instanceof Error ? verdicts.error.message : String(verdicts.error)}</AlertDescription>
          </Alert>
        ) : records.length === 0 ? (
          <p className='text-muted-foreground py-6 text-center text-sm'>{t('ai4s.smartRouting.log.empty')}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('ai4s.smartRouting.log.columns.time')}</TableHead>
                <TableHead>{t('ai4s.smartRouting.log.columns.tier')}</TableHead>
                <TableHead>{t('ai4s.smartRouting.log.columns.pComplex')}</TableHead>
                <TableHead>{t('ai4s.smartRouting.log.columns.reason')}</TableHead>
                <TableHead>{t('ai4s.smartRouting.log.columns.resolvedModel')}</TableHead>
                <TableHead>{t('ai4s.smartRouting.log.columns.latency')}</TableHead>
                <TableHead>{t('ai4s.smartRouting.log.columns.session')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((r, i) => (
                <VerdictRow key={`${r.ts}-${i}`} r={r} />
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
