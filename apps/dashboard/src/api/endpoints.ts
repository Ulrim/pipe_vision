/**
 * AIVIS 대시보드 엔드포인트 (CLAUDE.md §7.4, docs/API.md).
 * 모든 응답 타입은 @aivis/shared-types. 신규 타입 정의 금지(쿼리 입력만 로컬 정의).
 */
import type {
  InspectionResult,
  InspectionImages,
  ItemMaster,
  ItemMasterUpdate,
  KpiSummary,
  KpiManual,
  LoginRequest,
  TokenResponse,
} from "@aivis/shared-types";
import { requestJson, requestBlob, toQuery } from "./client";

/* ---------------- 인증 (§7 7) ---------------- */
export function login(body: LoginRequest): Promise<TokenResponse> {
  return requestJson<TokenResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/* ---------------- 검사이력 (M11, M8) ---------------- */
export interface InspectionQuery {
  lot?: string;
  item?: string;
  from?: string;
  to?: string;
  verdict?: string;
  limit?: number;
  offset?: number;
}

/** GET /inspection — 필터 조회(서버 페이지네이션). */
export function fetchInspections(
  q: InspectionQuery = {},
): Promise<InspectionResult[]> {
  return requestJson<InspectionResult[]>(`/inspection${toQuery({ ...q })}`);
}

/** GET /inspection/{id} — 단건. */
export function fetchInspection(id: number): Promise<InspectionResult> {
  return requestJson<InspectionResult>(`/inspection/${id}`);
}

/** GET /inspection/{id}/images — 원본/결과 이미지 경로. */
export function fetchInspectionImages(id: number): Promise<InspectionImages> {
  return requestJson<InspectionImages>(`/inspection/${id}/images`);
}

/* ---------------- KPI (M12, §1.1) ---------------- */
/** GET /kpi/summary?period=YYYY-MM. */
export function fetchKpiSummary(period: string): Promise<KpiSummary> {
  return requestJson<KpiSummary>(`/kpi/summary${toQuery({ period })}`);
}

/** POST /kpi/manual — 작업공수/리드타임/Claim upsert(quality+). */
export function upsertKpiManual(body: KpiManual): Promise<KpiManual> {
  return requestJson<KpiManual>("/kpi/manual", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export type ReportFormat = "pdf" | "xlsx";

/** GET /kpi/report?period=&fmt= — 월간 리포트 파일(Blob). */
export function fetchKpiReport(
  period: string,
  fmt: ReportFormat,
): Promise<{ blob: Blob; filename: string | null }> {
  return requestBlob(`/kpi/report${toQuery({ period, fmt })}`);
}

/* ---------------- 기준정보 (M13) ---------------- */
/** GET /master/items — 목록. */
export function fetchItems(): Promise<ItemMaster[]> {
  return requestJson<ItemMaster[]>("/master/items");
}

/** GET /master/items/{code} — 단건. */
export function fetchItem(code: string): Promise<ItemMaster> {
  return requestJson<ItemMaster>(`/master/items/${encodeURIComponent(code)}`);
}

/** PUT /master/items/{code} — 부분 갱신(version +1). quality+ 권한 토큰 필요. */
export function updateItem(
  code: string,
  body: ItemMasterUpdate,
): Promise<ItemMaster> {
  return requestJson<ItemMaster>(`/master/items/${encodeURIComponent(code)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}
