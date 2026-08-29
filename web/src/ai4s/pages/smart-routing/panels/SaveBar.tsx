/**
 * 智能路由面板保存行共享件：行内红字（预检失败原因）+ 未保存提示 + 保存按钮。
 * 四个配置面板同款收尾（先例为原单卡页的内联保存行，拆面板时收敛于此）。
 */
import { IconLoader2 } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';

export function RoutingSaveBar({
  formError,
  dirty,
  pending,
  onSave,
}: {
  formError: string | null;
  dirty: boolean;
  pending: boolean;
  onSave: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className='flex items-center justify-end gap-3'>
      {formError && <span className='text-destructive text-sm'>{formError}</span>}
      {dirty && !formError && <span className='text-sm text-amber-600'>{t('ai4s.smartRouting.unsaved')}</span>}
      <Button onClick={onSave} disabled={!dirty || pending}>
        {pending && <IconLoader2 className='animate-spin' />}
        {t('ai4s.smartRouting.save')}
      </Button>
    </div>
  );
}
