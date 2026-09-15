import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { IconCheck, IconCopy, IconMailPlus } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { graphqlRequest } from '@/gql/graphql';
import { apiRequest } from '@/lib/api-client';
import { Button } from '@/components/ui/button';
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

// issue #128：邀请策略定稿为 7 天有效、仅可注册 1 次，不再让发起人选择
const INVITE_EXPIRES_IN_HOURS = 168;
const INVITE_MAX_USES = 1;
// issue #128：邀请一律绑隔离项目，注册后零能力，Key 走审批签发
const QUARANTINE_PROJECT_NAME = 'External-Quarantine';
// beta7 起邀请创建强制 roleID；绑隔离项目内零 scopes 的 Invitee 角色，保持落地零能力
const INVITEE_ROLE_NAME = 'Invitee';

const INVITE_PREREQS_QUERY = `
  query InvitePrereqs($first: Int, $roleName: String) {
    projects(first: $first) {
      edges {
        node {
          id
          name
        }
      }
    }
    roles(first: $first, where: { name: $roleName }) {
      edges {
        node {
          id
          name
          projectID
        }
      }
    }
  }
`;

interface InvitePrereqsResult {
  projects: {
    edges: Array<{ node: { id: string; name: string } }>;
  };
  roles: {
    edges: Array<{ node: { id: string; name: string; projectID: string } }>;
  };
}

// gid://axonhub/Role/20 -> 20；REST /admin/invitations 的 roleID 是数字主键
function parseNumericId(gid: string): number | null {
  const id = Number(gid.split('/').pop());
  return Number.isInteger(id) && id > 0 ? id : null;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function UsersInviteDialog({ open, onOpenChange }: Props) {
  const { t } = useTranslation();
  const [inviteLink, setInviteLink] = useState('');
  const [isCopied, setIsCopied] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { data: prereqs, isError: isPrereqsQueryError } = useQuery({
    queryKey: ['invite-prereqs', QUARANTINE_PROJECT_NAME, INVITEE_ROLE_NAME],
    enabled: open,
    staleTime: 60_000,
    queryFn: async () => {
      const result = await graphqlRequest<InvitePrereqsResult>(INVITE_PREREQS_QUERY, {
        first: 100,
        roleName: INVITEE_ROLE_NAME,
      });
      const projectId =
        result.projects.edges.find((edge) => edge.node.name === QUARANTINE_PROJECT_NAME)?.node.id ?? null;
      const roleGid = projectId
        ? (result.roles.edges.find((edge) => edge.node.projectID === projectId)?.node.id ?? null)
        : null;
      return {
        projectId,
        roleId: roleGid ? parseNumericId(roleGid) : null,
      };
    },
  });
  const quarantineProjectId = prereqs?.projectId ?? null;
  const inviteeRoleId = prereqs?.roleId ?? null;

  const closeDialog = (nextOpen: boolean) => {
    if (!nextOpen) {
      setInviteLink('');
      setIsCopied(false);
    }
    onOpenChange(nextOpen);
  };

  const onSubmit = async () => {
    if (!quarantineProjectId || !inviteeRoleId) {
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await apiRequest<{ token: string }>('/admin/invitations', {
        method: 'POST',
        requireAuth: true,
        headers: { 'X-Project-ID': quarantineProjectId },
        body: {
          expiresInHours: INVITE_EXPIRES_IN_HOURS,
          maxUses: INVITE_MAX_USES,
          roleID: inviteeRoleId,
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

  const quarantineMissing = isPrereqsQueryError || (prereqs !== undefined && quarantineProjectId === null);
  const inviteeRoleMissing = !quarantineMissing && prereqs !== undefined && inviteeRoleId === null;

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
        {inviteLink ? (
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
        ) : (
          <div className='space-y-2 text-sm'>
            <p>
              {t('users.dialogs.invite.quarantineProject')}：{QUARANTINE_PROJECT_NAME}
            </p>
            {quarantineMissing && <p className='text-destructive'>{t('users.dialogs.invite.quarantineMissing')}</p>}
            {inviteeRoleMissing && <p className='text-destructive'>{t('users.dialogs.invite.inviteeRoleMissing')}</p>}
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
              <Button type='button' onClick={onSubmit} disabled={isSubmitting || !quarantineProjectId || !inviteeRoleId}>
                {t('users.buttons.createInvitation')}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
