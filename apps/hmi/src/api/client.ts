/**
 * REST 클라이언트 (CLAUDE.md §7.4). HMI 가 쓰는 엔드포인트만 노출.
 * 타입은 packages/shared-types(@aivis/shared-types) 재사용 — 신규 정의 금지.
 */
import type {
  InspectionResult,
  ReviewUpdate,
} from "@aivis/shared-types";
import { API_BASE } from "@/lib/config";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
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
  return (await res.json()) as T;
}

export interface InspectionQuery {
  lot?: string;
  item?: string;
  from?: string;
  to?: string;
  verdict?: string;
  limit?: number;
  offset?: number;
}

/** GET /inspection — 필터 조회(서버 페이지네이션). 초기 적재/이력용. */
export function fetchInspections(
  q: InspectionQuery = {},
): Promise<InspectionResult[]> {
  const params = new URLSearchParams();
  Object.entries(q).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  });
  const qs = params.toString();
  return request<InspectionResult[]>(`/inspection${qs ? `?${qs}` : ""}`);
}

/**
 * PATCH /inspection/{id}/review — NG 재확인 결과 입력(M10).
 * 응답은 갱신된 InspectionResult.
 */
export function submitReview(
  id: number,
  body: ReviewUpdate,
): Promise<InspectionResult> {
  return request<InspectionResult>(`/inspection/${id}/review`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
