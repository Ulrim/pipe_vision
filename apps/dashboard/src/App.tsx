import type { ReactElement } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuthStore, canEdit } from "@/store/auth";
import { InspectionsPage } from "@/pages/InspectionsPage";
import { StatisticsPage } from "@/pages/StatisticsPage";
import { KpiPage } from "@/pages/KpiPage";
import { LivePage } from "@/pages/LivePage";
import { ReportPage } from "@/pages/ReportPage";
import { LabelingPage } from "@/pages/LabelingPage";
import { MasterPage } from "@/pages/MasterPage";
import { MonitorPage } from "@/pages/MonitorPage";
import { UpdatePage } from "@/pages/UpdatePage";
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
  // 실시간 현황은 벽걸이 모니터(15.6" FHD 이상)에 띄워 두는 화면이라 폭 제한을
  // 풀어 사진을 크게 쓴다. 나머지 화면은 읽기 좋은 폭(7xl)을 유지한다.
  const wide = useLocation().pathname.startsWith("/live");

  return (
    <div className="flex min-h-full flex-col">
      <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-3 sm:gap-4 sm:px-5">
        <NavMenu />
        {/* 좁은 화면에서 제목이 글자 단위로 꺾이지 않게 한 줄 고정 + 말줄임. */}
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="text-sm font-bold text-brand">AIVIS</span>
          {currentLabel && (
            <h1 className="truncate whitespace-nowrap text-lg font-bold text-slate-800">
              {currentLabel}
            </h1>
          )}
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-3 whitespace-nowrap text-sm">
          {username ? (
            <>
              <span className="hidden text-slate-500 sm:inline">
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

      <main className={`mx-auto w-full flex-1 p-4 sm:p-5 ${wide ? "max-w-none" : "max-w-7xl"}`}>
        <Routes>
          {/* 첫 화면은 실시간 현황 — 파이 여러 대가 지금 어떤지가 가장 먼저 궁금하다. */}
          <Route path="/" element={<Navigate to="/live" replace />} />
          <Route
            path="/live"
            element={
              <ProtectedRoute>
                <LivePage />
              </ProtectedRoute>
            }
          />
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
            path="/labeling"
            element={
              <ProtectedRoute>
                <LabelingPage />
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
          <Route
            path="/monitor"
            element={
              <ProtectedRoute>
                <MonitorPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/update"
            element={
              <ProtectedRoute>
                <UpdatePage />
              </ProtectedRoute>
            }
          />
          <Route path="/login" element={<LoginPage />} />
          <Route path="*" element={<Navigate to="/live" replace />} />
        </Routes>
      </main>
    </div>
  );
}
