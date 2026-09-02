import { useState } from 'react';
import { IconCheck, IconCopy, IconMailPlus } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { apiRequest } from '@/lib/api-client';
import { useSelectedProjectId } from '@/stores/projectStore';
import { Button } from '@/components/ui/button';
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

// issue #128：邀请策略定稿为 7 天有效、仅可注册 1 次，不再让发起人选择
const INVITE_EXPIRES_IN_HOURS = 168;
const INVITE_MAX_USES = 1;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function UsersInviteDialog({ open, onOpenChange }: Props) {
  const { t } = useTranslation();
  const selectedProjectId = useSelectedProjectId();
  const [inviteLink, setInviteLink] = useState('');
  const [isCopied, setIsCopied] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const closeDialog = (nextOpen: boolean) => {
    if (!nextOpen) {
      setInviteLink('');
      setIsCopied(false);
    }
    onOpenChange(nextOpen);
  };

  const onSubmit = async () => {
    if (!selectedProjectId) {
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await apiRequest<{ token: string }>('/admin/invitations', {
        method: 'POST',
        requireAuth: true,
        headers: { 'X-Project-ID': selectedProjectId },
        body: {
          expiresInHours: INVITE_EXPIRES_IN_HOURS,
          maxUses: INVITE_MAX_USES,
        },
      });
      // 邀请链接指向当前控制台 origin 的 /sign-up?invite=<token>
      const url = new URL('/sign-up', window.location.origin);
      url.searchParams.set('invite', response.token);
      setInviteLink(url.toString());
      toast.success(t('users.messages.invitationCreated'));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('common.errors.internalServerError'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const copyInviteLink = async () => {
    try {
      await navigator.clipboard.writeText(inviteLink);
      setIsCopied(true);
      toast.success(t('users.messages.invitationCopied'));
    } catch {
      toast.error(t('common.errors.internalServerError'));
    }
  };

  return (
    <Dialog open={open} onOpenChange={closeDialog}>
      <DialogContent className='sm:max-w-md'>
        <DialogHeader className='text-left'>
          <DialogTitle className='flex items-center gap-2'>
            <IconMailPlus />
            {t('users.dialogs.invite.title')}
          </DialogTitle>
          <DialogDescription>{t('users.dialogs.invite.description')}</DialogDescription>
        </DialogHeader>
        {inviteLink && (
          <div className='space-y-3'>
            <Label htmlFor='invitation-link'>{t('users.form.invitationLink')}</Label>
            <div className='flex gap-2'>
              <Input id='invitation-link' value={inviteLink} readOnly />
              <Button type='button' size='icon' variant='outline' onClick={copyInviteLink} title={t('users.buttons.copyInvitationLink')}>
                {isCopied ? <IconCheck /> : <IconCopy />}
              </Button>
            </div>
            <p className='text-sm text-muted-foreground'>{t('users.messages.invitationLinkReady')}</p>
          </div>
        )}
        <DialogFooter>
          {inviteLink ? (
            <Button type='button' onClick={() => closeDialog(false)}>{t('common.buttons.close')}</Button>
          ) : (
            <>
              <DialogClose asChild>
                <Button variant='outline'>{t('common.buttons.cancel')}</Button>
              </DialogClose>
              <Button type='button' onClick={onSubmit} disabled={isSubmitting}>
                {t('users.buttons.createInvitation')}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
