/**
 * KPI 게이지 계산 로직 (CLAUDE.md §1.1 목표 대비 현재값).
 * 산출식 자체는 백엔드(GET /kpi/summary)가 수행하며, 여기서는 목표 대비
 * 달성도(게이지 0~1)와 통과 여부를 계산한다. 임의 변형 금지 — §1.1 목표값 그대로.
 */
import type { KpiSummary } from "@aivis/shared-types";

export type KpiStatus = "pass" | "warn" | "fail";

/** 방향: lower=값이 낮을수록 좋음(불량률), higher=값이 높을수록 좋음(자동검사율). */
export type KpiDirection = "lower" | "higher";

export interface KpiGaugeSpec {
  key: string;
  label: string;
  unit: string;
  value: number;
  /** §1.1 목표값. */
  target: number;
  direction: KpiDirection;
  status: KpiStatus;
  /** 게이지 채움 비율 0~1 (목표 대비 달성도). */
  ratio: number;
}

/**
 * 게이지 달성 비율(0~1).
 * - higher: value/target (목표 도달 시 1).
 * - lower : target/value (목표 이하면 1, 초과하면 < 1).
 * value=0 또는 target=0 등 경계는 안전 처리.
 */
export function gaugeRatio(
  value: number,
  target: number,
  direction: KpiDirection,
): number {
  if (!Number.isFinite(value) || !Number.isFinite(target)) return 0;
  let r: number;
  if (direction === "higher") {
    r = target === 0 ? 1 : value / target;
  } else {
    // lower-is-better: 값이 0이면 완벽, 목표 이하면 1.
    if (value <= 0) r = 1;
    else r = target / value;
  }
  return Math.max(0, Math.min(1, r));
}

/** 목표 충족 여부 → pass/warn/fail. warn 은 목표의 ±10% 경계. */
export function kpiStatus(
  value: number,
  target: number,
  direction: KpiDirection,
): KpiStatus {
  const meets =
    direction === "lower" ? value <= target : value >= target;
  if (meets) return "pass";
  // 경계: 목표를 10% 이내로 벗어난 경우 warn, 그 이상은 fail.
  const margin = Math.abs(target) * 0.1;
  const off =
    direction === "lower" ? value - target : target - value;
  return off <= margin ? "warn" : "fail";
}

function spec(
  key: string,
  label: string,
  unit: string,
  value: number,
  target: number,
  direction: KpiDirection,
): KpiGaugeSpec {
  return {
    key,
    label,
    unit,
    value,
    target,
    direction,
    status: kpiStatus(value, target, direction),
    ratio: gaugeRatio(value, target, direction),
  };
}

/**
 * KpiSummary -> 게이지 4종 (§1.1/§1.2 인수 합격 핵심).
 * 목표값:
 * - 공정불량률 ≤ 600 ppm (lower)
 * - 검사불량률 ≤ 30 % (lower)
 * - 자동검사율 = 100 % (higher)
 * - 저장&MES 연계율 = 100 % (higher)
 */
export function buildKpiGauges(s: KpiSummary): KpiGaugeSpec[] {
  return [
    spec("process_defect_ppm", "공정불량률", "ppm", s.process_defect_ppm, 600, "lower"),
    spec("inspection_defect_rate_pct", "검사불량률", "%", s.inspection_defect_rate_pct, 30, "lower"),
    spec("auto_inspection_rate_pct", "자동검사율", "%", s.auto_inspection_rate_pct, 100, "higher"),
    spec("storage_mes_rate_pct", "저장·MES 연계율", "%", s.storage_mes_rate_pct, 100, "higher"),
  ];
}

/** 처리속도 보조 KPI (목표 ≤ 300ms/ea, §1.2). null 안전. */
export function procTimeSpec(s: KpiSummary): KpiGaugeSpec | null {
  if (s.avg_proc_time_ms === null || s.avg_proc_time_ms === undefined) return null;
  return spec("avg_proc_time_ms", "평균 처리속도", "ms", s.avg_proc_time_ms, 300, "lower");
}
