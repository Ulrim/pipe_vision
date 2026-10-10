import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderApp } from "@/test/utils";

const fetchInspectionStats = vi.fn();
const fetchInspections = vi.fn();
vi.mock("@/api/endpoints", () => ({
  fetchInspectionStats: (...a: unknown[]) => fetchInspectionStats(...a),
  fetchInspections: (...a: unknown[]) => fetchInspections(...a),
}));

// 차트는 jsdom 에 캔버스가 없어 그릴 수 없다 — 이 테스트는 데이터 경로만 본다.
vi.mock("@/components/TrendChart", () => ({ TrendChart: () => null }));
vi.mock("@/components/DefectPie", () => ({ DefectPie: () => null }));

import { StatisticsPage } from "./StatisticsPage";
import { distFromServer, trendFromServer } from "@/lib/stats";

beforeEach(() => {
  fetchInspectionStats.mockReset();
  fetchInspections.mockReset();
});

describe("StatisticsPage — 서버 집계(2026-10-10 점검: 5,000건 요청이 상한 2,000을 넘어 실패)", () => {
  it("행을 내려받지 않고 서버 집계를 쓴다", async () => {
    fetchInspectionStats.mockResolvedValue({
      total: 123456, ng: 789,
      by_code: [{ code: "LEN", count: 700 }, { code: "OIL", count: 89 }],
      monthly: [{ month: "2026-09", total: 123456, ng: 789, defect_rate_pct: 0.639 }],
    });
    renderApp(<StatisticsPage />);
    expect(await screen.findByTestId("stats-total")).toHaveTextContent("123,456");
    expect(fetchInspections).not.toHaveBeenCalled();
    await waitFor(() => expect(fetchInspectionStats).toHaveBeenCalled());
  });

  it("실패를 숨기지 않는다", async () => {
    fetchInspectionStats.mockRejectedValue(new Error("boom"));
    renderApp(<StatisticsPage />);
    expect(await screen.findByTestId("stats-error")).toHaveTextContent("boom");
  });

  it("서버 응답 → 차트 입력 변환", () => {
    const res = {
      total: 3, ng: 2,
      by_code: [{ code: "OIL", count: 1 }, { code: "LEN", count: 2 }],
      monthly: [{ month: "2026-10", total: 3, ng: 2, defect_rate_pct: 66.667 }],
    };
    expect(distFromServer(res).map((d) => d.code)).toEqual(["LEN", "OIL"]);
    expect(trendFromServer(res)[0]).toEqual({ month: "2026-10", total: 3, ng: 2, defectRatePct: 66.667 });
    expect(distFromServer(undefined)).toEqual([]);
  });
});
