/** 테스트용 InspectionResult 팩토리 — shared-types 재사용. */
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict, DefectCode } from "@aivis/shared-types";

let seq = 1;

export function makeResult(
  over: Partial<InspectionResult> = {},
): InspectionResult {
  const id = over.id ?? seq++;
  const ng = over.final_verdict === Verdict.NG;
  return {
    id,
    lot: "LOT-001",
    work_order: null,
    item_code: "HP12",
    cam_id: "CAM-1",
    inspected_at: "2026-06-05T10:00:00+09:00",
    shift: "A",
    operator: "kim",
    ref_length_mm: 250,
    meas_length_mm: ng ? 245 : 250.1,
    deviation_mm: ng ? -5 : 0.1,
    length_verdict: ng ? Verdict.NG : Verdict.OK,
    oil_score: 0.1,
    discolor_score: 0.1,
    scratch_score: 0.1,
    final_verdict: Verdict.OK,
    defect_codes: [],
    confidence: 0.99,
    raw_image_path: null,
    result_image_path: null,
    proc_time_ms: 120,
    review_flag: false,
    manual_verdict: null,
    mes_synced: true,
    ...over,
  };
}

export function makeNg(over: Partial<InspectionResult> = {}): InspectionResult {
  return makeResult({
    final_verdict: Verdict.NG,
    defect_codes: [DefectCode.LEN],
    review_flag: true,
    ...over,
  });
}
