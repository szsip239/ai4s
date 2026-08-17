import { useTranslation } from 'react-i18next';

/**
 * 登录/初始化页左栏品牌区（issue #58）：新 logo + Ai-4S-infra 名称 + 4S 四支柱说明。
 * 文案走 locale 键（ai4s.brand.*，中英双语）；挂载点登记见 MOUNTPOINTS.md（two-column-auth）。
 * 左栏为深色 slate 底，配色用暖橙渐变呼应 token 层 terracotta 主色。
 */

const PILLARS = ['science', 'security', 'service', 'speed'] as const;

export function Ai4sBrandPanel() {
  const { t } = useTranslation();

  return (
    <div className='mb-8'>
      <div className='mb-6 flex items-center gap-4'>
        <img src='/logo.svg' alt={t('ai4s.brand.name')} className='h-12 w-12 shrink-0 drop-shadow-lg' />
        <div>
          <h1 className='mb-1 text-base font-light text-slate-300'>{t('auth.brand.title')}</h1>
          <h2 className='bg-gradient-to-r from-orange-300 to-amber-200 bg-clip-text text-4xl font-bold text-transparent'>
            {t('ai4s.brand.name')}
          </h2>
        </div>
      </div>
      <p className='mb-6 text-lg leading-relaxed text-slate-300'>{t('ai4s.brand.tagline')}</p>
      <ul className='grid grid-cols-2 gap-3'>
        {PILLARS.map((p) => (
          <li key={p} className='rounded-lg bg-white/5 px-3 py-2 ring-1 ring-white/10'>
            <span className='mr-2 font-semibold text-orange-200'>{t(`ai4s.brand.pillars.${p}.name`)}</span>
            <span className='text-sm text-slate-300'>{t(`ai4s.brand.pillars.${p}.desc`)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
