import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { KpiSummary } from "@aivis/shared-types";
import { renderApp } from "@/test/utils";

const fetchKpiSummary = vi.fn();
const fetchKpiReport = vi.fn();
vi.mock("@/api/endpoints", () => ({
  fetchKpiSummary: (...a: unknown[]) => fetchKpiSummary(...a),
  fetchKpiReport: (...a: unknown[]) => fetchKpiReport(...a),
}));

const triggerBlobDownload = vi.fn();
vi.mock("@/lib/download", () => ({
  triggerBlobDownload: (...a: unknown[]) => triggerBlobDownload(...a),
}));

import { ReportPage } from "./ReportPage";

const summary: KpiSummary = {
  period: "2026-06", total_inspected: 100, defect_count: 1,
  process_defect_ppm: 500, auto_inspected: 100, auto_inspection_rate_pct: 100,
  misjudge_count: 0, miss_count: 0, inspection_defect_rate_pct: 0,
  stored_count: 100, mes_synced_count: 100, storage_mes_rate_pct: 100,
  avg_proc_time_ms: 250,
};

beforeEach(() => {
  fetchKpiSummary.mockReset().mockResolvedValue(summary);
  fetchKpiReport.mockReset();
  triggerBlobDownload.mockReset();
});

describe("ReportPage", () => {
  it("미리보기 KPI 렌더", async () => {
    renderApp(<ReportPage />);
    expect(await screen.findByText("공정불량률")).toBeInTheDocument();
    expect(screen.getByTestId("report-preview")).toBeInTheDocument();
  });

  it("PDF 내보내기 클릭 시 report 엔드포인트(fmt=pdf) 호출 + blob 다운로드 트리거", async () => {
    const blob = new Blob(["%PDF"], { type: "application/pdf" });
    fetchKpiReport.mockResolvedValue({ blob, filename: "report.pdf" });
    renderApp(<ReportPage />);
    await screen.findByTestId("report-preview");

    await userEvent.click(screen.getByTestId("download-pdf"));

    await waitFor(() => {
      expect(fetchKpiReport).toHaveBeenCalledWith(expect.any(String), "pdf");
      expect(triggerBlobDownload).toHaveBeenCalledWith(blob, "report.pdf");
    });
    expect(await screen.findByTestId("report-msg")).toHaveTextContent("완료");
  });

  it("엑셀 내보내기 실패 시 오류 메시지", async () => {
    fetchKpiReport.mockRejectedValue(new Error("server error"));
    renderApp(<ReportPage />);
    await screen.findByTestId("report-preview");
    await userEvent.click(screen.getByTestId("download-xlsx"));
    expect(await screen.findByTestId("report-msg")).toHaveTextContent("실패");
    expect(fetchKpiReport).toHaveBeenCalledWith(expect.any(String), "xlsx");
  });
});
