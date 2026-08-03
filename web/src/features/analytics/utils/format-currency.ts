// ai4s（issue #18）：cost 语义是 credit，显示为"点 / credits"，不用货币符号。
// currencyCode 参数保留（调用方兼容），显示时忽略。
import i18n from '@/lib/i18n';

function suffix(): string {
  return (i18n.language || '').startsWith('zh') ? ' 点' : ' credits';
}

export function formatCurrencySimple(val: number, _currencyCode: string): string {
  return `${val.toFixed(4)}${suffix()}`;
}

export function formatCurrencyTick(value: number | string, _currencyCode: string): string {
  return `${Number(value).toFixed(0)}${suffix()}`;
}
