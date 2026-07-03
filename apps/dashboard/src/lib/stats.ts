/**
 * 불량유형 분포/월별 추이 집계 (M11). 조회된 InspectionResult 배열에서
 * 클라이언트 측 요약을 만든다(대시보드 차트용).
 */
import type { InspectionResult } from "@aivis/shared-types";
import { DefectCode, Verdict } from "@aivis/shared-types";

export interface DefectDist {
  code: DefectCode;
  count: number;
}

/** defect_codes 분포 집계. 한 검사에 복수 코드면 각각 카운트. */
export function defectDistribution(rows: InspectionResult[]): DefectDist[] {
  const tally = new Map<DefectCode, number>();
  for (const r of rows) {
    for (const c of r.defect_codes ?? []) {
      tally.set(c, (tally.get(c) ?? 0) + 1);
    }
  }
  return (Object.values(DefectCode) as DefectCode[])
    .map((code) => ({ code, count: tally.get(code) ?? 0 }))
    .filter((d) => d.count > 0);
}

export interface MonthlyTrendPoint {
  month: string; // YYYY-MM
  total: number;
  ng: number;
  defectRatePct: number;
}

/** inspected_at 기준 월별 불량률(%) 추이. */
export function monthlyDefectTrend(rows: InspectionResult[]): MonthlyTrendPoint[] {
  const byMonth = new Map<string, { total: number; ng: number }>();
  for (const r of rows) {
    const month = (r.inspected_at ?? "").slice(0, 7); // YYYY-MM
    if (!month) continue;
    const bucket = byMonth.get(month) ?? { total: 0, ng: 0 };
    bucket.total += 1;
    if (r.final_verdict === Verdict.NG) bucket.ng += 1;
    byMonth.set(month, bucket);
  }
  return [...byMonth.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, { total, ng }]) => ({
      month,
      total,
      ng,
      defectRatePct: total === 0 ? 0 : (ng / total) * 100,
    }));
}
