/**
 * REST 클라이언트 (CLAUDE.md §7.4). 대시보드가 쓰는 엔드포인트만 노출.
 * 타입은 packages/shared-types(@aivis/shared-types) 재사용 — 신규 정의 금지.
 * 인증 토큰은 auth 스토어에서 읽어 Authorization 헤더로 첨부(§7 7).
 */
import { API_BASE } from "@/lib/env";
import { getToken } from "@/store/auth";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function authHeaders(extra?: HeadersInit): HeadersInit {
  const token = getToken();
  return {
    ...(extra ?? {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

/** JSON 요청. 4xx/5xx 는 ApiError 로 변환(detail 우선). */
export async function requestJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: authHeaders({
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    }),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = (body?.detail as string) ?? detail;
    } catch {
      /* non-json error body */
    }
    throw new ApiError(res.status, detail);
  }
  // 204/빈 본문 허용.
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Blob 요청(리포트 PDF/XLSX 다운로드). 파일명은 Content-Disposition 우선. */
export async function requestBlob(
  path: string,
  init?: RequestInit,
): Promise<{ blob: Blob; filename: string | null }> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: authHeaders(init?.headers),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = (body?.detail as string) ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition");
  const filename = cd ? parseFilename(cd) : null;
  return { blob, filename };
}

/**
 * 이미지 바이트 요청(검사 raw/result, image/jpeg). Authorization 헤더 첨부.
 * 401/403/404 등은 ApiError(status) 로 변환 → 호출측이 placeholder 처리.
 */
export async function requestImageBlob(
  path: string,
  init?: RequestInit,
): Promise<Blob> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: authHeaders(init?.headers),
  });
  if (!res.ok) {
    throw new ApiError(res.status, res.statusText);
  }
  return res.blob();
}

/** Content-Disposition 에서 filename 추출(RFC 5987 filename* 우선). */
export function parseFilename(disposition: string): string | null {
  const star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(disposition);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].replace(/"/g, "").trim());
    } catch {
      /* fall through */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain?.[1]?.trim() ?? null;
}

/** undefined/null/"" 를 제외한 쿼리스트링 빌더. */
export function toQuery(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  });
  const qs = sp.toString();
  return qs ? `?${qs}` : "";
}
