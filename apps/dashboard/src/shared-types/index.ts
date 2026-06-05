/**
 * VENDORED COPY of packages/shared-types/ts/src/index.ts.
 *
 * The dashboard Docker build context is `./apps/dashboard` (see docker-compose.yml),
 * so it cannot reach `../../packages/shared-types`. This file is a verbatim copy of the
 * canonical shared-types and MUST stay 1:1 with it. The canonical source remains the
 * single source of truth (CLAUDE.md §7). Do not edit types here independently — update
 * the canonical file (via orchestrator approval) and re-sync this copy.
 *
 * Imported app-wide as `@aivis/shared-types`.
 */

/* =========================================================================
 * Enums (CLAUDE.md §7.2 defect codes, §7.1 role, verdict)
 * String enums so values match the Python str-Enum values exactly.
 * ========================================================================= */

/** 불량유형 코드표 (§7.2). MULTI = 2종 이상 복합. */
export enum DefectCode {
  LEN = "LEN",
  OIL = "OIL",
  DIS = "DIS",
  SCR = "SCR",
  MULTI = "MULTI",
}

/** 판정 결과 — 길이/표면/종합 공통. */
export enum Verdict {
  OK = "OK",
  NG = "NG",
}

/** 사용자 권한 3역할 (§5 M14, §7.1). */
export enum Role {
  OPERATOR = "operator",
  QUALITY = "quality",
  ADMIN = "admin",
}

/** 시스템 로그 분류 (§7.1 sys_log.category). */
export enum LogCategory {
  INSPECT = "inspect",
  DB = "db",
  MES = "mes",
  ERROR = "error",
  USER = "user",
}

/** 촬영 구도 (부록 A.1). */
export enum CameraView {
  END = "END",
  SIDE = "SIDE",
}

/* =========================================================================
 * Master / inspection (mirror of inspection.py)
 * ========================================================================= */

/** 품목/기준정보 (item_master 테이블, §7.1). */
export interface ItemMaster {
  item_code: string;
  item_name: string;
  ref_length_mm: number;
  tol_plus_mm: number;
  tol_minus_mm: number;
  px_to_mm_scale: number;
  oil_threshold?: number | null;
  discolor_threshold?: number | null;
  scratch_threshold?: number | null;
  capture_recipe?: Record<string, unknown> | null;
  version: number;
  updated_by?: string | null;
  updated_at?: string | null; // ISO datetime
}

/** 기준정보 등록 입력(version/updated_* 제외). */
export interface ItemMasterCreate {
  item_code: string;
  item_name: string;
  ref_length_mm: number;
  tol_plus_mm: number;
  tol_minus_mm: number;
  px_to_mm_scale: number;
  oil_threshold?: number | null;
  discolor_threshold?: number | null;
  scratch_threshold?: number | null;
  capture_recipe?: Record<string, unknown> | null;
}

/** 기준정보 수정 입력(부분 갱신). 변경 시 version 자동 증가. */
export interface ItemMasterUpdate {
  item_name?: string | null;
  ref_length_mm?: number | null;
  tol_plus_mm?: number | null;
  tol_minus_mm?: number | null;
  px_to_mm_scale?: number | null;
  oil_threshold?: number | null;
  discolor_threshold?: number | null;
  scratch_threshold?: number | null;
  capture_recipe?: Record<string, unknown> | null;
}

/** 검사 결과 (inspection 테이블, 제품 1개 = 1행, §7.1). */
export interface InspectionResult {
  id?: number | null; // 적재 시 미지정, 조회 응답에 포함

  // 식별/메타
  lot: string;
  work_order?: string | null;
  item_code: string;
  cam_id: string;
  inspected_at: string; // ISO datetime
  shift?: string | null;
  operator?: string | null;

  // 길이
  ref_length_mm?: number | null;
  meas_length_mm?: number | null;
  deviation_mm?: number | null;
  length_verdict?: Verdict | null;

  // 표면 (0~1 신뢰도)
  oil_score?: number | null;
  discolor_score?: number | null;
  scratch_score?: number | null;

  // 종합
  final_verdict: Verdict;
  defect_codes: DefectCode[];
  confidence?: number | null;

  raw_image_path?: string | null;
  result_image_path?: string | null;
  proc_time_ms?: number | null;

  // 운영/재확인
  review_flag: boolean;
  manual_verdict?: Verdict | null;
  mes_synced: boolean;
}

/** 재확인 입력 (PATCH /inspection/{id}/review, M10). */
export interface ReviewUpdate {
  manual_verdict: Verdict;
  review_flag?: boolean | null;
  operator?: string | null;
}

/** 검사 이미지 경로 응답 (GET /inspection/{id}/images, M8). */
export interface InspectionImages {
  id: number;
  raw_image_path?: string | null;
  result_image_path?: string | null;
}

/* =========================================================================
 * Vision pipeline intermediate/aggregate outputs (mirror of vision.py)
 * vision-ai produces these; backend stores the InspectionResult.
 * ========================================================================= */

/** 길이 측정 결과 (M3). */
export interface LengthResult {
  ref_length_mm: number;
  meas_length_mm?: number | null;
  deviation_mm?: number | null;
  length_verdict: Verdict;
  edge_detected: boolean;
  proc_time_ms: number;
}

/** 표면 결함 판정 결과 (M4). */
export interface SurfaceResult {
  oil_score?: number | null;
  discolor_score?: number | null;
  scratch_score?: number | null;
  surface_verdict: Verdict;
  defect_codes: DefectCode[];
  proc_time_ms: number;
}

/** 종합 판정 결과 (M5) — InspectionResult 적재 직전 비전 최종 산출물. */
export interface VerdictResult {
  final_verdict: Verdict;
  defect_codes: DefectCode[];
  confidence?: number | null;
  review_flag: boolean;
  length: LengthResult;
  surface: SurfaceResult;
  proc_time_ms: number;
}

/* =========================================================================
 * KPI (mirror of kpi.py)
 * ========================================================================= */

/** 비자동 KPI 입력 (kpi_manual 테이블, §1.1). */
export interface KpiManual {
  period: string; // "YYYY-MM-DD" (월 1일)
  claim_count?: number | null;
  workload_index?: number | null;
  lead_time_days?: number | null;
  note?: string | null;
}

/** KPI 요약 응답 (GET /kpi/summary, §1.1). */
export interface KpiSummary {
  period: string; // 예 "2026-06"

  total_inspected: number;
  defect_count: number;
  process_defect_ppm: number;

  auto_inspected: number;
  auto_inspection_rate_pct: number;

  misjudge_count: number;
  miss_count: number;
  inspection_defect_rate_pct: number;

  stored_count: number;
  mes_synced_count: number;
  storage_mes_rate_pct: number;

  avg_proc_time_ms?: number | null;

  claim_count?: number | null;
  workload_index?: number | null;
  lead_time_days?: number | null;
}

/* =========================================================================
 * Auth / logs (mirror of auth.py)
 * ========================================================================= */

/** 사용자 등록 입력 (POST /auth/users, M14). */
export interface UserCreate {
  username: string;
  password: string;
  role: Role;
  active: boolean;
}

/** 사용자 공개 정보(비밀번호 제외). */
export interface UserPublic {
  username: string;
  role: Role;
  active: boolean;
}

/** 로그인 입력 (POST /auth/login). */
export interface LoginRequest {
  username: string;
  password: string;
}

/** JWT 토큰 응답. */
export interface TokenResponse {
  access_token: string;
  token_type: string;
  role: Role;
  username: string;
}

/** 시스템 로그 (sys_log 테이블, GET /logs, M15). */
export interface SysLog {
  id?: number | null;
  ts?: string | null; // ISO datetime
  level?: string | null;
  category: LogCategory;
  message?: string | null;
  payload?: Record<string, unknown> | null;
}
