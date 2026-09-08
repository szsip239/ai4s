import { format } from 'date-fns';
import { ColumnDef, Table, Row } from '@tanstack/react-table';
import { useTranslation } from 'react-i18next';
import { extractNumberID } from '@/lib/utils';
import { Checkbox } from '@/components/ui/checkbox';
import { DataTableColumnHeader } from '@/components/data-table-column-header';
import LongText from '@/components/long-text';
import { ApiKey } from '../data/schema';
import { DataTableRowActions } from './data-table-row-actions';

// issue #138：列表不再有 key 明文列（列表查询不下发明文）——
// 打码片段/复制按钮随列一并移除；刻意查看单条走行操作「查看」→ 查看对话框（单条查询取明文）

export const createColumns = (t: ReturnType<typeof useTranslation>['t'], canWrite: boolean = true, canViewCreators: boolean = false): ColumnDef<ApiKey>[] => [
  ...(canWrite
    ? [
        {
          id: 'select',
          header: ({ table }: { table: Table<ApiKey> }) => (
            <Checkbox
              checked={table.getIsAllPageRowsSelected() || (table.getIsSomePageRowsSelected() && 'indeterminate')}
              onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
              aria-label={t('common.columns.selectAll')}
              className='translate-y-[2px]'
            />
          ),
          cell: ({ row }: { row: Row<ApiKey> }) => (
            <Checkbox
              checked={row.getIsSelected()}
              onCheckedChange={(value) => row.toggleSelected(!!value)}
              aria-label={t('common.columns.selectRow')}
              className='translate-y-[2px]'
            />
          ),
          enableSorting: false,
          enableHiding: false,
        },
      ]
    : []),
  {
    accessorKey: 'id',
    header: ({ column }) => <DataTableColumnHeader column={column} title={t('common.columns.id')} />,
    cell: ({ row }) => <div className='font-mono text-xs'>#{extractNumberID(row.getValue('id'))}</div>,
    enableSorting: false,
  },
  {
    accessorKey: 'name',
    header: ({ column }) => <DataTableColumnHeader column={column} title={t('common.columns.name')} />,
    cell: ({ row }) => <LongText className='max-w-36 font-medium'>{row.getValue('name')}</LongText>,
    meta: {
      className: 'md:table-cell',
    },
    filterFn: (row, _id, value) => {
      return String(row.getValue('name')).toLowerCase().includes(String(value).toLowerCase());
    },
    enableHiding: false,
  },
  ...(canViewCreators
    ? ([
        {
          accessorKey: 'creator',
          header: ({ column }) => <DataTableColumnHeader column={column} title={t('apikeys.columns.creator')} />,
          cell: ({ row }) => {
            const creator = row.original.user;
            const displayName = creator ? `${creator.firstName} ${creator.lastName}` : t('apikeys.user.deleted');
            return <LongText className='text-muted-foreground max-w-24'>{displayName}</LongText>;
          },
          filterFn: (row, _id, value) => {
            const creator = row.original.user;
            if (!creator) return false;
            return value.includes(creator.id);
          },
          enableSorting: false,
        },
      ] as ColumnDef<ApiKey>[])
    : []),
  {
    accessorKey: 'type',
    header: ({ column }) => <DataTableColumnHeader column={column} title={t('apikeys.columns.type')} />,
    cell: ({ row }) => {
      const type = row.getValue('type') as string;
      const typeText =
        {
          user: t('apikeys.type.user'),
          personal: t('apikeys.type.personal'),
          service_account: t('apikeys.type.service_account'),
          noauth: t('apikeys.type.noauth'),
        }[type] || type;

      const typeColor =
        {
          user: 'text-blue-600',
          personal: 'text-emerald-600',
          service_account: 'text-purple-600',
        }[type] || 'text-muted-foreground';

      return <div className={`text-sm ${typeColor}`}>{typeText}</div>;
    },
    filterFn: (row, _id, value) => {
      return value.includes(row.getValue('type'));
    },
    enableSorting: false,
  },
  {
    accessorKey: 'status',
    header: ({ column }) => <DataTableColumnHeader column={column} title={t('common.columns.status')} />,
    cell: ({ row }) => {
      const status = row.getValue('status') as string;
      const statusText =
        {
          enabled: t('apikeys.status.enabled'),
          disabled: t('apikeys.status.disabled'),
          archived: t('apikeys.status.archived'),
        }[status] || t('apikeys.status.disabled');

      const statusColor =
        {
          enabled: 'text-green-600',
          disabled: 'text-red-600',
          archived: 'text-orange-600',
        }[status] || 'text-red-600';

      return <div className={`text-sm ${statusColor}`}>{statusText}</div>;
    },
    filterFn: (row, _id, value) => {
      return value.includes(row.getValue('status'));
    },
    enableSorting: false,
  },
  {
    accessorKey: 'createdAt',
    header: ({ column }) => <DataTableColumnHeader column={column} title={t('common.columns.createdAt')} />,
    cell: ({ row }) => {
      const date = row.getValue('createdAt') as Date;
      return <div className='text-muted-foreground'>{format(date, 'yyyy-MM-dd HH:mm')}</div>;
    },
  },
  {
    accessorKey: 'updatedAt',
    header: ({ column }) => <DataTableColumnHeader column={column} title={t('common.columns.updatedAt')} />,
    cell: ({ row }) => {
      const date = row.getValue('updatedAt') as Date;
      return <div className='text-muted-foreground'>{format(date, 'yyyy-MM-dd HH:mm')}</div>;
    },
  },
  {
    id: 'actions',
    header: t('common.columns.actions'),
    cell: DataTableRowActions,
  },
];
