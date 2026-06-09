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

/**
 * WS URL 에 JWT 를 query 로 부착(`?token=<JWT>`).
 * 백엔드 /ws/live?token= 에서 검증(유효해야 accept, 아니면 1008 close).
 * - 토큰은 URL 인코딩.
 * - 이미 query 가 있으면 `&` 로 연결.
 * - 토큰이 비어 있으면 원본 URL 그대로 반환(부착 안 함).
 */
export function withWsToken(baseUrl: string, token: string | null): string {
  if (!token) return baseUrl;
  const sep = baseUrl.includes("?") ? "&" : "?";
  return `${baseUrl}${sep}token=${encodeURIComponent(token)}`;
}
