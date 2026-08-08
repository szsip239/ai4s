/**
 * 面板查询态共享件（issue #36 review #7）：五个可配面板重复的 Skeleton/错误 Alert 段收敛于此。
 * 缺省错误展示为 destructive Alert + API error 原因；特殊面板（如 settings 404 兜底态）经 renderError 自定义。
 */
import type { ReactNode } from 'react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Skeleton } from '@/components/ui/skeleton';

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
