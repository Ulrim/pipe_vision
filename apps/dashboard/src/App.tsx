import type { ReactElement } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuthStore, canEdit } from "@/store/auth";
import { InspectionsPage } from "@/pages/InspectionsPage";
import { StatisticsPage } from "@/pages/StatisticsPage";
import { KpiPage } from "@/pages/KpiPage";
import { ReportPage } from "@/pages/ReportPage";
import { MasterPage } from "@/pages/MasterPage";
import { LoginPage } from "@/pages/LoginPage";

const NAV = [
  { to: "/kpi", label: "KPI" },
  { to: "/inspections", label: "검사이력" },
  { to: "/statistics", label: "불량통계" },
  { to: "/report", label: "월간리포트" },
  { to: "/master", label: "기준정보" },
];

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

  return (
    <div className="flex min-h-full flex-col">
      <header className="flex items-center gap-6 border-b border-slate-200 bg-white px-5 py-3">
        <div className="text-lg font-bold text-brand">AIVIS 관리자 대시보드</div>
        <nav className="flex gap-1">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                `rounded-md px-3 py-1.5 text-sm font-medium ${
                  isActive ? "bg-brand text-white" : "text-slate-600 hover:bg-slate-100"
                }`
              }
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
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
