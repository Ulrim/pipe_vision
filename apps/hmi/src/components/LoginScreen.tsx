/**
 * 로그인 게이트 (CLAUDE.md §5 M10, §7.4).
 *
 * **파이 자체 화면에서는 이 화면이 거의 뜨지 않는다.** 마운트 즉시
 * POST /auth/kiosk 로 자동 로그인을 시도하고, 서버가 루프백(=파이 화면)일 때만
 * 작업자 권한 토큰을 준다. 현장 작업자가 교대마다(토큰 8시간 만료) 장갑 낀
 * 손으로 터치 키보드에 아이디·비밀번호를 입력하던 문제를 없애기 위함이다.
 * 사무실 PC 에서 파이 IP 로 접속하면 서버가 거절하므로 아래 로그인 폼이 뜬다.
 *
 * 레이아웃: 파이 7인치(800x480)에 **스크롤 없이** 들어가야 한다(실측 후 조정).
 * 이전에는 카드가 화면보다 커서 로그인 버튼이 잘렸다.
 */
import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { login, kioskLogin, ApiError } from "@/api/client";
import { useAuthStore } from "@/store/authStore";

export function LoginScreen() {
  const setSession = useAuthStore((s) => s.setSession);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  // 자동 로그인 시도 중에는 폼 대신 대기 표시(깜빡임 방지).
  const [tryingKiosk, setTryingKiosk] = useState(true);
  const tried = useRef(false);

  useEffect(() => {
    if (tried.current) return;
    tried.current = true;
    let alive = true;
    kioskLogin()
      .then((tok) => {
        // 200 이어도 토큰이 없으면 로그인으로 치지 않는다(프록시/게이트웨이가
        // 빈 본문을 200 으로 돌려주는 경우 대비 — 세션이 깨진 채 통과하면
        // 이후 모든 요청이 401 로 실패한다).
        if (alive && tok?.access_token) setSession(tok);
      })
      .catch(() => {
        // 파이 화면이 아니거나 기능이 꺼져 있다 → 평소대로 로그인 폼.
      })
      .finally(() => {
        if (alive) setTryingKiosk(false);
      });
    return () => {
      alive = false;
    };
  }, [setSession]);

  const mutation = useMutation({
    mutationFn: () => login({ username, password }),
    onSuccess: (tok) => setSession(tok),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password || mutation.isPending) return;
    mutation.mutate();
  };

  if (tryingKiosk) {
    return (
      <div
        className="flex h-full items-center justify-center bg-gray-100"
        data-testid="login-checking"
      >
        <span className="text-hmi-num font-bold text-gray-500">
          검사 화면을 준비하는 중…
        </span>
      </div>
    );
  }

  return (
    <div
      className="flex h-full items-center justify-center bg-gray-100 p-3"
      data-testid="login-screen"
    >
      <form
        onSubmit={submit}
        className="w-full max-w-lg rounded-2xl bg-white p-5 shadow-xl"
        aria-label="작업자 로그인"
      >
        <h1 className="text-hmi-num font-black text-gray-900">AIVIS 검사</h1>
        <p className="mt-1 text-hmi-cap text-gray-500">
          사용하려면 로그인하세요.
        </p>

        {/* 아이디·비밀번호를 가로로 나란히 — 480px 세로 공간을 아낀다. */}
        <div className="mt-3 grid grid-cols-2 gap-3">
          <label className="block text-hmi-cap font-bold text-gray-700">
            아이디
            <input
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="mt-1 w-full rounded-xl border-2 border-gray-300 px-3 py-2 text-hmi-body"
              data-testid="login-username"
              autoFocus
            />
          </label>
          <label className="block text-hmi-cap font-bold text-gray-700">
            비밀번호
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-xl border-2 border-gray-300 px-3 py-2 text-hmi-body"
              data-testid="login-password"
            />
          </label>
        </div>

        {mutation.isError && (
          <p className="mt-2 text-hmi-cap font-bold text-ng-fg" role="alert">
            로그인 실패:{" "}
            {(mutation.error as ApiError)?.message ?? "알 수 없는 오류"}
          </p>
        )}

        <button
          type="submit"
          disabled={!username || !password || mutation.isPending}
          className="mt-4 min-h-touch w-full rounded-xl bg-blue-600 text-hmi-num font-black text-white disabled:opacity-40 active:scale-95"
          data-testid="login-submit"
        >
          {mutation.isPending ? "로그인 중…" : "로그인"}
        </button>
      </form>
    </div>
  );
}
