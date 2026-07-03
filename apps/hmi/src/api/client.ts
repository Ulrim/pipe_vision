/**
 * REST 클라이언트 (CLAUDE.md §7.4). HMI 가 쓰는 엔드포인트만 노출.
 * 타입은 packages/shared-types(@aivis/shared-types) 재사용 — 신규 정의 금지.
 */
import type {
  InspectionResult,
  LoginRequest,
  ReviewUpdate,
  TokenResponse,
} from "@aivis/shared-types";
import { API_BASE } from "@/lib/config";
import { getAuthToken } from "@/store/authStore";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface RequestOptions {
  /** true 면 현재 토큰을 Authorization: Bearer 로 첨부(쓰기/보호 엔드포인트). */
  auth?: boolean;
}

async function request<T>(
  path: string,
  init?: RequestInit,
  opts: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  if (opts.auth) {
    const token = getAuthToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
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

/**
 * POST /auth/login — JSON 본문 로그인. 토큰/역할 응답.
 * (인증 헤더 불필요.)
 */
export function login(body: LoginRequest): Promise<TokenResponse> {
  return request<TokenResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
  });
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
 * operator+ 권한 필요 → Authorization: Bearer 첨부(auth:true).
 * 응답은 갱신된 InspectionResult.
 */
export function submitReview(
  id: number,
  body: ReviewUpdate,
): Promise<InspectionResult> {
  return request<InspectionResult>(
    `/inspection/${id}/review`,
    {
      method: "PATCH",
      body: JSON.stringify(body),
    },
    { auth: true },
  );
}
