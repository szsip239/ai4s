/**
 * 面板查询态共享件（issue #36 review #7）：五个可配面板重复的 Skeleton/错误 Alert 段收敛于此。
 * 缺省错误展示为 destructive Alert + API error 原因；特殊面板（如 settings 404 兜底态）经 renderError 自定义。
 * Ai4sSettingsQueryState（issue #38）：judge/PG/设置三面板同款 settings 查询态（404=env 兜底态给指引）。
 */
import type { ReactNode } from 'react';
import { IconAlertTriangle } from '@tabler/icons-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Skeleton } from '@/components/ui/skeleton';
import { DlpApiError } from '../api';

export function Ai4sQueryState({
  isLoading,
  error,
  errorTitle,
  rows = 3,
  renderError,
  children,
}: {
  isLoading: boolean;
  error: unknown;
  errorTitle: string;
  /** 加载骨架行数（对齐各面板原行数） */
  rows?: number;
  /** 自定义错误展示；缺省 destructive Alert（errorTitle + 原因） */
  renderError?: (error: unknown) => ReactNode;
  children: ReactNode;
}) {
  if (isLoading) {
    return (
      <div className='space-y-2'>
        {Array.from({ length: rows }).map((_, i) => (
          <Skeleton key={i} className='h-10 w-full' />
        ))}
      </div>
    );
  }
  if (error) {
    if (renderError) return <>{renderError(error)}</>;
    return (
      <Alert variant='destructive'>
        <AlertTitle>{errorTitle}</AlertTitle>
        <AlertDescription>{error instanceof Error ? error.message : String(error)}</AlertDescription>
      </Alert>
    );
  }
  return <>{children}</>;
}

/** settings 查询态共享件（issue #38）：judge/PG/设置三面板同款——
 * 404 = settings.json 缺失（env 兜底态，合法）给恢复指引；非 404 故障 destructive 报错不加指引。 */
export function Ai4sSettingsQueryState({
  isLoading,
  error,
  rows = 4,
  children,
}: {
  isLoading: boolean;
  error: unknown;
  rows?: number;
  children: ReactNode;
}) {
  return (
    <Ai4sQueryState
      isLoading={isLoading}
      error={error}
      errorTitle='settings 加载失败'
      rows={rows}
      renderError={(err) =>
        err instanceof DlpApiError && err.status === 404 ? (
          <Alert>
            <IconAlertTriangle className='size-4' />
            <AlertTitle>settings.json 不存在（env 兜底态）</AlertTitle>
            <AlertDescription>
              当前 shim 以 env/内置默认运行；请在部署侧恢复 deploy/dlp/settings.json 后再于本页维护。
            </AlertDescription>
          </Alert>
        ) : (
          <Alert variant='destructive'>
            <AlertTitle>settings 加载失败</AlertTitle>
            <AlertDescription>{err instanceof Error ? err.message : String(err)}</AlertDescription>
          </Alert>
        )
      }
    >
      {children}
    </Ai4sQueryState>
  );
}
