/**
 * 작업자 로그인 모달 (CLAUDE.md §5 M10, §7.4 POST /auth/login).
 *
 * 현장 단말: username/password 입력 → 토큰/역할 저장(한 번 로그인 후 유지).
 * 쓰기 액션(재확인)에서 미인증/401 시 자동으로 열린다.
 * 큰 폰트/버튼 — 현장 가독성·터치 최적화.
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import type { TokenResponse } from "@aivis/shared-types";
import { login, ApiError } from "@/api/client";
import { useAuthStore } from "@/store/authStore";

export interface LoginModalProps {
  /** 로그인 성공 후 콜백(예: 보류했던 액션 재시도). */
  onSuccess?: (token: TokenResponse) => void;
}

export function LoginModal({ onSuccess }: LoginModalProps) {
  const setSession = useAuthStore((s) => s.setSession);
  const closeLoginPrompt = useAuthStore((s) => s.closeLoginPrompt);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const mutation = useMutation({
    mutationFn: () => login({ username, password }),
    onSuccess: (tok) => {
      setSession(tok);
      onSuccess?.(tok);
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password || mutation.isPending) return;
    mutation.mutate();
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="작업자 로그인"
      data-testid="login-modal"
    >
      <form
        onSubmit={submit}
        className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl"
      >
        <h2 className="text-hmi-lg font-black">작업자 로그인</h2>
        <p className="mt-1 text-base text-gray-600">
          재확인 입력에는 로그인이 필요합니다.
        </p>

        <label className="mt-5 block text-hmi font-bold text-gray-700">
          아이디
          <input
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="mt-2 w-full rounded-xl border-2 border-gray-300 px-4 py-3 text-hmi"
            data-testid="login-username"
          />
        </label>

        <label className="mt-4 block text-hmi font-bold text-gray-700">
          비밀번호
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-2 w-full rounded-xl border-2 border-gray-300 px-4 py-3 text-hmi"
            data-testid="login-password"
          />
        </label>

        {mutation.isError && (
          <p className="mt-3 font-semibold text-ng-fg" role="alert">
            로그인 실패:{" "}
            {(mutation.error as ApiError)?.message ?? "알 수 없는 오류"}
          </p>
        )}

        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={closeLoginPrompt}
            className="rounded-xl border-2 border-gray-300 px-6 py-3 text-hmi font-bold text-gray-700 active:scale-95"
            data-testid="login-cancel"
          >
            취소
          </button>
          <button
            type="submit"
            disabled={!username || !password || mutation.isPending}
            className="rounded-xl bg-blue-600 px-8 py-3 text-hmi font-bold text-white disabled:opacity-40 active:scale-95"
            data-testid="login-submit"
          >
            {mutation.isPending ? "로그인 중…" : "로그인"}
          </button>
        </div>
      </form>
    </div>
  );
}
