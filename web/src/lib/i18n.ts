import i18n from 'i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { initReactI18next } from 'react-i18next';

function mergeTranslations(...translations: Array<Record<string, unknown>>) {
  return Object.assign({}, ...translations);
}

type LocaleModule = {
  default: Record<string, unknown>;
};

function getModuleDefaultExport(module: unknown): Record<string, unknown> {
  if (module && typeof module === 'object' && 'default' in module) {
    return (module as LocaleModule).default;
  }
  return module as Record<string, unknown>;
}

import ai4sPatchZhCN from '../ai4s/locales/zh-CN/ai4s-patch.json'; // 挂载点 M6：ai4s zh-CN 补键包（issue #12）

const enModules = import.meta.glob('../locales/en/*.json', { eager: true }) as Record<string, unknown>;
const zhCNModules = import.meta.glob('../locales/zh-CN/*.json', { eager: true }) as Record<string, unknown>;

const enTranslation = mergeTranslations(...Object.values(enModules).map(getModuleDefaultExport));
const zhTranslation = mergeTranslations(...Object.values(zhCNModules).map(getModuleDefaultExport), ai4sPatchZhCN);

const resources = {
  en: {
    translation: enTranslation,
  },
  zh: {
    translation: zhTranslation,
  },
  'zh-CN': {
    translation: zhTranslation,
  },
};

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'en',
    debug: false,
    supportedLngs: ['en', 'zh', 'zh-CN'],

    interpolation: {
      escapeValue: false, // React 已经默认转义了
      format: (value, format, lng, options) => {
        if (format === 'currency') {
          // ai4s（issue #18）：cost 语义是 credit（1 credit = $1 官方原价 × 渠道倍率），
          // 显示为"点 / credits"，不显示货币符号；档位：体验 3 / 标准 20 / 高档 80 点每自然月
          const num = typeof value === 'number' ? value : Number(value);
          if (Number.isNaN(num)) return String(value);
          const isZh = (lng || '').startsWith('zh');
          const text = new Intl.NumberFormat(options?.locale || lng, {
            minimumFractionDigits: options?.minimumFractionDigits ?? (Math.abs(num) >= 1 ? 2 : 6),
            maximumFractionDigits: options?.maximumFractionDigits ?? 6,
          }).format(num);
          return isZh ? `${text} 点` : `${text} credits`;
        }
        return value;
      },
    },

    detection: {
      order: ['localStorage', 'navigator', 'htmlTag'],
      caches: ['localStorage'],
      convertDetectedLanguage: (lng: string) => {
        const normalized = lng.toLowerCase();
        if (normalized === 'zh-cn' || normalized.startsWith('zh-')) {
          return 'zh';
        }
        return lng;
      },
    },
  });

export default i18n;
