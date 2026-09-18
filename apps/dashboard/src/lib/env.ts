/**
 * API base URL 결정.
 *
 * 우선순위:
 * 1) VITE_API_BASE 가 **명시**되면 그대로 사용(빈 문자열 "" = same-origin 강제
 *    — nginx 등 역프록시 뒤에 API 가 같은 출처로 물려 있는 배포).
 * 2) 미지정이면 접속한 브라우저 호스트로 판단:
 *    - 포트가 80/443/없음(프록시 배포) → same-origin("").
 *    - 그 외(독립형 :5174, dev :5173 등 SPA 전용 포트) → 같은 호스트의
 *      API 포트(:8000)를 가리킨다. 독립형 런처는 대시보드(:5174)와
 *      API(:8000)를 분리 서빙하므로, same-origin 기본은 POST 가 정적
 *      서버로 가서 "Unsupported method ('POST')" 로 전부 실패한다
 *      (실사용 결함 — 재빌드 없이 파이 IP 로 접속해도 동작해야 한다).
 */

/** 독립형 API 포트(런처 API_PORT 기본과 동일). 다르면 VITE_API_BASE 명시. */
const DEFAULT_API_PORT = 8000;

export function resolveApiBase(
  raw: string | undefined = import.meta.env?.VITE_API_BASE as string | undefined,
  loc: { protocol: string; hostname: string; port: string } | undefined =
    typeof window !== "undefined" ? window.location : undefined,
): string {
  if (raw !== undefined) return raw.replace(/\/$/, "");
  if (loc?.hostname) {
    if (loc.port === "" || loc.port === "80" || loc.port === "443") {
      return ""; // 프록시 배포(80/443) = same-origin.
    }
    return `${loc.protocol}//${loc.hostname}:${DEFAULT_API_PORT}`;
  }
  return ""; // 비브라우저(테스트) 폴백 = same-origin.
}

export const API_BASE: string = resolveApiBase();
