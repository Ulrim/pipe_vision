import type { ReactElement } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuthStore, canEdit } from "@/store/auth";
import { InspectionsPage } from "@/pages/InspectionsPage";
import { StatisticsPage } from "@/pages/StatisticsPage";
import { KpiPage } from "@/pages/KpiPage";
import { ReportPage } from "@/pages/ReportPage";
import { MasterPage } from "@/pages/MasterPage";
import { LoginPage } from "@/pages/LoginPage";
import { NavMenu, useCurrentNavLabel } from "@/components/NavMenu";

/**
 * 사내 도구 — 전체 로그인 필수(§14 RBAC).
 * 미인증(토큰 없음) 사용자가 보호 경로 접근 시 /login 으로 강제 리다이렉트.
 * 토큰 만료/401 시 api client 가 auth 스토어를 비우므로 다음 렌더에서 여기로 복귀한다.
 */
function ProtectedRoute({ children }: { children: ReactElement }): ReactElement {
  const token = useAuthStore((s) => s.token);
  const location = useLocation();
  if (!token) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return children;
}

export default function App(): JSX.Element {
  const { username, role, clear } = useAuthStore();
  const currentLabel = useCurrentNavLabel();

  return (
    <div className="flex min-h-full flex-col">
      <header className="flex items-center gap-4 border-b border-slate-200 bg-white px-5 py-3">
        <NavMenu />
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-bold text-brand">AIVIS</span>
          {currentLabel && (
            <h1 className="text-lg font-bold text-slate-800">{currentLabel}</h1>
          )}
        </div>
        <div className="ml-auto flex items-center gap-3 text-sm">
          {username ? (
            <>
              <span className="text-slate-500">
                {username}
                <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 text-xs">
                  {role}
                </span>
                {canEdit(role) && (
                  <span className="ml-1 text-xs text-pass">편집권한</span>
                )}
              </span>
              <button type="button" className="btn-ghost" onClick={clear}>
                로그아웃
              </button>
            </>
          ) : (
            <NavLink to="/login" className="btn-ghost">
              로그인
            </NavLink>
          )}
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl flex-1 p-5">
        <Routes>
          <Route path="/" element={<Navigate to="/kpi" replace />} />
          <Route
            path="/kpi"
            element={
              <ProtectedRoute>
                <KpiPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/inspections"
            element={
              <ProtectedRoute>
                <InspectionsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/statistics"
            element={
              <ProtectedRoute>
                <StatisticsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/report"
            element={
              <ProtectedRoute>
                <ReportPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/master"
            element={
              <ProtectedRoute>
                <MasterPage />
              </ProtectedRoute>
            }
          />
          <Route path="/login" element={<LoginPage />} />
          <Route path="*" element={<Navigate to="/kpi" replace />} />
        </Routes>
      </main>
    </div>
  );
}
