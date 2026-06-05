/**
 * 런타임 설정 — API/WS 베이스 URL은 env 로 주입(VITE_API_BASE, VITE_WS_URL).
 * 기본값은 현장 단일호스트 localhost:8000 (CLAUDE.md §4 런타임 토폴로지).
 */

const DEFAULT_API_BASE = "http://localhost:8000";

export const API_BASE: string =
  import.meta.env.VITE_API_BASE?.replace(/\/$/, "") ?? DEFAULT_API_BASE;

/**
 * WS URL 우선순위: VITE_WS_URL → API_BASE 에서 http(s)→ws(s) 변환 + /ws/live.
 */
export function resolveWsUrl(): string {
  const explicit = import.meta.env.VITE_WS_URL;
  if (explicit) return explicit;
  const base = API_BASE.replace(/^http/, "ws");
  return `${base}/ws/live`;
}

export const WS_URL: string = resolveWsUrl();
