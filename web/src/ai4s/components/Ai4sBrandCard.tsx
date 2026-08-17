import { useTranslation } from 'react-i18next';
import { Card, CardContent } from '@/components/ui/card';

/**
 * 仪表盘首页品牌说明卡（issue #58）：名称 + 4S 含义，一行低调横卡。
 * 样式对齐现有 Card（虚线边框 + 无投影，不抢眼）；文案走 locale 键（ai4s.brand.*）。
 * 挂载点登记见 MOUNTPOINTS.md（features/dashboard/index.tsx）。
 */

const PILLARS = ['science', 'security', 'service', 'speed'] as const;

export function Ai4sBrandCard() {
  const { t } = useTranslation();

  return (
    <Card className='border-dashed shadow-none'>
      <CardContent className='flex flex-col gap-3 p-4 sm:flex-row sm:items-center'>
        <div className='flex shrink-0 items-center gap-3'>
          <img src='/logo.svg' alt={t('ai4s.brand.name')} className='size-9' />
          <div className='leading-tight'>
            <div className='text-sm font-semibold'>{t('ai4s.brand.name')}</div>
            <div className='text-muted-foreground text-xs'>{t('ai4s.brand.tagline')}</div>
          </div>
        </div>
        <div className='flex flex-wrap items-center gap-x-4 gap-y-1 sm:ml-auto'>
          {PILLARS.map((p) => (
            <span key={p} className='text-xs whitespace-nowrap'>
              <span className='text-primary font-medium'>{t(`ai4s.brand.pillars.${p}.name`)}</span>{' '}
              <span className='text-muted-foreground'>{t(`ai4s.brand.pillars.${p}.desc`)}</span>
            </span>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
