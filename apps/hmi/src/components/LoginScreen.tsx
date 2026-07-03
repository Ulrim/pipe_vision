/**
 * 전체화면 로그인 게이트 (CLAUDE.md §5 M10, §7.4 POST /auth/login).
 *
 * 사내 도구 전환: 미인증 시 앱 본문 대신 이 화면만 노출(진입 차단).
 * LoginModal 과 달리 "취소/닫기"가 없다 — 로그인해야만 통과한다.
 * 현장 가독성·터치 최적화(큰 폰트/버튼).
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { login, ApiError } from "@/api/client";
import { useAuthStore } from "@/store/authStore";

export function LoginScreen() {
  const setSession = useAuthStore((s) => s.setSession);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const mutation = useMutation({
    mutationFn: () => login({ username, password }),
    onSuccess: (tok) => {
      // 세션 저장 → App 게이트가 본문(검사 화면)을 렌더하고 WS 가 토큰으로 연결.
      setSession(tok);
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password || mutation.isPending) return;
    mutation.mutate();
  };

  return (
    <div
      className="flex min-h-full items-center justify-center bg-gray-100 p-4"
      data-testid="login-screen"
    >
      <form
        onSubmit={submit}
        className="w-full max-w-md rounded-2xl bg-white p-8 shadow-2xl"
        aria-label="작업자 로그인"
      >
        <h1 className="text-hmi-lg font-black text-gray-900">AIVIS 실시간 검사</h1>
        <p className="mt-2 text-base text-gray-600">
          사용을 위해 로그인하세요.
        </p>

        <label className="mt-6 block text-hmi font-bold text-gray-700">
          아이디
          <input
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="mt-2 w-full rounded-xl border-2 border-gray-300 px-4 py-3 text-hmi"
            data-testid="login-username"
            autoFocus
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

        <button
          type="submit"
          disabled={!username || !password || mutation.isPending}
          className="mt-6 w-full rounded-xl bg-blue-600 px-8 py-4 text-hmi font-bold text-white disabled:opacity-40 active:scale-95"
          data-testid="login-submit"
        >
          {mutation.isPending ? "로그인 중…" : "로그인"}
        </button>
      </form>
    </div>
  );
}
