/**
 * MES 출하·클레임 — 월 KPI 를 AIVIS KPI 수기 입력에 넘길 때 네 칸만 보낸다
 * (작업공수·비고를 null 로 덮어쓰면 사람이 적어 둔 값이 사라진다).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Role } from "@aivis/shared-types";
import { renderApp } from "@/test/utils";
import { useAuthStore } from "@/store/auth";
import { currentPeriod } from "@/lib/format";
import { todayIso } from "@/lib/mes";
import { claim, monthlyKpi, salesOrder, shipment } from "@/test/mesFixtures";

const fetchMonthlyKpi = vi.fn();
const fetchShipments = vi.fn();
const fetchClaims = vi.fn();
const fetchSalesOrders = vi.fn();
const createShipment = vi.fn();
const createClaim = vi.fn();
const patchClaim = vi.fn();
vi.mock("@/api/mes", () => ({
  MES_UNREACHABLE: "MES 서버에 연결할 수 없습니다",
  fetchMonthlyKpi: (...a: unknown[]) => fetchMonthlyKpi(...a),
  fetchShipments: (...a: unknown[]) => fetchShipments(...a),
  fetchClaims: (...a: unknown[]) => fetchClaims(...a),
  fetchSalesOrders: (...a: unknown[]) => fetchSalesOrders(...a),
  createShipment: (...a: unknown[]) => createShipment(...a),
  createClaim: (...a: unknown[]) => createClaim(...a),
  patchClaim: (...a: unknown[]) => patchClaim(...a),
}));

const fetchItems = vi.fn();
const upsertKpiManual = vi.fn();
vi.mock("@/api/endpoints", () => ({
  fetchItems: (...a: unknown[]) => fetchItems(...a),
  upsertKpiManual: (...a: unknown[]) => upsertKpiManual(...a),
}));

import { MesShippingPage } from "./MesShippingPage";

function login(role: Role): void {
  useAuthStore.getState().setAuth({ token: "t.t.t", username: "kim", role });
}

beforeEach(() => {
  fetchMonthlyKpi.mockReset().mockImplementation((period: string) =>
    Promise.resolve(monthlyKpi({ period })),
  );
  fetchShipments.mockReset().mockResolvedValue([
    shipment(),
    shipment({ id: 32, ship_no: "SH-20261014-002", on_time: false, lead_time_days: 12 }),
    shipment({ id: 33, ship_no: "SH-20261014-003", so_id: null, so_no: null, on_time: null, lead_time_days: null }),
  ]);
  fetchClaims.mockReset().mockResolvedValue([claim(), claim({ id: 42, claim_no: "CL-OLD", status: "CLOSED" })]);
  fetchSalesOrders.mockReset().mockResolvedValue([salesOrder({ shipped_qty: 200 })]);
  createShipment.mockReset().mockResolvedValue(shipment({ id: 34, ship_no: "SH-NEW" }));
  createClaim.mockReset().mockResolvedValue(claim({ id: 43, claim_no: "CL-NEW" }));
  patchClaim.mockReset().mockResolvedValue(claim({ status: "CLOSED" }));
  fetchItems.mockReset().mockResolvedValue([{ item_code: "HP12", item_name: "헤더파이프12" }]);
  upsertKpiManual.mockReset().mockResolvedValue({});
  login(Role.QUALITY);
});

describe("MesShippingPage — 월 KPI", () => {
  it("이번 달 KPI 와 출하·클레임 목록", async () => {
    renderApp(<MesShippingPage />);
    expect(screen.getByTestId("ship-period")).toHaveValue(currentPeriod());
    expect(await screen.findByTestId("kpi-shipped-qty")).toHaveTextContent("1,200");
    expect(screen.getByTestId("mes-kpi")).toHaveTextContent("표본 2건");
    expect(screen.getByTestId("mes-kpi")).toHaveTextContent("50%");
    expect(fetchMonthlyKpi).toHaveBeenCalledWith(currentPeriod());
    expect(fetchShipments).toHaveBeenCalledWith(currentPeriod());

    // 납기: 색 + 기호 + 글자.
    expect(await screen.findByTestId("ship-ontime-31")).toHaveTextContent("✓ 납기 내");
    expect(screen.getByTestId("ship-ontime-32")).toHaveTextContent("✕ 납기 초과");
    expect(screen.getByTestId("ship-ontime-33")).toHaveTextContent("-");

    const c = await screen.findByTestId("claim-row-41");
    expect(within(c).getByText("스크래치")).toBeInTheDocument();
    expect(within(c).getByText("처리 중")).toBeInTheDocument();
    expect(screen.queryByTestId("claim-close-42")).not.toBeInTheDocument();
  });

  it("'AIVIS KPI 에 반영' 은 월(YYYY-MM-01)과 네 칸만 보낸다", async () => {
    const user = userEvent.setup();
    renderApp(<MesShippingPage />);
    await screen.findByTestId("kpi-shipped-qty");
    await user.click(screen.getByTestId("kpi-reflect"));

    await waitFor(() => expect(upsertKpiManual).toHaveBeenCalledTimes(1));
    const body = upsertKpiManual.mock.calls[0][0] as Record<string, unknown>;
    expect(body).toEqual({
      period: `${currentPeriod()}-01`,
      claim_count: 1,
      lead_time_days: 4.5,
      shipped_qty: 1200,
      leak_defect_qty: 3,
    });
    // 작업공수·비고는 키 자체가 없어야 한다(null 도 덮어쓰기다).
    expect(Object.keys(body).sort()).toEqual(
      ["claim_count", "lead_time_days", "leak_defect_qty", "period", "shipped_qty"],
    );
    expect(await screen.findByTestId("kpi-reflect-msg")).toHaveTextContent(
      "KPI 에 반영했습니다(작업공수·비고는 그대로)",
    );
  });

  it("월을 바꾸면 그 달로 다시 조회하고 반영 메시지는 사라진다", async () => {
    const user = userEvent.setup();
    renderApp(<MesShippingPage />);
    await screen.findByTestId("kpi-shipped-qty");
    await user.click(screen.getByTestId("kpi-reflect"));
    await screen.findByTestId("kpi-reflect-msg");

    const input = screen.getByTestId("ship-period");
    // 빈 값(지우기)은 무시된다 — 빈 period 는 서버가 '이번 달' 로 바꿔 표기와 어긋난다.
    fireEvent.change(input, { target: { value: "" } });
    expect(input).toHaveValue(currentPeriod());
    fireEvent.change(input, { target: { value: "2026-09" } });
    await waitFor(() => expect(fetchMonthlyKpi).toHaveBeenCalledWith("2026-09"));
    expect(screen.queryByTestId("kpi-reflect-msg")).not.toBeInTheDocument();
  });

  it("MES 서버가 없으면 오류 상자, 출하가 없으면 빈 상태", async () => {
    fetchMonthlyKpi.mockRejectedValue(new Error("MES 서버에 연결할 수 없습니다"));
    fetchShipments.mockResolvedValue([]);
    fetchClaims.mockResolvedValue([]);
    renderApp(<MesShippingPage />);
    expect(await screen.findByTestId("mes-kpi-error")).toHaveTextContent(
      "월 KPI 조회 실패: MES 서버에 연결할 수 없습니다",
    );
    expect(screen.getByTestId("kpi-reflect")).toBeDisabled();
    expect(await screen.findByTestId("ship-empty")).toHaveTextContent("이 달 출하가 없습니다.");
    expect(await screen.findByTestId("claim-empty")).toBeInTheDocument();
  });

  it("작업자는 반영 버튼·등록 폼·종결 버튼이 없다", async () => {
    login(Role.OPERATOR);
    renderApp(<MesShippingPage />);
    await screen.findByTestId("kpi-shipped-qty");
    await screen.findByTestId("claim-row-41");
    expect(screen.queryByTestId("kpi-reflect")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ship-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("claim-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("claim-close-41")).not.toBeInTheDocument();
  });
});

describe("MesShippingPage — 출하·클레임 등록", () => {
  it("출하 등록: 수주를 고르면 고객·품목·남은 수량이 채워진다", async () => {
    const user = userEvent.setup();
    renderApp(<MesShippingPage />);
    const form = await screen.findByTestId("ship-form");
    await waitFor(() =>
      expect(within(form).getByRole("option", { name: /SO-20261014-001/ })).toBeInTheDocument(),
    );
    await user.selectOptions(within(form).getByTestId("ship-so"), "7");
    expect(within(form).getByLabelText("고객")).toHaveValue("한라공조");
    expect(within(form).getByTestId("ship-item")).toHaveValue("HP12");
    expect(within(form).getByLabelText("출하 수량")).toHaveValue(300);
    await user.type(within(form).getByLabelText("LOT(선택)"), "L-20261014-01");
    await user.click(within(form).getByTestId("ship-submit"));
    await waitFor(() =>
      expect(createShipment).toHaveBeenCalledWith({
        so_id: 7,
        customer: "한라공조",
        item_code: "HP12",
        lot: "L-20261014-01",
        qty: 300,
        ship_date: todayIso(),
        note: null,
      }),
    );
    expect(await within(form).findByText(/출하 SH-NEW/)).toBeInTheDocument();
  });

  it("클레임 등록 → 본문, 종결 → PATCH status CLOSED", async () => {
    const user = userEvent.setup();
    renderApp(<MesShippingPage />);
    const form = await screen.findByTestId("claim-form");
    await user.type(within(form).getByLabelText("고객"), "한라공조");
    await user.type(within(form).getByLabelText("LOT(선택)"), "L-20261001-02");
    const qty = within(form).getByLabelText("부적합 수량");
    await user.clear(qty);
    await user.type(qty, "5");
    await user.selectOptions(within(form).getByTestId("claim-type"), "OIL");
    await user.type(within(form).getByLabelText("내용(선택)"), "세척 후 유분 잔존");
    await user.click(within(form).getByTestId("claim-submit"));
    await waitFor(() =>
      expect(createClaim).toHaveBeenCalledWith({
        customer: "한라공조",
        lot: "L-20261001-02",
        item_code: null,
        defect_qty: 5,
        defect_type: "OIL",
        occurred_date: todayIso(),
        description: "세척 후 유분 잔존",
      }),
    );

    await user.click(screen.getByTestId("claim-close-41"));
    await waitFor(() => expect(patchClaim).toHaveBeenCalledWith(41, { status: "CLOSED" }));
  });
});
