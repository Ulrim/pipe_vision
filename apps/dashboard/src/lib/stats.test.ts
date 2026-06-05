import { describe, expect, it } from "vitest";
import type { InspectionResult } from "@aivis/shared-types";
import { DefectCode, Verdict } from "@aivis/shared-types";
import { defectDistribution, monthlyDefectTrend } from "./stats";

function mk(p: Partial<InspectionResult>): InspectionResult {
  return {
    lot: "L", item_code: "HP12", cam_id: "C1",
    inspected_at: "2026-06-01T00:00:00Z",
    final_verdict: Verdict.OK, defect_codes: [],
    review_flag: false, mes_synced: true, ...p,
  };
}

describe("defectDistribution", () => {
  it("복수 코드 각각 카운트, 0인 코드 제외", () => {
    const dist = defectDistribution([
      mk({ defect_codes: [DefectCode.LEN, DefectCode.OIL] }),
      mk({ defect_codes: [DefectCode.LEN] }),
      mk({ defect_codes: [] }),
    ]);
    const map = Object.fromEntries(dist.map((d) => [d.code, d.count]));
    expect(map.LEN).toBe(2);
    expect(map.OIL).toBe(1);
    expect(map.SCR).toBeUndefined();
  });
});

describe("monthlyDefectTrend", () => {
  it("월별 불량률(%) 산출 + 정렬", () => {
    const trend = monthlyDefectTrend([
      mk({ inspected_at: "2026-05-10T00:00:00Z", final_verdict: Verdict.NG }),
      mk({ inspected_at: "2026-05-11T00:00:00Z", final_verdict: Verdict.OK }),
      mk({ inspected_at: "2026-06-01T00:00:00Z", final_verdict: Verdict.OK }),
    ]);
    expect(trend.map((t) => t.month)).toEqual(["2026-05", "2026-06"]);
    expect(trend[0].defectRatePct).toBe(50); // 1/2
    expect(trend[1].defectRatePct).toBe(0);
  });
});
