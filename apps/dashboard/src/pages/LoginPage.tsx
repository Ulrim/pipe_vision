import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { login } from "@/api/endpoints";
import { useAuthStore } from "@/store/auth";
import { ApiError } from "@/api/client";

/** §7 7 — 로그인(POST /auth/login) 후 토큰 저장. */
export function LoginPage(): JSX.Element {
  const navigate = useNavigate();
  const setAuth = useAuthStore((s) => s.setAuth);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const res = await login({ username, password });
      setAuth({ token: res.access_token, username: res.username, role: res.role });
      navigate("/kpi");
    } catch (e2) {
      setErr(e2 instanceof ApiError ? e2.message : (e2 as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto mt-12 max-w-sm">
      <form className="card space-y-3 p-6" onSubmit={submit} data-testid="login-form">
        <h1 className="text-lg font-bold">로그인</h1>
        <div>
          <span className="label">아이디</span>
          <input className="input w-full" value={username} autoComplete="username"
            onChange={(e) => setUsername(e.target.value)} data-testid="login-username" />
        </div>
        <div>
          <span className="label">비밀번호</span>
          <input type="password" className="input w-full" value={password}
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)} data-testid="login-password" />
        </div>
        {err && <div className="rounded bg-ng-bg p-2 text-sm text-ng-fg" data-testid="login-error">{err}</div>}
        <button type="submit" className="btn-primary w-full" disabled={busy} data-testid="login-submit">
          {busy ? "로그인 중…" : "로그인"}
        </button>
      </form>
    </div>
  );
}
