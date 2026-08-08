import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { useRequest } from '@/features/requests/data';

/**
 * ai4s 审计元数据抽屉（issue #13）
 * 只渲染元数据字段；绝不渲染 requestBody / responseBody / chunks / curl 预览。
 */
interface Props {
  requestId: string | null;
  onClose: () => void;
}

export function Ai4sRequestMetaDrawer({ requestId, onClose }: Props) {
  const { data: r, isLoading } = useRequest(requestId ?? '', { enabled: !!requestId });

  const rows: [string, React.ReactNode][] = r
    ? [
        ['请求 ID', String(r.id)],
        ['时间', new Date(r.createdAt).toLocaleString('zh-CN')],
        ['状态', <Badge key="s" variant={r.status === 'completed' ? 'default' : 'destructive'}>{r.status}</Badge>],
        ['模型', r.modelID],
        ['渠道', r.channel?.name ?? String(r.channelID ?? '-')],
        ['API Key', r.apiKey?.name ?? String(r.apiKeyID ?? '-')],
        ['来源', r.source],
        ['流式', r.stream ? '是' : '否'],
        ['客户端 IP', r.clientIP ?? '-'],
        ['端到端延迟', r.metricsLatencyMs != null ? `${r.metricsLatencyMs} ms` : '-'],
        ['首 Token 延迟', r.metricsFirstTokenLatencyMs != null ? `${r.metricsFirstTokenLatencyMs} ms` : '-'],
        [
          'Token（输入/输出/缓存）',
          r.usageLogs?.edges?.length
            ? r.usageLogs.edges
                .map((e: any) => `${e.node.promptTokens}/${e.node.completionTokens}/${e.node.promptCachedTokens ?? 0}`)
                .join('；')
            : '-',
        ],
        [
          '成本（刊例价）',
          r.usageLogs?.edges?.length
            ? r.usageLogs.edges.map((e: any) => `$${(e.node.totalCost ?? 0).toFixed(4)}`).join('；')
            : '-',
        ],
      ]
    : [];

  return (
    <Sheet open={!!requestId} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className='w-[420px] sm:max-w-[420px]'>
        <SheetHeader>
          <SheetTitle>请求元数据</SheetTitle>
        </SheetHeader>
        <ScrollArea className='mt-4 h-[calc(100vh-6rem)]'>
          {isLoading ? (
            <div className='space-y-3'>
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className='h-6 w-full' />
              ))}
            </div>
          ) : (
            <table className='w-full text-sm'>
              <tbody>
                {rows.map(([k, v]) => (
                  <tr key={k} className='border-b last:border-0'>
                    <td className='py-2 pr-4 text-muted-foreground whitespace-nowrap'>{k}</td>
                    <td className='py-2 font-mono'>{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className='mt-6 text-xs text-muted-foreground'>
            按 ai4s 审计原则，本视图不含请求/响应原文、分片内容与 curl 重放。
          </p>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
