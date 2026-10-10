import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict, DefectCode } from "@aivis/shared-types";
import { renderApp } from "@/test/utils";

// 엔드포인트 모킹(네트워크 차단).
const fetchInspections = vi.fn();
const fetchInspectionImageBlob = vi.fn();
const fetchLotSummary = vi.fn();
vi.mock("@/api/endpoints", () => ({
  fetchInspections: (...a: unknown[]) => fetchInspections(...a),
  fetchInspectionImageBlob: (...a: unknown[]) => fetchInspectionImageBlob(...a),
  fetchLotSummary: (...a: unknown[]) => fetchLotSummary(...a),
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
  fetchLotSummary.mockReset();
  fetchLotSummary.mockResolvedValue({
    lot: "LOT-A", item_codes: ["HP12"], final_verdict: "NG",
    reasons: ["길이 검사 NG 1개 / 20개 (LEN 1)", "표면 검사 결과 없음"],
    required_stages: ["CUT_LENGTH", "POST_WASH_SURFACE"], missing_stages: ["POST_WASH_SURFACE"],
    stages: [{ stage: "CUT_LENGTH", label: "길이 검사", cam_ids: ["PI-CAM1"], total: 20, ng: 1,
      ng_rate_pct: 5, by_code: { LEN: 1 }, pending_review: 1, first_at: null, last_at: null }],
  });
  fetchInspections.mockReset();
  fetchInspectionImageBlob.mockReset();
  // 상세 모달이 raw/result 이미지를 인증 fetch→Blob→objectURL 로 표시.
  fetchInspectionImageBlob.mockResolvedValue(new Blob(["x"], { type: "image/jpeg" }));
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

  it("스테이션·검사 모드 필터가 cam_id/stage 쿼리로 나간다(2대 구성)", async () => {
    fetchInspections.mockResolvedValue([
      { ...row, cam_id: "PI-CAM2", inspection_stage: "CRATE_COUNT" },
    ]);
    renderApp(<InspectionsPage />);
    await screen.findByText("LOT-A");
    // 표 행에 모드(한글)·스테이션(원문)이 보인다. (필터 select 에도 같은 글자가
    // 있으므로 행 안에서 찾는다.)
    const tr = screen.getByTestId("insp-row");
    expect(within(tr).getByText("개수 확인")).toBeInTheDocument();
    expect(within(tr).getByText("PI-CAM2")).toBeInTheDocument();

    await userEvent.type(screen.getByTestId("filter-cam"), "PI-CAM2");
    await userEvent.selectOptions(screen.getByTestId("filter-stage"), "CRATE_COUNT");
    await userEvent.click(screen.getByTestId("apply-filters"));
    await waitFor(() => {
      const lastCall = fetchInspections.mock.calls.at(-1)?.[0];
      expect(lastCall).toMatchObject({ cam_id: "PI-CAM2", stage: "CRATE_COUNT" });
    });
  });

  it("LOT 으로 검색하면 LOT 종합 판정(모드별 결과를 합친 결론)이 뜬다", async () => {
    fetchInspections.mockResolvedValue([row]);
    renderApp(<InspectionsPage />);
    await screen.findByText("LOT-A");
    expect(screen.queryByTestId("lot-summary")).not.toBeInTheDocument();
    await userEvent.type(screen.getByTestId("filter-lot"), "LOT-A");
    await userEvent.click(screen.getByTestId("apply-filters"));
    const card = await screen.findByTestId("lot-summary");
    expect(card).toHaveAttribute("data-verdict", "NG");
    expect(card).toHaveTextContent("LOT NG");
    expect(screen.getByTestId("lot-reasons")).toHaveTextContent("표면 검사 결과 없음");
    expect(screen.getByTestId("lot-stage-CUT_LENGTH")).toHaveTextContent("LEN 1");
    expect(fetchLotSummary).toHaveBeenCalledWith("LOT-A");
  });

  it("행 클릭 시 상세 모달 + raw/result 이미지 인증 조회", async () => {
    fetchInspections.mockResolvedValue([row]);
    renderApp(<InspectionsPage />);
    await userEvent.click(await screen.findByText("LOT-A"));
    expect(await screen.findByTestId("insp-detail")).toBeInTheDocument();
    expect(screen.getByTestId("detail-defects")).toHaveTextContent("LEN");
    await waitFor(() => {
      expect(fetchInspectionImageBlob).toHaveBeenCalledWith(7, "result");
      expect(fetchInspectionImageBlob).toHaveBeenCalledWith(7, "raw");
    });
  });
});
