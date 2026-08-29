/**
 * 分类器面板（智能路由决策链第 2 阶段）：timeout + max_concurrency + prompt。
 * 分类通道沿用 settings judge.*（与商密/注入 judge 同通道，shim router_classify），
 * 面板只读展示当前通道模型并指路到脱敏规则 judge 面板维护。
 */
import { useTranslation } from 'react-i18next';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Ai4sSettingsQueryState } from '../../rules/panels/QueryState';
import { RoutingSaveBar } from './SaveBar';
import { useRoutingDraft } from './useRoutingDraft';

export function Ai4sRoutingClassifyPanel({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const { t } = useTranslation();
  const { settings, putSettings, routing, dirty, formError, mutate, save } = useRoutingDraft(onDirtyChange);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('ai4s.smartRouting.panel.classify.title')}</CardTitle>
        <CardDescription>{t('ai4s.smartRouting.panel.classify.description')}</CardDescription>
      </CardHeader>
      <CardContent>
        <Ai4sSettingsQueryState isLoading={settings.isLoading} error={settings.error}>
          {routing && (
            <div className='space-y-6'>
              <p className='text-muted-foreground text-sm'>
                {t('ai4s.smartRouting.panel.classify.channel', {
                  model: settings.data?.judge.model ?? '—',
                })}
              </p>
              <div className='grid grid-cols-2 gap-4'>
                <div className='space-y-1.5'>
                  <Label>{t('ai4s.smartRouting.fields.timeout')}</Label>
                  <Input
                    type='number'
                    min='0'
                    step='0.5'
                    value={routing.timeout}
                    onChange={(e) => mutate({ timeout: Number(e.target.value) })}
                  />
                </div>
                <div className='space-y-1.5'>
                  <Label>{t('ai4s.smartRouting.fields.maxConcurrency')}</Label>
                  <Input
                    type='number'
                    min='1'
                    step='1'
                    value={routing.max_concurrency}
                    onChange={(e) => mutate({ max_concurrency: Number(e.target.value) })}
                  />
                </div>
              </div>
              <div className='space-y-1.5'>
                <Label>{t('ai4s.smartRouting.fields.prompt')}</Label>
                <Textarea
                  rows={7}
                  className='font-mono text-xs'
                  value={routing.prompt}
                  onChange={(e) => mutate({ prompt: e.target.value })}
                />
              </div>
              <RoutingSaveBar formError={formError} dirty={dirty} pending={putSettings.isPending} onSave={save} />
            </div>
          )}
        </Ai4sSettingsQueryState>
      </CardContent>
    </Card>
  );
}
