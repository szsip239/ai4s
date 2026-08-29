/**
 * 会话继承面板（智能路由决策链第 1 阶段）：session_ttl + tool_loop_lock + thinking_lock。
 * 对应 shim route_resolve 的会话 LRU（x-session-id 头 > metadata.session_id > 首轮 user 消息 sha256）
 * 与两道锁（tool-loop 硬锁 / thinking 锁，issue #117/#119）。
 */
import { useTranslation } from 'react-i18next';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Ai4sSettingsQueryState } from '../../rules/panels/QueryState';
import { RoutingSaveBar } from './SaveBar';
import { useRoutingDraft } from './useRoutingDraft';

export function Ai4sRoutingSessionPanel({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const { t } = useTranslation();
  const { settings, putSettings, routing, dirty, formError, mutate, save } = useRoutingDraft(onDirtyChange);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('ai4s.smartRouting.panel.session.title')}</CardTitle>
        <CardDescription>{t('ai4s.smartRouting.panel.session.description')}</CardDescription>
      </CardHeader>
      <CardContent>
        <Ai4sSettingsQueryState isLoading={settings.isLoading} error={settings.error}>
          {routing && (
            <div className='space-y-6'>
              <div className='max-w-56 space-y-1.5'>
                <Label>{t('ai4s.smartRouting.fields.sessionTtl')}</Label>
                <Input
                  type='number'
                  min='1'
                  step='1'
                  value={routing.session_ttl}
                  onChange={(e) => mutate({ session_ttl: Number(e.target.value) })}
                />
              </div>
              <div className='flex items-center justify-between gap-4'>
                <div>
                  <div className='font-medium'>{t('ai4s.smartRouting.fields.toolLoopLock')}</div>
                  <div className='text-muted-foreground text-sm'>{t('ai4s.smartRouting.fields.toolLoopLockHint')}</div>
                </div>
                <Switch checked={routing.tool_loop_lock} onCheckedChange={(c) => mutate({ tool_loop_lock: c })} />
              </div>
              <div className='flex items-center justify-between gap-4'>
                <div>
                  <div className='font-medium'>{t('ai4s.smartRouting.fields.thinkingLock')}</div>
                  <div className='text-muted-foreground text-sm'>{t('ai4s.smartRouting.fields.thinkingLockHint')}</div>
                </div>
                <Switch checked={routing.thinking_lock} onCheckedChange={(c) => mutate({ thinking_lock: c })} />
              </div>
              <RoutingSaveBar formError={formError} dirty={dirty} pending={putSettings.isPending} onSave={save} />
            </div>
          )}
        </Ai4sSettingsQueryState>
      </CardContent>
    </Card>
  );
}
