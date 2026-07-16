import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

export interface NavItem {
  to: string;
  label: string;
}

/** 대시보드 5대 화면(M11~M13). App.tsx 라우트와 1:1 대응. */
export const NAV: NavItem[] = [
  { to: "/kpi", label: "KPI" },
  { to: "/inspections", label: "검사이력" },
  { to: "/statistics", label: "불량통계" },
  { to: "/report", label: "월간리포트" },
  { to: "/master", label: "기준정보" },
];

/** 현재 경로에 대응하는 NAV 라벨(헤더에 "지금 어디 있는지" 표시용). 매칭 없으면 빈 문자열. */
export function useCurrentNavLabel(): string {
  const { pathname } = useLocation();
  const match = NAV.find((n) => pathname === n.to || pathname.startsWith(`${n.to}/`));
  return match?.label ?? "";
}

/**
 * 대시보드 심플 네비게이션(사용자 요청: "평소엔 심플, 필요할 때만 메뉴").
 * 평소엔 "메뉴" 버튼만 노출되고, 클릭 시 5개 화면이 세로 드롭다운으로 펼쳐진다.
 * 바깥 클릭/Esc로 닫히고, 항목 선택 시 이동과 동시에 자동으로 닫힌다.
 */
export function NavMenu(): JSX.Element {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const { pathname } = useLocation();

  // 경로가 바뀌면(항목 클릭으로 이동 등) 메뉴를 닫는다.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!open) return;
    function onDocMouseDown(e: MouseEvent): void {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocMouseDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        className="btn-ghost"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="dashboard-nav-menu"
        data-testid="nav-menu-button"
        onClick={() => setOpen((v) => !v)}
      >
        <span aria-hidden="true">&#9776;</span> 메뉴
      </button>
      {open && (
        <nav
          id="dashboard-nav-menu"
          role="menu"
          aria-label="대시보드 메뉴"
          data-testid="nav-menu"
          className="absolute left-0 top-full z-20 mt-1 w-40 rounded-md border border-slate-200 bg-white py-1 shadow-lg"
        >
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              role="menuitem"
              className={({ isActive }) =>
                `block px-3 py-2 text-sm font-medium ${
                  isActive ? "bg-brand text-white" : "text-slate-600 hover:bg-slate-100"
                }`
              }
              onClick={() => setOpen(false)}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
      )}
    </div>
  );
}
