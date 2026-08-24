/**
 * 런타임 설정 — API/WS 베이스 URL은 env 로 주입(VITE_API_BASE, VITE_WS_URL).
 *
 * VITE_API_BASE 미지정(독립형/현장 기본 빌드) 시에는 빌드 시점이 아니라
 * **접속한 브라우저의 호스트 기준**으로 API(:8000)를 찾는다:
 *   - 파이 LCD 키오스크(localhost:5173) → http://localhost:8000
 *   - 사무실 PC(http://<파이IP>:5173)   → http://<파이IP>:8000
 * "localhost:8000" 을 고정하면 PC 브라우저에서 PC 자신을 가리켜 전부
 * 실패한다(실사용 결함) — 재빌드 없이 어느 호스트에서 열어도 동작해야 한다.
 * 클라우드 배포(다른 출처)는 VITE_API_BASE 를 명시해 이 기본값을 덮는다.
 */

/** 독립형 API 포트(런처 API_PORT 기본과 동일). 다르면 VITE_API_BASE 명시. */
const DEFAULT_API_PORT = 8000;

export function defaultApiBase(
  loc: { protocol: string; hostname: string } | undefined =
    typeof window !== "undefined" ? window.location : undefined,
): string {
  if (loc?.hostname) {
    return `${loc.protocol}//${loc.hostname}:${DEFAULT_API_PORT}`;
  }
  return `http://localhost:${DEFAULT_API_PORT}`; // 테스트/비브라우저 폴백.
}

export const API_BASE: string =
  import.meta.env.VITE_API_BASE?.replace(/\/$/, "") ?? defaultApiBase();

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
