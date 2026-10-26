import type { ReactNode } from "react";

import { AuthNav } from "../features/auth/auth-nav";
import { SiteNavLinks, SiteStatus } from "./site-nav-links";
import { SiteBackground } from "./site-background";

type AppShellProps = {
  children: ReactNode;
};

export async function AppShell({ children }: AppShellProps) {
  return (
    <div className="site-layout">
      <SiteBackground />
      <header className="site-header">
        <div className="page-shell">
          <div className="site-header-inner">
            <div className="site-brand">
              <div className="site-brand-title">终末地战斗日志</div>
              <div className="site-brand-subtitle">Endfield Battle Reports</div>
            </div>
            <nav className="site-nav">
              <SiteNavLinks />
              <AuthNav />
            </nav>
          </div>
          <SiteStatus />
        </div>
      </header>
      <main className="page-shell page-content">{children}</main>
    </div>
  );
}
