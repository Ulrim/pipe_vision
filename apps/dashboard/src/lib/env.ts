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

/** 독립형 작업자 화면(HMI) 포트(런처 HMI_PORT 기본과 동일). */
const DEFAULT_HMI_PORT = 5173;

/**
 * 작업자 화면(HMI) 주소 — 실시간 현황에서 "이 라인 작업자 화면" 으로 넘어갈 때.
 *
 * 1) VITE_HMI_URL 이 있으면 그대로(클라우드 배포: 다른 도메인).
 * 2) 독립형(:5174 등 SPA 포트)이면 같은 호스트의 :5173.
 * 3) 80/443 프록시 배포인데 명시가 없으면 **모른다** → null(링크를 숨긴다).
 *    틀린 링크를 보여주는 것보다 없는 편이 낫다.
 */
export function resolveHmiBase(
  raw: string | undefined = import.meta.env?.VITE_HMI_URL as string | undefined,
  loc: { protocol: string; hostname: string; port: string } | undefined =
    typeof window !== "undefined" ? window.location : undefined,
): string | null {
  if (raw) return raw.replace(/\/$/, "");
  if (!loc?.hostname) return null;
  if (loc.port === "" || loc.port === "80" || loc.port === "443") return null;
  return `${loc.protocol}//${loc.hostname}:${DEFAULT_HMI_PORT}`;
}

/** 특정 스테이션에 고정된 작업자 화면 주소(?cam=). HMI 주소를 모르면 null. */
export function hmiUrlFor(camId: string, base: string | null = resolveHmiBase()): string | null {
  return base ? `${base}/?cam=${encodeURIComponent(camId)}` : null;
}
