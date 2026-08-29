/**
 * AIVIS 대시보드 엔드포인트 (CLAUDE.md §7.4, docs/API.md).
 * 모든 응답 타입은 @aivis/shared-types. 신규 타입 정의 금지(쿼리 입력만 로컬 정의).
 */
import type {
  InspectionResult,
  InspectionImages,
  ItemMaster,
  ItemMasterUpdate,
  CalibrationRequest,
  KpiSummary,
  KpiManual,
  LoginRequest,
  TokenResponse,
} from "@aivis/shared-types";
import { requestJson, requestBlob, requestImageBlob, toQuery } from "./client";

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

/** 검사 이미지 종류(raw=원본, result=판정 오버레이). */
export type InspectionImageKind = "raw" | "result";

/**
 * GET /inspection/{id}/images/{kind} — 이미지 바이트(image/jpeg, JWT 필요).
 * <img src> 는 Authorization 헤더를 못 싣으므로 fetch→Blob→objectURL 경로 사용.
 */
export function fetchInspectionImageBlob(
  id: number,
  kind: InspectionImageKind,
): Promise<Blob> {
  return requestImageBlob(`/inspection/${id}/images/${kind}`);
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

/* ---------------- 월간 리포트 미리보기 (M12) ---------------- */

/** §1.1/§1.2 목표 대비 달성 1행. achieved: null = 판정보류(실적 없음). */
export interface ReportTarget {
  key: string;
  label: string;
  label_en: string;
  target: string;
  actual: string;
  achieved: boolean | null;
}

export interface ReportDefect {
  code: string;
  label: string;
  count: number;
}

export interface ReportDaily {
  date: string;
  inspected: number;
  defects: number;
  /** 일자별 공정불량률(ppm) — 추세 차트용. */
  ppm: number;
}

/**
 * GET /kpi/report/preview 응답. PDF/XLSX 와 **동일한 서버 집계**를 그대로
 * 받아 화면에 그린다(화면과 파일의 숫자가 어긋나지 않게 단일 산출원 사용).
 */
export interface ReportPreview {
  period: string;
  summary: KpiSummary;
  proc_time: { p50: number | null; p95: number | null; p99: number | null };
  targets: ReportTarget[];
  defects: ReportDefect[];
  daily: ReportDaily[];
}

/** GET /kpi/report/preview?period= — 리포트 미리보기 데이터. */
export function fetchKpiReportPreview(period: string): Promise<ReportPreview> {
  return requestJson<ReportPreview>(`/kpi/report/preview${toQuery({ period })}`);
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

/**
 * POST /master/items/{code}/calibrate — 웹 자기보정(quality+ 권한).
 * px_to_mm_scale := 기존 scale × (actual_mm / measured_mm), version +1.
 * 갱신된 ItemMaster 반환.
 */
export function calibrateItem(
  code: string,
  body: CalibrationRequest,
): Promise<ItemMaster> {
  return requestJson<ItemMaster>(
    `/master/items/${encodeURIComponent(code)}/calibrate`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}

/* ---------------- 현재 검사 오더 (발주 기반 전환) ---------------- */

/**
 * 현재 검사 오더. 발주마다 품목(모양/외경/개수)·절단 길이가 달라지므로,
 * 여기서 설정하면 라즈베리파이 워커가 폴링(15초 주기)해 재시작 없이
 * 품목/LOT/작업지시를 전환한다. 미설정이면 워커는 env 기본 품목 유지.
 */
export interface ActiveOrder {
  item_code: string;
  lot: string | null;
  work_order: string | null;
  updated_by: string | null;
  updated_at: string | null;
}

export interface ActiveOrderIn {
  item_code: string;
  lot: string | null;
  work_order: string | null;
}

/** GET /master/active — 미설정이면 null. */
export function fetchActiveOrder(): Promise<ActiveOrder | null> {
  return requestJson<ActiveOrder | null>("/master/active");
}

/** PUT /master/active — 오더 설정(quality+). 404=품목 없음. */
export function putActiveOrder(body: ActiveOrderIn): Promise<ActiveOrder> {
  return requestJson<ActiveOrder>("/master/active", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** DELETE /master/active — 오더 해제(quality+). */
export function clearActiveOrder(): Promise<void> {
  return requestJson<void>("/master/active", { method: "DELETE" });
}

/* ---------------- 시스템 모니터링 (현장 라즈베리파이 원격 감시) ---------------- */

/** 라즈베리파이 하드웨어 자원. 측정 불가 항목은 null(0 과 구분해야 함). */
export interface SystemResources {
  cpu_temp_c: number | null;
  cpu_percent: number | null;
  load_1m: number | null;
  mem_total_mb: number | null;
  mem_used_mb: number | null;
  mem_percent: number | null;
  disk_total_gb: number | null;
  disk_used_gb: number | null;
  disk_percent: number | null;
  /** true = 전원 부족/과열로 CPU 스로틀 감지. */
  throttled: boolean | null;
}

/** 워커는 하트비트 지연을 stale 로 구분(정지와 다름). */
export interface SystemServices {
  db: "up" | "down";
  worker: "up" | "stale" | "down";
  worker_last_seen_s: number | null;
}

export interface SystemInspectionWindow {
  total: number;
  ng: number;
  ng_rate_pct: number;
}

export interface SystemInspection {
  last_hour: SystemInspectionWindow;
  today: SystemInspectionWindow;
  avg_proc_time_ms: number | null;
  p95_proc_time_ms: number | null;
  last_inspected_at: string | null;
  mes_pending: number;
}

export interface SystemError {
  ts: string;
  message: string;
}

/** GET /system/status 응답(operator+). */
export interface SystemStatus {
  ts: string;
  system: SystemResources;
  services: SystemServices;
  inspection: SystemInspection;
  active_order: {
    item_code: string;
    lot: string | null;
    work_order: string | null;
  } | null;
  recent_errors: SystemError[];
}

/** GET /system/status — 현장 장비 상태 스냅샷(5초 주기 폴링용). */
export function fetchSystemStatus(): Promise<SystemStatus> {
  return requestJson<SystemStatus>("/system/status");
}

/* ---------------- 프로그램 업데이트 (현장 사용자용, admin 전용) ---------------- */

/**
 * 현재 설치본 정보. 현장 담당자에게 커밋 해시는 의미가 없으므로 화면은
 * date(설치 날짜)·subject(한 줄 설명)를 앞세우고 commit/branch 는 보조로만 쓴다.
 * available=false = 자동 업데이트가 불가한 설치 형태(git 체크아웃 아님).
 */
export interface UpdateVersion {
  available: boolean;
  commit: string | null;
  date: string | null;
  subject: string | null;
  branch: string | null;
  /**
   * 업데이트 후 프로그램이 **스스로 다시 시작할 수 있는 설치인가**
   * (부팅 자동시작 유닛 등록 여부). false 면 파일만 갱신되고 재시작을
   * 건너뛰므로, 화면에 "완료"가 떠도 실행 중인 프로그램은 이전 버전이다.
   * 비개발자는 이를 알아챌 수 없으므로 화면이 반드시 미리 안내해야 한다.
   */
  restart_supported: boolean;
}

/** 업데이트 진행 상태(화면이 3초 주기로 폴링해 그대로 표시). */
export interface UpdateProgress {
  state: "idle" | "running" | "success" | "failed";
  started_at: number | null;
  finished_at: number | null;
  exit_code: number | null;
  log_tail: string[];
}

export interface UpdateInfo {
  current: UpdateVersion;
  progress: UpdateProgress;
}

/** 원격 최신본 확인 결과. behind=0 이면 최신, reachable=false 면 인터넷/저장소 접근 실패. */
export interface UpdateRemote {
  reachable: boolean;
  behind: number | null;
  latest_date: string | null;
  latest_subject: string | null;
  error: string | null;
}

export interface UpdateStartResult {
  started: boolean;
  message: string;
}

/**
 * GET /system/update — 현재 버전 + 진행 상태(admin).
 * 네트워크를 쓰지 않아 가볍다. 업데이트 중에는 이 요청이 API 재시작으로
 * 잠시 실패할 수 있으며, 화면은 그 구간을 정상(재시작 중)으로 처리한다.
 */
export function fetchUpdateInfo(): Promise<UpdateInfo> {
  return requestJson<UpdateInfo>("/system/update");
}

/** POST /system/update/check — 새 버전 확인만(원격 조회, 설치본 미변경, admin). */
export function checkUpdate(): Promise<UpdateRemote> {
  return requestJson<UpdateRemote>("/system/update/check", { method: "POST" });
}

/**
 * POST /system/update/start — 업데이트 시작(admin).
 * 분리 프로세스로 수 분간 진행되고 완료 후 서비스(API 포함)가 자동 재시작된다.
 */
export function startUpdate(): Promise<UpdateStartResult> {
  return requestJson<UpdateStartResult>("/system/update/start", {
    method: "POST",
  });
}
