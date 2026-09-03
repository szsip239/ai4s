import { useTranslation } from 'react-i18next';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Separator } from '@/components/ui/separator';
import { formatNumber } from '@/utils/format-number';

/**
 * Key token 用量统计展示主体（从 api-key-token-chart-dialog 抽出的纯展示件）：
 * 管理端弹窗（apiKeyTokenUsageStats 直连 GraphQL）与员工自助侧弹窗（shim /self/key-usage-stats
 * 代查同一上游 query）共用——两侧 stat 均为 inputTokens/outputTokens/cachedTokens/
 * reasoningTokens/topModels 五键白名单形状，字段 optional 容忍（自助侧 zod 更宽松）。
 */
export interface TokenUsageModelStat {
  modelId: string;
  inputTokens?: number;
  outputTokens?: number;
  cachedTokens?: number;
  reasoningTokens?: number;
}

export interface TokenUsageStat {
  inputTokens?: number;
  outputTokens?: number;
  cachedTokens?: number;
  reasoningTokens?: number;
  topModels?: TokenUsageModelStat[];
}

const pct = (value: number, total: number) =>
  total > 0 ? ((value / total) * 100).toFixed(1) : '0.0';

interface ApiKeyTokenUsageViewProps {
  stat: TokenUsageStat | null | undefined;
  isLoading: boolean;
  isFetching?: boolean;
}

export function ApiKeyTokenUsageView({ stat, isLoading, isFetching = false }: ApiKeyTokenUsageViewProps) {
  const { t } = useTranslation();
  const input = stat?.inputTokens ?? 0;
  const output = stat?.outputTokens ?? 0;
  const cached = stat?.cachedTokens ?? 0;
  const reasoning = stat?.reasoningTokens ?? 0;
  const totalTokens = input + output;
  const topModels = stat?.topModels ?? [];

  if (isLoading) {
    return <Skeleton className="h-[200px] w-full" />;
  }
  if (!stat || totalTokens === 0) {
    return (
      <div className="flex h-[200px] items-center justify-center text-muted-foreground">
        {t('apikeys.tokenUsageChart.noData')}
      </div>
    );
  }
  return (
    <div className="space-y-4" style={{ opacity: isFetching ? 0.6 : 1, transition: 'opacity 0.2s' }}>
      <div>
        <h3 className="mb-2 text-sm font-medium">{t('apikeys.tokenUsageChart.overallUsage')}</h3>
        <div className="rounded-lg border overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-2/5 whitespace-nowrap">{t('apikeys.tokenUsageChart.tokenType')}</TableHead>
                <TableHead className="w-[30%] text-center whitespace-nowrap">{t('apikeys.tokenUsageChart.count')}</TableHead>
                <TableHead className="w-[30%] text-center whitespace-nowrap">{t('apikeys.tokenUsageChart.percentage')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow>
                <TableCell className="font-medium">{t('apikeys.columns.inputTokens')}</TableCell>
                <TableCell className="text-center tabular-nums">{formatNumber(input)}</TableCell>
                <TableCell className="text-center tabular-nums">{pct(input, totalTokens)}%</TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">{t('apikeys.columns.outputTokens')}</TableCell>
                <TableCell className="text-center tabular-nums">{formatNumber(output)}</TableCell>
                <TableCell className="text-center tabular-nums">{pct(output, totalTokens)}%</TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">{t('apikeys.tokenUsageChart.cacheHitRate')}</TableCell>
                <TableCell className="text-center tabular-nums">{formatNumber(cached)}</TableCell>
                <TableCell className="text-center tabular-nums">{pct(cached, input)}%</TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">{t('apikeys.tokenUsageChart.reasoningRatio')}</TableCell>
                <TableCell className="text-center tabular-nums">{formatNumber(reasoning)}</TableCell>
                <TableCell className="text-center tabular-nums">{pct(reasoning, output)}%</TableCell>
              </TableRow>
              <TableRow className="bg-muted/50 font-semibold">
                <TableCell>{t('apikeys.tokenUsageChart.total')}</TableCell>
                <TableCell className="text-center tabular-nums">{formatNumber(totalTokens)}</TableCell>
                <TableCell className="text-center tabular-nums">100%</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      </div>

      {topModels.length > 0 && (
        <div>
          <Separator className="mb-4" />
          <h3 className="mb-3 text-sm font-medium">{t('apikeys.tokenUsageChart.topModels')}</h3>
          <div className="space-y-4">
            {topModels.map((model, index) => {
              const mIn = model.inputTokens ?? 0;
              const mOut = model.outputTokens ?? 0;
              const mCached = model.cachedTokens ?? 0;
              const mReasoning = model.reasoningTokens ?? 0;
              const modelTotal = mIn + mOut;
              return (
                <div key={model.modelId} className="rounded-lg border">
                  <div className="bg-muted/30 px-4 py-2">
                    <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                      <span className="font-medium text-sm break-all">
                        #{index + 1} {model.modelId}
                      </span>
                      <span className="text-sm text-muted-foreground whitespace-nowrap">
                        {t('apikeys.tokenUsageChart.totalTokens')}: {formatNumber(modelTotal)}
                      </span>
                    </div>
                  </div>
                  <div className="overflow-x-auto">
                    <Table>
                      <TableBody>
                        <TableRow>
                          <TableCell className="w-2/5 font-medium whitespace-nowrap">{t('apikeys.columns.inputTokens')}</TableCell>
                          <TableCell className="w-[30%] text-center tabular-nums">{formatNumber(mIn)}</TableCell>
                          <TableCell className="w-[30%] text-center tabular-nums whitespace-nowrap">
                            {pct(mIn, modelTotal)}%
                          </TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell className="font-medium whitespace-nowrap">{t('apikeys.columns.outputTokens')}</TableCell>
                          <TableCell className="text-center tabular-nums">{formatNumber(mOut)}</TableCell>
                          <TableCell className="text-center tabular-nums whitespace-nowrap">
                            {pct(mOut, modelTotal)}%
                          </TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell className="font-medium whitespace-nowrap">{t('apikeys.tokenUsageChart.cacheHitRate')}</TableCell>
                          <TableCell className="text-center tabular-nums">{formatNumber(mCached)}</TableCell>
                          <TableCell className="text-center tabular-nums whitespace-nowrap">
                            {pct(mCached, mIn)}%
                          </TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell className="font-medium whitespace-nowrap">{t('apikeys.tokenUsageChart.reasoningRatio')}</TableCell>
                          <TableCell className="text-center tabular-nums">{formatNumber(mReasoning)}</TableCell>
                          <TableCell className="text-center tabular-nums whitespace-nowrap">
                            {pct(mReasoning, mOut)}%
                          </TableCell>
                        </TableRow>
                      </TableBody>
                    </Table>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
