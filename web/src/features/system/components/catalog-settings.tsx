'use client';

import { useEffect, useState } from 'react';
import { Loader2, Save } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useCatalogSettings, useUpdateCatalogSettings } from '../data/system';

export function CatalogSettings() {
  const { t } = useTranslation();
  const { data, isLoading } = useCatalogSettings();
  const updateSettings = useUpdateCatalogSettings();
  const [upstreamURL, setUpstreamURL] = useState('');
  const canSave = Boolean(data) && !updateSettings.isPending;

  useEffect(() => {
    if (!data) {
      return;
    }

    setUpstreamURL(data.upstreamURL);
  }, [data]);

  if (isLoading) {
    return (
      <div className='flex h-24 items-center justify-center'>
        <Loader2 className='h-5 w-5 animate-spin' />
      </div>
    );
  }

  const handleSave = async () => {
    if (!data) {
      return;
    }

    await updateSettings.mutateAsync({
      upstreamURL: upstreamURL.trim(),
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('system.catalog.title')}</CardTitle>
        <CardDescription>{t('system.catalog.description')}</CardDescription>
      </CardHeader>
      <CardContent className='space-y-6'>
        <div className='space-y-2'>
          <Label htmlFor='catalog-upstream-url'>{t('system.catalog.upstreamURL.label')}</Label>
          <Input
            id='catalog-upstream-url'
            value={upstreamURL}
            onChange={(event) => setUpstreamURL(event.target.value)}
            placeholder='https://raw.githubusercontent.com/ThinkInAIXYZ/PublicProviderConf/refs/heads/dev/dist/all.json'
          />
          <p className='text-muted-foreground text-sm'>{t('system.catalog.upstreamURL.description')}</p>
        </div>
        <div className='flex flex-wrap gap-2'>
          <Button onClick={handleSave} disabled={!canSave}>
            {updateSettings.isPending ? <Loader2 className='mr-2 h-4 w-4 animate-spin' /> : <Save className='mr-2 h-4 w-4' />}
            {t('system.buttons.save')}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
