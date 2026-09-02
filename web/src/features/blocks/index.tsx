/**
 * 拦截审计页（issue #132，日志 tab 组「拦截」）：GET /dlp-admin/shadow-verdicts?layer=block&n=50
 * 只读表格（时间/模型/命中规则族），手动刷新不轮询；数据为 shim block 层审计条（不落原文）。
 * 数据源为 DLP 管理面（admin 级），路由用 system 级 read_channels 鉴权（同智能路由页）。
 */
import { format } from 'date-fns';
import { IconRefresh } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { useBlockVerdicts, type BlockVerdict } from '@/ai4s/pages/smart-routing/api';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Header } from '@/components/layout/header';
import { Main } from '@/components/layout/main';
import { Ai4sPageTabs } from '@/ai4s/components/Ai4sPageTabs';
import { pageTabGroups } from '@/ai4s/components/page-tab-groups';

/** 内容阻断审计行（issue #132；#134 增列：侧别/用户/Key/命中摘录） */
function BlockRow({ r }: { r: BlockVerdict }) {
  const { t } = useTranslation();
  const rules = r.rule_ids ?? [];
  return (
    <TableRow>
      <TableCell className='text-muted-foreground whitespace-nowrap'>
        {typeof r.ts === 'number' ? format(new Date(r.ts * 1000), 'MM-dd HH:mm:ss') : '—'}
      </TableCell>
      <TableCell>
        {r.side ? <Badge variant='outline'>{t(`ai4s.blocks.side.${r.side}`)}</Badge> : '—'}
      </TableCell>
      <TableCell className='max-w-40 truncate' title={r.user_email ?? undefined}>
        {r.user_email ?? '—'}
      </TableCell>
      <TableCell className='max-w-40 truncate' title={r.key_name ?? undefined}>
        {r.key_name ?? '—'}
      </TableCell>
      <TableCell className='max-w-40 truncate font-mono text-xs' title={r.model ?? undefined}>
        {r.model ?? '—'}
      </TableCell>
      <TableCell className='font-mono text-xs'>
        {rules.length > 0 ? rules.map((id) => <Badge key={id} variant='destructive' className='mr-1'>{id}</Badge>) : '—'}
      </TableCell>
      <TableCell className='max-w-56 font-mono text-xs'>
        {(r.excerpts ?? []).length > 0
          ? r.excerpts!.map((e, i) => (
              <span key={`${e.rule}-${i}`} className='mr-2 inline-block max-w-52 truncate align-bottom' title={`${e.rule}: ${e.text}`}>
                {e.text}
              </span>
            ))
          : '—'}
      </TableCell>
    </TableRow>
  );
}

export default function BlocksPage() {
  const { t } = useTranslation();
  const block = useBlockVerdicts();
  const records = block.data?.records ?? [];

  return (
    <>
      <Header fixed>
        <div className='flex flex-1 items-center justify-between'>
          <div>
            <h2 className='text-xl font-bold tracking-tight'>{t('ai4s.blocks.title')}</h2>
            <p className='text-sm text-muted-foreground'>{t('ai4s.blocks.description')}</p>
          </div>
          <Button variant='outline' size='sm' disabled={block.isFetching} onClick={() => block.refetch()}>
            <IconRefresh className={block.isFetching ? 'animate-spin' : undefined} />
            {t('ai4s.blocks.refresh')}
          </Button>
        </div>
      </Header>

      <Main fixed>
        <Ai4sPageTabs tabs={pageTabGroups.observability(t)} />
        {block.isLoading ? (
          <p className='text-muted-foreground py-6 text-center text-sm'>{t('common.loading')}</p>
        ) : block.isError ? (
          <Alert variant='destructive'>
            <AlertTitle>{t('ai4s.blocks.loadError')}</AlertTitle>
            <AlertDescription>{block.error instanceof Error ? block.error.message : String(block.error)}</AlertDescription>
          </Alert>
        ) : records.length === 0 ? (
          <p className='text-muted-foreground py-6 text-center text-sm'>{t('ai4s.blocks.empty')}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('ai4s.blocks.columns.time')}</TableHead>
                <TableHead>{t('ai4s.blocks.columns.side')}</TableHead>
                <TableHead>{t('ai4s.blocks.columns.user')}</TableHead>
                <TableHead>{t('ai4s.blocks.columns.key')}</TableHead>
                <TableHead>{t('ai4s.blocks.columns.model')}</TableHead>
                <TableHead>{t('ai4s.blocks.columns.rules')}</TableHead>
                <TableHead>{t('ai4s.blocks.columns.excerpt')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((r, i) => (
                <BlockRow key={`${r.ts}-${i}`} r={r} />
              ))}
            </TableBody>
          </Table>
        )}
      </Main>
    </>
  );
}
