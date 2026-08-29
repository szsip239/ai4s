/**
 * 模型 combobox（issue #120）：包装共享 AutoComplete 为单值受控（先例见
 * prompts-action-dialog.tsx ModelAutoCompleteWrapper）——下拉建议=axonhub /models
 * 卡片库（models 页同款 useQueryAllModels，GraphQL models 查询），允许手输
 * （AutoComplete 失焦即把手输值提交为选中值）；白名单字符校验在 validation.validateRouting，
 * 服务端 shim _SETTINGS_MODEL_SAFE 兜底。
 * clearSearchOnFocus：值回填态聚焦即清搜索词展开全量列表（否则过滤关键字=全名，下拉只剩当前值）；
 * onChange 忽略空串——tiers 不允许空（validateRouting 拒绝），重选当前值触发的 reset() 不误清草稿。
 */
import { useEffect, useState } from 'react';
import { AutoComplete } from '@/components/auto-complete';

export function ModelCombobox({
  value,
  onChange,
  modelOptions,
  isLoading,
  placeholder,
  emptyText,
}: {
  value: string;
  onChange: (v: string) => void;
  modelOptions: { value: string; label: string }[];
  isLoading?: boolean;
  placeholder?: string;
  emptyText: string;
}) {
  const [searchValue, setSearchValue] = useState(value);
  // 外部值变化（初始 GET 回填/保存后草稿复位）同步搜索框；手输失焦经 AutoComplete 提交回 value
  useEffect(() => {
    const selected = modelOptions.find((o) => o.value === value);
    setSearchValue(selected?.label || value || '');
  }, [value, modelOptions]);

  return (
    <AutoComplete
      selectedValue={value}
      onSelectedValueChange={(v) => {
        if (v === '') return; // 重选当前值的 reset()/失焦空串不清草稿（空值本就不合法）
        onChange(v);
      }}
      searchValue={searchValue}
      onSearchValueChange={setSearchValue}
      items={modelOptions}
      isLoading={isLoading}
      placeholder={placeholder}
      emptyMessage={emptyText}
      clearSearchOnFocus
    />
  );
}
