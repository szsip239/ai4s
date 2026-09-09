/**
 * 管理端 Key 管理「档位消耗」仪表（2026-09-09 owner 反馈）：profiles 对话框配额用量区
 * 由纯文本「已用/上限」升级为 进度条 + 百分比——三维度（请求数/Token/点）各自一条仪表，
 * 上限为空显示「不设限」；色调分档：<80 主色、80-99 琥珀、≥100 红（超额 pct 文本标红）。
 * 数值口径复用用户侧 my-keys（#82 指南：zh 万/亿、en K/M；credit 点最多两位小数）。
 */
import { useTranslation } from 'react-i18next';
import { Progress } from '@/components/ui/progress';
import { formatCredits, formatTokenCount } from '@/ai4s/pages/my-keys/key-usage';
import { formatPct, meterTone, usagePct, type MeterTone } from './quota-meter';

interface QuotaUsageNumbers {
  requestCount: number;
  totalTokens: number;
  totalCost: number;
}

interface QuotaLimits {
  requests?: number | null;
  totalTokens?: number | null;
  cost?: number | null;
}

const TONE_INDICATOR: Record<MeterTone, string | undefined> = {
  normal: undefined,
  warning: 'bg-amber-500',
  over: 'bg-red-500',
};

function Meter({
  label,
  usedText,
  limitText,
  pct,
}: {
  label: string;
  usedText: string;
  limitText: string | null;
  pct: number | null;
}) {
  const { t } = useTranslation();
  const tone = meterTone(pct);
  return (
    <div className='min-w-0 space-y-1'>
      <div className='flex items-baseline justify-between gap-2'>
        <span className='text-muted-foreground text-xs'>{label}</span>
        {pct != null && (
          <span className={tone === 'over' ? 'text-destructive text-xs font-medium' : 'text-muted-foreground text-xs'}>
            {formatPct(pct)}
          </span>
        )}
      </div>
      <div className='truncate text-sm'>
        {limitText != null ? (
          <>
            {usedText} / {limitText}
          </>
        ) : (
          <>
            {usedText} <span className='text-muted-foreground text-xs'>· {t('ai4s.myKeys.usage.unlimited')}</span>
          </>
        )}
      </div>
      {pct != null && (
        <Progress value={Math.min(pct, 100)} indicatorClassName={TONE_INDICATOR[tone]} className='h-1.5' />
      )}
    </div>
  );
}

export function Ai4sQuotaUsageGrid({ usage, quota }: { usage: QuotaUsageNumbers; quota: QuotaLimits | null | undefined }) {
  const { t, i18n } = useTranslation();
  const zh = i18n.language.startsWith('zh');
  const tokens = (n: number) => formatTokenCount(n, zh);

  return (
    <div className='grid gap-3 md:grid-cols-3'>
      <Meter
        label={t('apikeys.profiles.quotaRequests')}
        usedText={Number(usage.requestCount ?? 0).toLocaleString()}
        limitText={quota?.requests != null ? Number(quota.requests).toLocaleString() : null}
        pct={usagePct(Number(usage.requestCount ?? 0), quota?.requests)}
      />
      <Meter
        label={t('apikeys.profiles.quotaTotalTokens')}
        usedText={tokens(Number(usage.totalTokens ?? 0))}
        limitText={quota?.totalTokens != null ? tokens(Number(quota.totalTokens)) : null}
        pct={usagePct(Number(usage.totalTokens ?? 0), quota?.totalTokens)}
      />
      <Meter
        label={t('apikeys.profiles.quotaCost')}
        usedText={formatCredits(Number(usage.totalCost ?? 0))}
        limitText={quota?.cost != null ? formatCredits(Number(quota.cost)) : null}
        pct={usagePct(Number(usage.totalCost ?? 0), quota?.cost)}
      />
    </div>
  );
}
