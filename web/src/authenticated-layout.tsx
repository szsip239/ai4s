import { Outlet } from '@tanstack/react-router';
import { useVersionCheck } from '@/hooks/use-version-check';
import { SidebarProvider } from '@/components/ui/sidebar';
import { AppHeader } from '@/components/layout/app-header';
import { Ai4sTopNavBar } from '@/ai4s/layout/Ai4sTopNavBar';
import SkipToMain from '@/components/skip-to-main';
import { OnboardingProvider } from '@/features/onboarding';

interface Props {
  children?: React.ReactNode;
}

/**
 * ai4s C 结构布局（issue #11，挂载点 M3）：
 * 移除侧边栏，改为 AppHeader（h-14）+ Ai4sTopNavBar（h-11）两行固定头部。
 * 保留 SidebarProvider——AppHeader 内 useSidebar() 依赖其 context。
 */
export function AuthenticatedLayout({ children }: Props) {
  // Check for new version on mount (only for owners)
  useVersionCheck();

  return (
    <SidebarProvider className='h-screen flex-col overflow-hidden'>
      <AppHeader />
      <Ai4sTopNavBar />
      <div className='flex flex-1 overflow-hidden'>
        <SkipToMain />
        <div id='content' className='flex min-h-0 min-w-0 flex-1 flex-col overflow-auto pt-14 md:pt-[100px]'>
          <OnboardingProvider>{children ? children : <Outlet />}</OnboardingProvider>
        </div>
      </div>
    </SidebarProvider>
  );
}
