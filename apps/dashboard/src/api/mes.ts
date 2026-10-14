/**
 * MES(품질·생산실적) 클라이언트 — services/mes (기본 :8100, docs/MES.md §6).
 *
 * AIVIS API 와 **다른 서버**다. 그래서 base URL 만 MES_BASE 로 바꾸고, 토큰 첨부는
 * api/client.ts 의 것을 그대로 쓴다(로그인은 하나 — MES 가 AIVIS 가 발급한 JWT 를 같은
 * 서명키로 검증한다).
 *
 * 단, MES 의 401 로는 **로그아웃하지 않는다**. MES 가 토큰을 거부하는 가장 흔한 이유는
 * 서명키(JWT_SECRET) 설정이 AIVIS 와 다른 것인데, 그때 로그인을 비우면 MES 화면을
 * 열 때마다 대시보드 전체에서 쫓겨난다. 토큰 만료는 같은 화면의 AIVIS 호출(품목·오더
 * 조회)이 잡아 로그인 화면으로 보낸다.
 *
 * 타입은 services/mes/mes_app/schemas.py 와 1:1(MES 는 @aivis/shared-types 범위 밖의
 * 별도 서비스라 여기서 정의한다). 날짜(date)는 "YYYY-MM-DD", 시각(datetime)은 ISO 문자열.
 * pydantic 의 `Optional[X]`(기본값 없음)는 항상 오지만 null 일 수 있어 `X | null`,
 * 입력 모델의 `Optional[X] = None` 은 생략 가능 `?: X | null` 로 옮긴다.
 */
import { MES_BASE } from "@/lib/env";
import { ApiError, authHeaders, toQuery } from "./client";

/** MES 를 못 찾았을 때(꺼짐·미배포·프록시 경로 없음) 화면에 그대로 띄우는 문구. */
export const MES_UNREACHABLE = "MES 서버에 연결할 수 없습니다";

/** MES 가 토큰을 거부했을 때 — 대개 MES 의 JWT_SECRET 이 검사기(AIVIS)와 다르다. */
export const MES_AUTH_REJECTED =
  "MES 가 로그인을 받지 않습니다 — MES 서버의 JWT_SECRET 이 검사기(AIVIS)와 같은지 확인하세요";

/* ---------------- 상태 값 ---------------- */
export type SoStatus = "OPEN" | "CLOSED";
export type WoStatus = "PLANNED" | "RUNNING" | "DONE" | "CANCELLED";
export type ClaimStatus = "OPEN" | "CLOSED";

/* ---------------- 수주 ---------------- */
export interface SalesOrderIn {
  /** 비우면 SO-YYYYMMDD-NNN 자동. */
  so_no?: string | null;
  customer: string;
  item_code: string;
  qty: number;
  order_date: string;
  due_date?: string | null;
  note?: string | null;
}

export interface SalesOrderPatch {
  status?: SoStatus | null;
  qty?: number | null;
  due_date?: string | null;
  note?: string | null;
}

export interface SalesOrderOut {
  id: number;
  so_no: string;
  customer: string;
  item_code: string;
  qty: number;
  order_date: string;
  due_date: string | null;
  /** 서버 응답 모델은 str — 알려진 값은 SoStatus. */
  status: string;
  note: string | null;
  created_by: string | null;
  created_at: string | null;
  shipped_qty: number;
  work_order_count: number;
}

/* ---------------- 작업지시 ---------------- */
export interface WorkOrderIn {
  /** 비우면 WO-YYYYMMDD-NNN 자동. */
  wo_no?: string | null;
  so_id?: number | null;
  /** 수주를 고르면 비워도 된다. */
  item_code?: string | null;
  /** 비우면 L-YYYYMMDD-NN 자동. 작업지시마다 하나. */
  lot?: string | null;
  plan_qty: number;
  plan_date?: string | null;
  note?: string | null;
}

/** operator 는 status 만, 수량·계획일·비고는 quality+ (서버가 403 으로 막는다). */
export interface WorkOrderPatch {
  status?: WoStatus | null;
  plan_qty?: number | null;
  plan_date?: string | null;
  note?: string | null;
}

export interface StageStatOut {
  stage: string;
  label: string;
  inspected: number;
  ai_ng: number;
  final_ng: number;
  reviewed: number;
  good: number;
  by_code: Record<string, number>;
}

export interface ProductionOut {
  inspected_rows: number;
  stages: StageStatOut[];
  defect_total: number;
  by_code: Record<string, number>;
  good_qty: number;
  /** crate | stage:<STAGE> | none */
  good_source: string;
  crate_count: number;
  crate_tubes: number;
  crate_mismatch: number;
  first_at: string | null;
  last_at: string | null;
}

export interface WorkOrderOut {
  id: number;
  wo_no: string;
  so_id: number | null;
  so_no: string | null;
  customer: string | null;
  item_code: string;
  lot: string;
  plan_qty: number;
  plan_date: string | null;
  /** 서버 응답 모델은 str — 알려진 값은 WoStatus. */
  status: string;
  started_at: string | null;
  finished_at: string | null;
  note: string | null;
  created_by: string | null;
  created_at: string | null;
  // 요약(목록 화면) — 상세는 production.
  good_qty: number;
  defect_total: number;
  inspected_rows: number;
  progress_pct: number | null;
  last_at: string | null;
}

export interface WorkOrderDetail {
  work_order: WorkOrderOut;
  production: ProductionOut;
}

export interface UnassignedLot {
  lot: string;
  work_order: string | null;
  item_code: string | null;
  production: ProductionOut;
}

/* ---------------- 출하·클레임 ---------------- */
export interface ShipmentIn {
  ship_no?: string | null;
  so_id?: number | null;
  lot?: string | null;
  /** 수주를 고르면 비워도 된다. */
  item_code?: string | null;
  /** 수주를 고르면 비워도 된다. */
  customer?: string | null;
  qty: number;
  ship_date: string;
  note?: string | null;
}

export interface ShipmentOut {
  id: number;
  ship_no: string;
  so_id: number | null;
  so_no: string | null;
  lot: string | null;
  item_code: string;
  customer: string;
  qty: number;
  ship_date: string;
  note: string | null;
  created_by: string | null;
  created_at: string | null;
  /** 수주일 → 출하일(일). 수주 없이 출하하면 null. */
  lead_time_days: number | null;
  /** 납기 내 여부. 수주 납기가 없으면 null. */
  on_time: boolean | null;
}

export interface ClaimIn {
  claim_no?: string | null;
  customer: string;
  lot?: string | null;
  item_code?: string | null;
  defect_qty?: number;
  /** LEN|OIL|DIS|SCR|COUNT|ETC */
  defect_type?: string | null;
  occurred_date: string;
  description?: string | null;
}

export interface ClaimPatch {
  status?: ClaimStatus | null;
  defect_qty?: number | null;
  defect_type?: string | null;
  description?: string | null;
}

export interface ClaimOut {
  id: number;
  claim_no: string;
  customer: string;
  lot: string | null;
  item_code: string | null;
  defect_qty: number;
  defect_type: string | null;
  occurred_date: string;
  description: string | null;
  /** 서버 응답 모델은 str — 알려진 값은 ClaimStatus. */
  status: string;
  closed_at: string | null;
  created_by: string | null;
  created_at: string | null;
}

export interface MonthlyKpi {
  period: string;
  shipment_count: number;
  shipped_qty: number;
  lead_time_days: number | null;
  lead_time_samples: number;
  on_time_rate_pct: number | null;
  on_time_samples: number;
  claim_count: number;
  leak_defect_qty: number;
  leak_ppm: number | null;
}

/* ---------------- 수신(연계 현황) ---------------- */
// QualityIn(AIVIS → MES 수신 본문)은 검사기가 보내는 것이라 대시보드에는 두지 않는다.

export interface QualityOut {
  id: number;
  idem_key: string;
  inspection_id: number | null;
  lot: string;
  work_order: string | null;
  item_code: string | null;
  cam_id: string | null;
  inspection_stage: string | null;
  /** 다발 사진 안의 튜브 순서(0-base, AIVIS 와 같음). 화면은 #(tube_index+1). */
  tube_index: number | null;
  inspected_at: string;
  final_verdict: string;
  defect_codes: string[] | null;
  meas_length_mm: number | null;
  deviation_mm: number | null;
  manual_verdict: string | null;
  count_detected: number | null;
  count_expected: number | null;
  /** table | rest */
  source: string;
  received_at: string | null;
  updated_at: string | null;
}

export interface IntakeRunOut {
  ts: string;
  source: string;
  received: number;
  updated: number;
  error: string | null;
}

export interface PollOut {
  scanned: number;
  stored: number;
  updated: number;
  duplicate: number;
  marked: number;
  error: string | null;
}

export interface IntakeStatus {
  table_enabled: boolean;
  poller_running: boolean;
  poll_interval_s: number;
  last_poll_at: string | null;
  last_result: PollOut | null;
  /** AIVIS 인터페이스 테이블에서 아직 안 가져간 건수(테이블 연계가 꺼져 있으면 null). */
  interface_pending: number | null;
  interface_error: string | null;
  rest_token_required: boolean;
  records_total: number;
  records_today: number;
  last_received_at: string | null;
  runs: IntakeRunOut[];
}

/* ---------------- 요청 ---------------- */

/** FastAPI 의 detail — HTTPException 은 문자열, 입력 검증(422)은 [{loc, msg}] 배열. */
function detailText(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d as { msg?: unknown })?.msg)
      .filter((m): m is string => typeof m === "string");
    if (msgs.length) return msgs.join("; ");
  }
  return fallback;
}

function isJson(res: Response): boolean {
  return (res.headers.get("content-type") ?? "").toLowerCase().includes("json");
}

/**
 * MES JSON 요청. requestJson(api/client.ts)과 같지만 base 가 MES_BASE 다.
 *
 * MES 가 없을 때의 모양이 배포마다 달라 전부 같은 문구로 모은다 — 화면이 원인별로
 * 다른 영문 오류를 띄우면 현장에서 "고장" 으로만 읽힌다.
 * - 독립형(:8100 꺼짐): fetch 자체가 실패(TypeError) → 상태 0.
 * - 80/443 프록시에 /mes-api 경로가 없음: SPA 의 index.html 이 200 으로 온다
 *   (JSON 이 아님) → 그대로 파싱하면 "Unexpected token <" 로 화면이 깨진다.
 * - 프록시는 있는데 MES 가 죽음: 502/504 HTML.
 */
export async function requestMes<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${MES_BASE}${path}`, {
      ...init,
      headers: authHeaders({
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      }),
    });
  } catch {
    throw new ApiError(0, MES_UNREACHABLE);
  }
  if (!res.ok) {
    if (res.status === 401) {
      throw new ApiError(401, MES_AUTH_REJECTED);
    }
    if (!isJson(res)) {
      throw new ApiError(res.status, `${MES_UNREACHABLE} (HTTP ${res.status})`);
    }
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* 본문이 깨진 JSON — statusText 로 대신 */
    }
    throw new ApiError(res.status, detailText(body, res.statusText || `HTTP ${res.status}`));
  }
  if (res.status === 204) return undefined as T;
  if (!isJson(res)) throw new ApiError(res.status, MES_UNREACHABLE);
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError(res.status, MES_UNREACHABLE);
  }
}

function post<T>(path: string, body: unknown): Promise<T> {
  return requestMes<T>(path, { method: "POST", body: JSON.stringify(body) });
}

function patch<T>(path: string, body: unknown): Promise<T> {
  return requestMes<T>(path, { method: "PATCH", body: JSON.stringify(body) });
}

/* ---------------- 수주 ---------------- */
/** GET /orders?status= — 수주 목록(수주일 최신순). */
export function fetchSalesOrders(status?: SoStatus): Promise<SalesOrderOut[]> {
  return requestMes<SalesOrderOut[]>(`/orders${toQuery({ status })}`);
}

/** POST /orders — 수주 등록(quality+). */
export function createSalesOrder(body: SalesOrderIn): Promise<SalesOrderOut> {
  return post<SalesOrderOut>("/orders", body);
}

/** PATCH /orders/{id} — 마감 등(quality+). */
export function patchSalesOrder(id: number, body: SalesOrderPatch): Promise<SalesOrderOut> {
  return patch<SalesOrderOut>(`/orders/${id}`, body);
}

/* ---------------- 작업지시·생산실적 ---------------- */
/** GET /work-orders?status=PLANNED,RUNNING — 쉼표로 여러 상태. 비우면 전체(최신순). */
export function fetchWorkOrders(status?: string): Promise<WorkOrderOut[]> {
  return requestMes<WorkOrderOut[]>(`/work-orders${toQuery({ status })}`);
}

/** POST /work-orders — 작업지시 등록(quality+). */
export function createWorkOrder(body: WorkOrderIn): Promise<WorkOrderOut> {
  return post<WorkOrderOut>("/work-orders", body);
}

/** GET /work-orders/{id} — 작업지시 + 생산실적 상세. */
export function fetchWorkOrder(id: number): Promise<WorkOrderDetail> {
  return requestMes<WorkOrderDetail>(`/work-orders/${id}`);
}

/** PATCH /work-orders/{id} — 상태는 operator 도, 수량·계획일은 quality+. */
export function patchWorkOrder(id: number, body: WorkOrderPatch): Promise<WorkOrderOut> {
  return patch<WorkOrderOut>(`/work-orders/${id}`, body);
}

/** GET /production/unassigned — 어느 작업지시에도 안 이어진 LOT 의 실적. */
export function fetchUnassignedLots(): Promise<UnassignedLot[]> {
  return requestMes<UnassignedLot[]>("/production/unassigned");
}

/* ---------------- 품질이력(수신분) ---------------- */
export interface QualityQuery {
  lot?: string;
  work_order?: string;
  limit?: number;
}

/** GET /quality — MES 가 받은 AIVIS 검사결과(최근 것부터). */
export function fetchQuality(q: QualityQuery = {}): Promise<QualityOut[]> {
  return requestMes<QualityOut[]>(`/quality${toQuery({ ...q })}`);
}

/* ---------------- 출하·클레임·월 KPI ---------------- */
/** GET /shipments?period=YYYY-MM */
export function fetchShipments(period?: string): Promise<ShipmentOut[]> {
  return requestMes<ShipmentOut[]>(`/shipments${toQuery({ period })}`);
}

/** POST /shipments — 출하 등록(quality+). */
export function createShipment(body: ShipmentIn): Promise<ShipmentOut> {
  return post<ShipmentOut>("/shipments", body);
}

/** GET /claims?status= */
export function fetchClaims(status?: ClaimStatus): Promise<ClaimOut[]> {
  return requestMes<ClaimOut[]>(`/claims${toQuery({ status })}`);
}

/** POST /claims — 클레임 등록(quality+). */
export function createClaim(body: ClaimIn): Promise<ClaimOut> {
  return post<ClaimOut>("/claims", body);
}

/** PATCH /claims/{id} — 종결 등(quality+). */
export function patchClaim(id: number, body: ClaimPatch): Promise<ClaimOut> {
  return patch<ClaimOut>(`/claims/${id}`, body);
}

/** GET /kpi/monthly?period=YYYY-MM — 비우면 이번 달(KST). */
export function fetchMonthlyKpi(period?: string): Promise<MonthlyKpi> {
  return requestMes<MonthlyKpi>(`/kpi/monthly${toQuery({ period })}`);
}

/* ---------------- 연계 현황 ---------------- */
/** GET /intake/status */
export function fetchIntakeStatus(): Promise<IntakeStatus> {
  return requestMes<IntakeStatus>("/intake/status");
}

/** POST /intake/poll — 지금 가져오기(quality+). 테이블 연계가 꺼져 있으면 409. */
export function pollIntake(): Promise<PollOut> {
  return requestMes<PollOut>("/intake/poll", { method: "POST" });
}
