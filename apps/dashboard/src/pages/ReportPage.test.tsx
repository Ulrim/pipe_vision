import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { KpiSummary } from "@aivis/shared-types";
import { renderApp } from "@/test/utils";

const fetchKpiReportPreview = vi.fn();
const fetchKpiReport = vi.fn();
vi.mock("@/api/endpoints", () => ({
  fetchKpiReportPreview: (...a: unknown[]) => fetchKpiReportPreview(...a),
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

/** 미리보기 응답 — PDF/XLSX 와 같은 서버 집계 결과를 그대로 화면에 그린다. */
const preview = {
  period: "2026-06",
  summary,
  proc_time: { p50: 170, p95: 252, p99: 259 },
  targets: [
    { key: "process_defect_ppm", label: "공정불량률 (ppm)", label_en: "x",
      target: "600 이하", actual: "500.000", achieved: true },
    { key: "p95_proc_time_ms", label: "처리속도 p95 (ms)", label_en: "x",
      target: "300 이하", actual: "252", achieved: false },
    { key: "storage_mes_rate_pct", label: "저장·MES 연계율 (%)", label_en: "x",
      target: "100", actual: "-", achieved: null },
  ],
  defects: [{ code: "LEN", label: "LEN (길이)", count: 3 }],
  daily: [
    { date: "2026-06-01", inspected: 50, defects: 1, ppm: 20000 },
    { date: "2026-06-02", inspected: 50, defects: 0, ppm: 0 },
  ],
};

beforeEach(() => {
  fetchKpiReportPreview.mockReset().mockResolvedValue(preview);
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

describe("ReportPage 미리보기 상세 (M12)", () => {
  it("처리속도 백분위와 목표 대비 달성표를 표시한다", async () => {
    renderApp(<ReportPage />);
    // report-preview 는 로딩 중에도 존재하므로 실제 데이터 요소를 기다린다.
    const pct = await screen.findByTestId("proc-percentiles");
    expect(pct).toHaveTextContent("252");
    expect(pct).toHaveTextContent("259");

    const table = screen.getByTestId("target-table");
    expect(table).toHaveTextContent("공정불량률 (ppm)");
    expect(table).toHaveTextContent("600 이하");
  });

  it("달성 여부는 색 단독이 아니라 기호+문자로 표기한다(색각 이상 고려)", async () => {
    renderApp(<ReportPage />);
    const table = await screen.findByTestId("target-table");
    // 달성/미달/판정보류 세 상태 모두 문자로 읽을 수 있어야 한다.
    expect(table).toHaveTextContent("O 달성");
    expect(table).toHaveTextContent("X 미달");
    expect(table).toHaveTextContent("- 판정보류");
  });

  it("추세/불량유형 차트를 렌더한다", async () => {
    renderApp(<ReportPage />);
    expect(await screen.findByTestId("daily-ppm-trend")).toBeInTheDocument();
    expect(screen.getByTestId("defect-bar")).toBeInTheDocument();
  });
});
