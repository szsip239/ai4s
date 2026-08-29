/**
 * 模型映射面板（智能路由决策链第 4 阶段）：tiers.simple / tiers.complex 两档目标模型。
 * combobox 下拉拉 axonhub /models 卡片（useQueryAllModels），允许手输；
 * 保存前 validateRouting 预检（模型名白名单，值进响应头防拆分）+ 服务端白名单兜底。
 */
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { useQueryAllModels } from '@/features/models/data/models';
import { Ai4sSettingsQueryState } from '../../rules/panels/QueryState';
import { ModelCombobox } from '../ModelCombobox';
import { RoutingSaveBar } from './SaveBar';
import { useRoutingDraft } from './useRoutingDraft';

export function Ai4sRoutingTiersPanel({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const { t } = useTranslation();
  const { settings, putSettings, routing, dirty, formError, mutate, save } = useRoutingDraft(onDirtyChange);
  const models = useQueryAllModels({});

  // combobox 建议=axonhub /models 卡片（modelID 去重排序）；加载失败/为空不挡手输
  const modelOptions = useMemo(() => {
    const ids = [...new Set((models.data?.edges ?? []).map((e) => e.node.modelID))].sort();
    return ids.map((id) => ({ value: id, label: id }));
  }, [models.data]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('ai4s.smartRouting.panel.tiers.title')}</CardTitle>
        <CardDescription>{t('ai4s.smartRouting.panel.tiers.description')}</CardDescription>
      </CardHeader>
      <CardContent>
        <Ai4sSettingsQueryState isLoading={settings.isLoading} error={settings.error}>
          {routing && (
            <div className='space-y-6'>
              <div className='grid grid-cols-1 gap-4 md:grid-cols-2'>
                <div className='space-y-1.5'>
                  <Label>{t('ai4s.smartRouting.fields.tierSimple')}</Label>
                  <ModelCombobox
                    value={routing.tiers.simple}
                    onChange={(v) => mutate({ tiers: { ...routing.tiers, simple: v } })}
                    modelOptions={modelOptions}
                    isLoading={models.isLoading}
                    placeholder={t('ai4s.smartRouting.fields.modelPlaceholder')}
                    emptyText={t('ai4s.smartRouting.fields.modelListEmpty')}
                  />
                </div>
                <div className='space-y-1.5'>
                  <Label>{t('ai4s.smartRouting.fields.tierComplex')}</Label>
                  <ModelCombobox
                    value={routing.tiers.complex}
                    onChange={(v) => mutate({ tiers: { ...routing.tiers, complex: v } })}
                    modelOptions={modelOptions}
                    isLoading={models.isLoading}
                    placeholder={t('ai4s.smartRouting.fields.modelPlaceholder')}
                    emptyText={t('ai4s.smartRouting.fields.modelListEmpty')}
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
