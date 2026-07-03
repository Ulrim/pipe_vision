/**
 * 헤더 로그인 상태/역할 표시 + 로그인/로그아웃 버튼 (CLAUDE.md §5 M10).
 * 현장 가독성 우선 — 큰 텍스트/버튼, 역할은 한글 라벨.
 */
import { Role } from "@aivis/shared-types";
import { useAuthStore } from "@/store/authStore";

const ROLE_LABEL: Record<Role, string> = {
  [Role.OPERATOR]: "작업자",
  [Role.QUALITY]: "품질관리자",
  [Role.ADMIN]: "관리자",
};

export function AuthStatus() {
  const session = useAuthStore((s) => s.session);
  const openLoginPrompt = useAuthStore((s) => s.openLoginPrompt);
  const logout = useAuthStore((s) => s.logout);

  if (!session) {
    return (
      <button
        type="button"
        onClick={openLoginPrompt}
        className="rounded-lg bg-blue-600 px-4 py-2 font-bold text-white shadow-sm active:scale-95"
        data-testid="auth-login-button"
      >
        로그인
      </button>
    );
  }

  return (
    <div
      className="inline-flex items-center gap-3 rounded-lg bg-white/80 px-3 py-2 shadow-sm"
      data-testid="auth-status"
      data-role={session.role}
    >
      <span className="font-semibold text-gray-800">
        {session.username}
        <span className="ml-2 rounded bg-gray-200 px-2 py-0.5 text-sm font-bold text-gray-700">
          {ROLE_LABEL[session.role] ?? session.role}
        </span>
      </span>
      <button
        type="button"
        onClick={logout}
        className="rounded-lg border-2 border-gray-300 px-3 py-1 font-bold text-gray-700 active:scale-95"
        data-testid="auth-logout-button"
      >
        로그아웃
      </button>
    </div>
  );
}
