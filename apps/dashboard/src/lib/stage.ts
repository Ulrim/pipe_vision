/**
 * 검사 모드(InspectionStage) 한글 표기 — 이력 필터·모니터 스테이션 목록용.
 * 라벨은 HMI(apps/hmi/src/lib/stage.ts)와 같은 말을 쓴다: 현장과 사무실이
 * 같은 단어로 통화해야 한다.
 */
import { InspectionStage } from "@aivis/shared-types";
import type { StationLatest } from "@/api/endpoints";

export const STAGE_LABEL: Record<string, string> = {
  [InspectionStage.CUT_LENGTH]: "길이 검사",
  [InspectionStage.POST_WASH_SURFACE]: "표면 검사",
  [InspectionStage.CRATE_COUNT]: "개수 확인",
};

/** 필터 select 의 선택지 순서(공정 순). */
export const STAGE_OPTIONS: Array<{ value: string; label: string }> = [
  InspectionStage.CUT_LENGTH,
  InspectionStage.POST_WASH_SURFACE,
  InspectionStage.CRATE_COUNT,
].map((v) => ({ value: v, label: STAGE_LABEL[v] }));

/** 모르는 값은 원문 그대로(숨기지 않는다). 없으면 "-". */
export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return "-";
  return STAGE_LABEL[stage] ?? stage;
}

export function isCountStage(stage: string | null | undefined): boolean {
  return stage === InspectionStage.CRATE_COUNT;
}

function signed(v: number, digits = 2): string {
  const t = v.toFixed(digits);
  return v > 0 ? `+${t}` : v < 0 ? `−${t.slice(1)}` : t;
}

function scoreLine(name: string, score: number | null, th: number | null): string {
  if (score === null) return `${name} —`;
  return th === null ? `${name} ${score.toFixed(2)}` : `${name} ${score.toFixed(2)} > 기준 ${th.toFixed(2)}`;
}

/**
 * NG 사유를 **수치로**(HMI 와 같은 말). 양품이면 빈 배열.
 * "LEN" 배지만으로는 0.02mm 넘었는지 2mm 넘었는지 모른다 — 사무실에서도 같다.
 * count: 개수 모드에서 하트비트의 검출/기준(행에는 개수가 없다).
 */
export function reasonLines(
  r: StationLatest,
  count?: { detected: number | null; expected: number | null } | null,
): string[] {
  if (r.final_verdict !== "NG") return [];
  const codes = new Set(r.defect_codes ?? []);
  const lim = r.limits;
  const out: string[] = [];
  if (codes.has("LEN")) {
    if (r.deviation_mm === null) {
      out.push("길이: 끝단을 찾지 못함");
    } else {
      const over = r.deviation_mm > 0;
      const tol = over ? lim?.tol_plus_mm : lim?.tol_minus_mm;
      const tolTxt = tol === null || tol === undefined ? "" : ` (허용 ${over ? "+" : "−"}${tol.toFixed(2)})`;
      out.push(`길이 ${signed(r.deviation_mm)}mm${tolTxt}`);
    }
  }
  if (codes.has("OIL")) out.push(scoreLine("유분기", r.oil_score, lim?.oil_threshold ?? null));
  if (codes.has("DIS")) out.push(scoreLine("변색", r.discolor_score, lim?.discolor_threshold ?? null));
  if (codes.has("SCR")) out.push(scoreLine("스크래치", r.scratch_score, lim?.scratch_threshold ?? null));
  if (codes.has("COUNT")) {
    const d = count?.detected ?? null;
    const e = count?.expected ?? lim?.expected_count ?? null;
    out.push(
      d !== null && e !== null
        ? `개수 ${d} / 기준 ${e} (${d - e > 0 ? "+" : "−"}${Math.abs(d - e)})`
        : "개수 불일치",
    );
  }
  if (out.length === 0) out.push("판정 불가 — 사유 코드 없음");
  return out;
}
