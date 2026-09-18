/**
 * KPI 게이지 계산 로직 (CLAUDE.md §1.1 목표 대비 현재값).
 * 산출식 자체는 백엔드(GET /kpi/summary)가 수행하며, 여기서는 목표 대비
 * 달성도(게이지 0~1)와 통과 여부를 계산한다.
 *
 * **목표값은 여기서 정하지 않는다.** 예전에는 이 파일이 600ppm/30% 를 상수로
 * 들고 있어서, 리포트(PDF)와 화면이 서로 다른 목표로 합격을 찍을 수 있었다.
 * 이제 GET /kpi/targets 가 단일 출처이고 이 모듈은 그것을 받아 쓴다.
 */
import type { KpiSummary } from "@aivis/shared-types";
import type { KpiTarget } from "@/api/endpoints";

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
 * KpiSummary + 서버 목표치 -> 게이지 목록 (§1.1/§1.2 인수 합격 핵심).
 *
 * 실적을 산출할 수 없는 지표(출하유출불량률: 출하수량 수기 입력이 없을 때)는
 * 게이지를 만들지 않는다. 값 없음을 0 으로 그리면 "불량 0 = 합격"으로 읽힌다.
 */
export function buildKpiGauges(
  s: KpiSummary | undefined,
  targets: KpiTarget[] | undefined,
): KpiGaugeSpec[] {
  // 둘 중 하나라도 아직 안 왔으면 게이지를 그리지 않는다(로딩 중).
  if (!s || !targets) return [];
  const actual: Record<string, number | null | undefined> = {
    process_defect_ppm: s.process_defect_ppm,
    shipment_leak_ppm: s.shipment_leak_ppm,
    inspection_defect_rate_pct: s.inspection_defect_rate_pct,
    auto_inspection_rate_pct: s.auto_inspection_rate_pct,
    storage_mes_rate_pct: s.storage_mes_rate_pct,
  };
  const unit: Record<string, string> = {
    process_defect_ppm: "ppm",
    shipment_leak_ppm: "ppm",
    inspection_defect_rate_pct: "%",
    auto_inspection_rate_pct: "%",
    storage_mes_rate_pct: "%",
  };
  const out: KpiGaugeSpec[] = [];
  for (const t of targets) {
    const v = actual[t.key];
    if (v === null || v === undefined) continue; // 처리속도 p95 등은 별도 게이지
    out.push(
      spec(t.key, t.label.replace(/\s*\(.*\)$/, ""), unit[t.key] ?? "", v, t.target_value, t.direction),
    );
  }
  return out;
}

/** 처리속도 보조 KPI (목표는 서버 targets 의 p95_proc_time_ms). null 안전. */
export function procTimeSpec(
  s: KpiSummary | undefined,
  targets: KpiTarget[] | undefined,
): KpiGaugeSpec | null {
  if (!s || s.avg_proc_time_ms === null || s.avg_proc_time_ms === undefined) {
    return null;
  }
  const t = targets?.find((x) => x.key === "p95_proc_time_ms");
  return spec(
    "avg_proc_time_ms",
    "평균 처리속도",
    "ms",
    s.avg_proc_time_ms,
    t?.target_value ?? 300,
    "lower",
  );
}
