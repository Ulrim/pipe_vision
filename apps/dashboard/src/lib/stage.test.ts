import { describe, expect, it } from "vitest";
import type { StationLatest } from "@/api/endpoints";
import { reasonLines, stageLabel, STAGE_OPTIONS } from "./stage";

function row(over: Partial<StationLatest> = {}): StationLatest {
  return {
    id: 1, inspected_at: "2026-10-09T00:00:00Z", lot: "L", item_code: "HP12",
    inspection_stage: "CUT_LENGTH", final_verdict: "NG", defect_codes: ["LEN"],
    meas_length_mm: 429.82, deviation_mm: -0.18, length_verdict: "NG",
    oil_score: 0.62, discolor_score: 0.1, scratch_score: 0.31, review_flag: false,
    has_result_image: true, has_raw_image: true, frame_total: 1, frame_ng: 1,
    limits: {
      ref_length_mm: 430, tol_plus_mm: 0.1, tol_minus_mm: 0.1, oil_threshold: 0.4,
      discolor_threshold: 0.3, scratch_threshold: 0.2, expected_count: 20,
    },
    ...over,
  };
}

describe("reasonLines — NG 사유를 수치로(HMI 와 같은 말)", () => {
  it("양품이면 없다", () => {
    expect(reasonLines(row({ final_verdict: "OK", defect_codes: [] }))).toEqual([]);
  });
  it("길이: 부호·편차·허용치", () => {
    expect(reasonLines(row())).toEqual(["길이 −0.18mm (허용 −0.10)"]);
  });
  it("길이: 끝단 검출 실패는 따로 말한다", () => {
    expect(reasonLines(row({ deviation_mm: null }))).toEqual(["길이: 끝단을 찾지 못함"]);
  });
  it("표면: 점수 > 기준", () => {
    expect(reasonLines(row({ defect_codes: ["OIL", "SCR"] }))).toEqual([
      "유분기 0.62 > 기준 0.40",
      "스크래치 0.31 > 기준 0.20",
    ]);
  });
  it("개수: 하트비트 검출/기준, 없으면 품목 기준 개수", () => {
    const r = row({ defect_codes: ["COUNT"] });
    expect(reasonLines(r, { detected: 22, expected: 20 })).toEqual(["개수 22 / 기준 20 (+2)"]);
    expect(reasonLines(r, { detected: null, expected: null })).toEqual(["개수 불일치"]);
  });
  it("기준정보가 없어도 수치는 적는다", () => {
    expect(reasonLines(row({ limits: null, defect_codes: ["LEN", "OIL"] }))).toEqual([
      "길이 −0.18mm",
      "유분기 0.62",
    ]);
  });
  it("코드 없는 NG 는 판정 불가로", () => {
    expect(reasonLines(row({ defect_codes: [] }))).toEqual(["판정 불가 — 사유 코드 없음"]);
  });
});

describe("stageLabel", () => {
  it("세 모드 한글, 모르는 값은 원문, 없으면 -", () => {
    expect(STAGE_OPTIONS.map((o) => o.label)).toEqual(["길이 검사", "표면 검사", "개수 확인"]);
    expect(stageLabel("X")).toBe("X");
    expect(stageLabel(null)).toBe("-");
  });
});
