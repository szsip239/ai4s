/**
 * 档位判定面板（智能路由决策链第 3 阶段）：threshold + escalate_conf。
 * 首轮 p_complex ≥ threshold 判 complex；simple 存态每轮复查、≥ escalate_conf 才升档
 * （shim route_resolve，永不降档）。
 */
import { useTranslation } from 'react-i18next';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Ai4sSettingsQueryState } from '../../rules/panels/QueryState';
import { RoutingSaveBar } from './SaveBar';
import { useRoutingDraft } from './useRoutingDraft';

export function Ai4sRoutingDecisionPanel({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const { t } = useTranslation();
  const { settings, putSettings, routing, dirty, formError, mutate, save } = useRoutingDraft(onDirtyChange);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('ai4s.smartRouting.panel.decision.title')}</CardTitle>
        <CardDescription>{t('ai4s.smartRouting.panel.decision.description')}</CardDescription>
      </CardHeader>
      <CardContent>
        <Ai4sSettingsQueryState isLoading={settings.isLoading} error={settings.error}>
          {routing && (
            <div className='space-y-6'>
              <div className='grid grid-cols-2 gap-4'>
                <div className='space-y-1.5'>
                  <Label>{t('ai4s.smartRouting.fields.threshold')}</Label>
                  <Input
                    type='number'
                    step='0.05'
                    min='0'
                    max='1'
                    value={routing.threshold}
                    onChange={(e) => mutate({ threshold: Number(e.target.value) })}
                  />
                </div>
                <div className='space-y-1.5'>
                  <Label>{t('ai4s.smartRouting.fields.escalateConf')}</Label>
                  <Input
                    type='number'
                    step='0.05'
                    min='0'
                    max='1'
                    value={routing.escalate_conf}
                    onChange={(e) => mutate({ escalate_conf: Number(e.target.value) })}
                  />
                </div>
              </div>
              <RoutingSaveBar formError={formError} dirty={dirty} pending={putSettings.isPending} onSave={save} />
            </div>
          )}
        </Ai4sSettingsQueryState>
      </CardContent>
    </Card>
  );
}
