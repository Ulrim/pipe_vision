import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict, DefectCode } from "@aivis/shared-types";
import { renderApp } from "@/test/utils";

// 엔드포인트 모킹(네트워크 차단).
const fetchInspections = vi.fn();
const fetchInspectionImages = vi.fn();
vi.mock("@/api/endpoints", () => ({
  fetchInspections: (...a: unknown[]) => fetchInspections(...a),
  fetchInspectionImages: (...a: unknown[]) => fetchInspectionImages(...a),
}));

import { InspectionsPage } from "./InspectionsPage";

const row: InspectionResult = {
  id: 7, lot: "LOT-A", item_code: "HP12", cam_id: "C1",
  inspected_at: "2026-06-01T03:00:00Z",
  meas_length_mm: 248.5, deviation_mm: -1.5,
  final_verdict: Verdict.NG, defect_codes: [DefectCode.LEN],
  proc_time_ms: 210, review_flag: true, mes_synced: true,
};

beforeEach(() => {
  fetchInspections.mockReset();
  fetchInspectionImages.mockReset();
  fetchInspectionImages.mockResolvedValue({ id: 7, raw_image_path: "raw/x.jpg", result_image_path: "result/x.jpg" });
});

describe("InspectionsPage", () => {
  it("초기 조회 결과를 테이블에 렌더", async () => {
    fetchInspections.mockResolvedValue([row]);
    renderApp(<InspectionsPage />);
    expect(await screen.findByText("LOT-A")).toBeInTheDocument();
    expect(screen.getByText("HP12")).toBeInTheDocument();
    expect(screen.getAllByTestId("verdict-badge")[0]).toHaveTextContent("NG");
  });

  it("필터 적용 시 쿼리에 lot/verdict/offset 반영(서버 페이지네이션)", async () => {
    fetchInspections.mockResolvedValue([row]);
    renderApp(<InspectionsPage />);
    await screen.findByText("LOT-A");

    await userEvent.type(screen.getByTestId("filter-lot"), "LOT-A");
    await userEvent.selectOptions(screen.getByTestId("filter-verdict"), "NG");
    await userEvent.click(screen.getByTestId("apply-filters"));

    await waitFor(() => {
      const lastCall = fetchInspections.mock.calls.at(-1)?.[0];
      expect(lastCall).toMatchObject({
        lot: "LOT-A", verdict: "NG", limit: 25, offset: 0,
      });
    });
  });

  it("행 클릭 시 상세 모달 + 이미지 경로 조회", async () => {
    fetchInspections.mockResolvedValue([row]);
    renderApp(<InspectionsPage />);
    await userEvent.click(await screen.findByText("LOT-A"));
    expect(await screen.findByTestId("insp-detail")).toBeInTheDocument();
    expect(screen.getByTestId("detail-defects")).toHaveTextContent("LEN");
    await waitFor(() => expect(fetchInspectionImages).toHaveBeenCalledWith(7));
  });
});
