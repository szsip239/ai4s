import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { useApiKeysContext } from '../context/apikeys-context';
import { useUpdateApiKey } from '../data/apikeys';
import { UpdateApiKeyInput, updateApiKeyInputSchemaFactory } from '../data/schema';
import { ScopesSelect } from '@/components/scopes-select';

export function ApiKeysEditDialog() {
  const { t } = useTranslation();
  const { isDialogOpen, closeDialog, selectedApiKey } = useApiKeysContext();
  const updateApiKey = useUpdateApiKey();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [dialogContent, setDialogContent] = useState<HTMLDivElement | null>(null);
  const [ipRestrictionEnabled, setIPRestrictionEnabled] = useState(false);
  const [ipInput, setIpInput] = useState('');

  const form = useForm<UpdateApiKeyInput>({
    resolver: zodResolver(updateApiKeyInputSchemaFactory(t)),
    defaultValues: {
      name: '',
      scopes: [],
      allowedIps: [],
    },
  });

  useEffect(() => {
    if (selectedApiKey && isDialogOpen.edit) {
      const allowedIps = selectedApiKey.allowedIps ?? [];
      setIPRestrictionEnabled(allowedIps.length > 0);
      setIpInput(allowedIps.join(', '));
      form.reset({
        name: selectedApiKey.name,
        scopes: selectedApiKey.scopes || [],
        allowedIps,
      });
    }
  }, [selectedApiKey, isDialogOpen.edit, form]);

  const onSubmit = async (data: UpdateApiKeyInput) => {
    if (!selectedApiKey) return;

    setIsSubmitting(true);
    try {
      const allowedIps = ipRestrictionEnabled
        ? ipInput
            .split(',')
            .map((s) => s.trim())
            .filter((s) => s !== '')
        : [];

      const input: UpdateApiKeyInput = {
        name: data.name,
        allowedIps,
      };

      if (selectedApiKey.type === 'service_account') {
        input.scopes = data.scopes;
      }

      await updateApiKey.mutateAsync({
        id: selectedApiKey.id,
        input,
      });

      closeDialog('edit');
    } catch (error) {
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleClose = () => {
    form.reset();
    closeDialog('edit');
  };

  const isServiceAccount = selectedApiKey?.type === 'service_account';

  return (
    <Dialog open={isDialogOpen.edit} onOpenChange={handleClose}>
      <DialogContent className='flex max-h-[90vh] flex-col sm:max-w-[600px]' ref={setDialogContent}>
        <DialogHeader>
          <DialogTitle>{t('apikeys.dialogs.edit.title')}</DialogTitle>
          <DialogDescription>{t('apikeys.dialogs.edit.description')}</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className='space-y-4'>
            <FormField
              control={form.control}
              name='name'
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t('apikeys.dialogs.fields.name.label')}</FormLabel>
                  <FormControl>
                    <Input placeholder={t('apikeys.dialogs.fields.name.placeholder')} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            {isServiceAccount && (
              <FormField
                control={form.control}
                name='scopes'
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t('apikeys.dialogs.fields.scopes.label')}</FormLabel>
                    <FormControl>
                      <ScopesSelect value={field.value || []} onChange={field.onChange} portalContainer={dialogContent} />
                    </FormControl>
                  </FormItem>
                )}
              />
            )}
            <div className='space-y-3 rounded-lg border p-4'>
              <div className='flex items-center justify-between'>
                <div className='space-y-0.5'>
                  <FormLabel>{t('apikeys.dialogs.fields.ipRestriction.label')}</FormLabel>
                  <FormDescription>{t('apikeys.dialogs.fields.ipRestriction.description')}</FormDescription>
                </div>
                <Switch checked={ipRestrictionEnabled} onCheckedChange={setIPRestrictionEnabled} />
              </div>
              {ipRestrictionEnabled && (
                <FormItem>
                  <FormLabel>{t('apikeys.dialogs.fields.ipRestriction.cidrsLabel')}</FormLabel>
                  <FormControl>
                    <Input
                      placeholder={t('apikeys.dialogs.fields.ipRestriction.cidrsPlaceholder')}
                      value={ipInput}
                      onChange={(e) => setIpInput(e.target.value)}
                    />
                  </FormControl>
                  <FormDescription>{t('apikeys.dialogs.fields.ipRestriction.cidrsDescription')}</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            </div>
            <DialogFooter className='flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-end'>
              <div className='flex w-full gap-2 sm:w-auto'>
                <Button type='button' variant='outline' onClick={handleClose} disabled={isSubmitting}>
                  {t('common.buttons.cancel')}
                </Button>
                <Button type='submit' disabled={isSubmitting}>
                  {isSubmitting ? t('common.buttons.saving') : t('common.buttons.save')}
                </Button>
              </div>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
