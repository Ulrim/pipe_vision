/**
 * 수기 KPI 입력 폼 (M12).
 *
 * 이 폼이 없던 동안 Claim·작업공수·리드타임은 화면에 표시만 되고 입력할 수
 * 없었다(API 는 있는데 부르는 UI 가 없었음). 계약 성과지표인 출하유출불량률도
 * 여기서 받는 두 값(출하수량·유출 부적합수량) 없이는 영영 산출되지 않는다.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Role, type KpiSummary } from "@aivis/shared-types";

const upsertKpiManual = vi.fn();
vi.mock("@/api/endpoints", () => ({
  upsertKpiManual: (...a: unknown[]) => upsertKpiManual(...a),
}));

import { KpiManualForm } from "./KpiManualForm";
import { useAuthStore } from "@/store/auth";

const summary: KpiSummary = {
  period: "2026-06", total_inspected: 10, defect_count: 0,
  process_defect_ppm: 0, auto_inspected: 10, auto_inspection_rate_pct: 100,
  misjudge_count: 0, miss_count: 0, inspection_defect_rate_pct: 0,
  stored_count: 10, mes_synced_count: 10, storage_mes_rate_pct: 100,
  avg_proc_time_ms: 100,
};

function renderForm(s: KpiSummary | undefined = summary) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <KpiManualForm period="2026-06" summary={s} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  upsertKpiManual.mockReset();
  upsertKpiManual.mockResolvedValue({});
  useAuthStore.getState().setAuth({
    token: "t", username: "qa", role: Role.QUALITY,
  });
});

describe("KpiManualForm", () => {
  it("입력값을 월 1일 키로 저장한다", async () => {
    const user = userEvent.setup();
    renderForm();
    await user.type(screen.getByTestId("kpi-shipped"), "200000");
    await user.type(screen.getByTestId("kpi-leak"), "150");
    await user.click(screen.getByTestId("kpi-manual-save"));

    await waitFor(() => expect(upsertKpiManual).toHaveBeenCalled());
    expect(upsertKpiManual.mock.calls[0][0]).toMatchObject({
      period: "2026-06-01",
      shipped_qty: 200000,
      leak_defect_qty: 150,
    });
  });

  it("출하유출불량률을 저장 전에 미리 보여준다", async () => {
    const user = userEvent.setup();
    renderForm();
    await user.type(screen.getByTestId("kpi-shipped"), "200000");
    await user.type(screen.getByTestId("kpi-leak"), "150");
    // 150 / 200000 × 1e6 = 750 ppm
    expect(screen.getByTestId("kpi-leak-preview")).toHaveTextContent("750.0 ppm");
  });

  it("빈 칸은 0 이 아니라 null 로 보낸다(미입력과 '값이 0' 은 다르다)", async () => {
    const user = userEvent.setup();
    renderForm();
    await user.type(screen.getByTestId("kpi-claim"), "0");
    await user.click(screen.getByTestId("kpi-manual-save"));

    await waitFor(() => expect(upsertKpiManual).toHaveBeenCalled());
    const body = upsertKpiManual.mock.calls[0][0];
    expect(body.claim_count).toBe(0);
    expect(body.shipped_qty).toBeNull();
  });

  it("작업자(operator)는 저장할 수 없다", () => {
    useAuthStore.getState().setAuth({
      token: "t", username: "op", role: Role.OPERATOR,
    });
    renderForm();
    expect(screen.getByTestId("kpi-manual-save")).toBeDisabled();
    expect(screen.getByTestId("kpi-shipped")).toBeDisabled();
  });

  it("저장된 값이 있으면 폼에 그대로 채워진다", () => {
    renderForm({ ...summary, shipped_qty: 5000, leak_defect_qty: 2 });
    expect(screen.getByTestId("kpi-shipped")).toHaveValue(5000);
    expect(screen.getByTestId("kpi-leak")).toHaveValue(2);
  });
});
