/**
 * REST 클라이언트 (CLAUDE.md §7.4). HMI 가 쓰는 엔드포인트만 노출.
 * 타입은 packages/shared-types(@aivis/shared-types) 재사용 — 신규 정의 금지.
 */
import type {
  InspectionResult,
  ItemMaster,
  ItemSpecUpdate,
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

/**
 * 파이 자체 화면 자동 로그인(작업자 권한). POST /auth/kiosk.
 *
 * 서버가 **루프백 요청일 때만** 토큰을 준다. 즉 파이에 붙은 7인치 화면에서는
 * 통과하고, 사무실 PC 에서 파이 IP 로 접속하면 403 → 일반 로그인 화면으로
 * 넘어간다. 현장 작업자가 교대마다 장갑 낀 손으로 터치 키보드에 아이디·
 * 비밀번호를 치던 문제를 없애기 위한 경로다.
 */
export function kioskLogin(): Promise<TokenResponse> {
  return request<TokenResponse>("/auth/kiosk", { method: "POST" });
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
 * GET /master/items/{code} — 품목 기준정보(공차 등).
 *
 * 화면에 **공차 밴드**를 그리려면 허용 범위를 알아야 하는데, 검사결과에는
 * 기준길이·편차만 있고 공차는 기준정보에만 있다. 품목이 바뀔 때까지 변하지
 * 않는 값이라 한 번 받아 캐시한다.
 */
export function fetchItem(itemCode: string): Promise<ItemMaster> {
  return request<ItemMaster>(
    `/master/items/${encodeURIComponent(itemCode)}`,
    undefined,
    { auth: true },
  );
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

/**
 * PUT /master/items/{code}/spec — 오더 교체용 치수 사양 변경.
 *
 * 전체 갱신과 달리 기준길이·공차·개수만 바뀐다. px→mm 보정계수나 표면
 * 임계값은 서버가 받지 않는다 — 라인에서 급히 고치다 엉뚱한 값을 흔드는
 * 사고를 구조적으로 막기 위함이다.
 */
/** 현재 검사 오더(모드 포함). 미설정이면 null. */
export interface ActiveOrderView {
  item_code: string;
  lot?: string | null;
  work_order?: string | null;
  inspection_stage?: string | null;
}

export function fetchActiveOrder(): Promise<ActiveOrderView | null> {
  return request<ActiveOrderView | null>("/master/active", undefined, { auth: true });
}

/**
 * 검사 모드만 바꾼다 — PUT /master/active/stage (작업자 권한).
 * 길이→표면→개수를 한 대로 번갈아 보는 벤치에서 재시작 없이 전환한다.
 */
export function setActiveStage(
  itemCode: string,
  stage: string,
): Promise<ActiveOrderView> {
  return request<ActiveOrderView>(
    "/master/active/stage",
    {
      method: "PUT",
      body: JSON.stringify({ item_code: itemCode, inspection_stage: stage }),
    },
    { auth: true },
  );
}

export function updateItemSpec(
  itemCode: string,
  body: ItemSpecUpdate,
): Promise<ItemMaster> {
  return request<ItemMaster>(
    `/master/items/${encodeURIComponent(itemCode)}/spec`,
    { method: "PUT", body: JSON.stringify(body) },
    { auth: true },
  );
}
