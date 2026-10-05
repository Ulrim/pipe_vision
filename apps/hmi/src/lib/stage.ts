/**
 * 검사 모드(InspectionStage) 표시와 NG 사유 문장 (M10).
 *
 * 현장 요구(2026-10-05): "한 번에 다 하려니 NG 가 어떻게 나는지 모르겠다."
 * 모드마다 **한 질문**만 하고, NG 면 **무엇이 얼마나** 벗어났는지 숫자로 적는다.
 * 뱃지(LEN/OIL…)만으로는 "유분기 0.62 인데 기준이 0.40" 을 알 수 없다.
 */
import type { InspectionResult, ItemMaster } from "@aivis/shared-types";
import { InspectionStage } from "@aivis/shared-types";

export const STAGE_LABEL: Record<string, string> = {
  [InspectionStage.CUT_LENGTH]: "길이 검사",
  [InspectionStage.POST_WASH_SURFACE]: "표면 검사",
  [InspectionStage.CRATE_COUNT]: "개수 확인",
};

/** 모드 표기. 모르는 값이면 원문을 그대로(숨기지 않는다). */
export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return "모드 미지정";
  return STAGE_LABEL[stage] ?? stage;
}

export function isCountStage(stage: string | null | undefined): boolean {
  return stage === InspectionStage.CRATE_COUNT;
}

function f2(v: number): string {
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}`;
}

/** 표면 점수 하나를 "유분기 0.62 > 0.40" 로. 임계가 없으면 점수만. */
function scoreLine(
  name: string,
  score: number | null | undefined,
  th: number | null | undefined,
): string {
  if (score === null || score === undefined) return `${name} —`;
  if (th === null || th === undefined) return `${name} ${score.toFixed(2)}`;
  return `${name} ${score.toFixed(2)} > 기준 ${th.toFixed(2)}`;
}

export interface CountInfo {
  detected: number;
  expected: number;
}

/**
 * NG 사유 문장. 양품이면 null. 모드에 속한 항목만 설명한다.
 * item 이 없으면(기준정보 미조회) 수치는 적되 기준값은 생략한다.
 */
export function ngReason(
  r: InspectionResult,
  item: ItemMaster | null | undefined,
  count?: CountInfo | null,
): string | null {
  if (r.final_verdict !== "NG") return null;
  const codes = new Set<string>(r.defect_codes ?? []);
  const parts: string[] = [];

  if (codes.has("LEN")) {
    const dev = r.deviation_mm;
    if (dev === null || dev === undefined) {
      parts.push("길이 — 끝단을 찾지 못함");
    } else {
      const tol = dev >= 0 ? item?.tol_plus_mm : item?.tol_minus_mm;
      const lim =
        tol === null || tol === undefined
          ? ""
          : ` (허용 ${dev >= 0 ? "+" : "−"}${tol.toFixed(2)})`;
      parts.push(`길이 ${f2(dev)}mm${lim}`);
    }
  }
  if (codes.has("OIL")) parts.push(scoreLine("유분기", r.oil_score, item?.oil_threshold));
  if (codes.has("DIS")) parts.push(scoreLine("변색", r.discolor_score, item?.discolor_threshold));
  if (codes.has("SCR")) parts.push(scoreLine("스크래치", r.scratch_score, item?.scratch_threshold));
  if (codes.has("COUNT")) {
    if (count) {
      const d = count.detected - count.expected;
      parts.push(`개수 ${count.detected} / 기준 ${count.expected} (${d > 0 ? "+" : ""}${d})`);
    } else {
      parts.push("개수 불일치");
    }
  }
  if (parts.length === 0) {
    // 코드 없는 NG = 단계 오류(취득/전처리 실패 등) — 재확인 대상.
    return "판정 불가 — 재확인 필요";
  }
  return parts.join(" · ");
}

const CODE_KO: Record<string, string> = {
  LEN: "길이", OIL: "유분기", DIS: "변색", SCR: "스크래치", COUNT: "개수",
};

/** 배치의 NG 튜브들에서 불량 코드를 세어 "길이 2 · 스크래치 1" 로. MULTI 는 유형이 아니라 뺀다. */
export function tallyCodes(rows: InspectionResult[]): string | null {
  const tally = new Map<string, number>();
  for (const r of rows) {
    if (r.final_verdict !== "NG") continue;
    for (const c of r.defect_codes ?? []) {
      if (c === "MULTI") continue;
      tally.set(c, (tally.get(c) ?? 0) + 1);
    }
  }
  if (tally.size === 0) return null;
  return [...tally.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([c, n]) => `${CODE_KO[c] ?? c} ${n}`)
    .join(" · ");
}
