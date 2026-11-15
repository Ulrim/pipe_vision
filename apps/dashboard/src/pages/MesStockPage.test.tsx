/**
 * MES 자재·재고 — 현재고(안전재고 미달 경고), 입고, 조정(품질관리자 이상), 수불 이력.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Role } from "@aivis/shared-types";
import { renderApp } from "@/test/utils";
import { useAuthStore } from "@/store/auth";
import { mesItem, stockRow, stockTxn } from "@/test/mesPlantFixtures";

const api = vi.hoisted(() => ({
  fetchStock: vi.fn(),
  fetchStockTxns: vi.fn(),
  fetchMesItems: vi.fn(),
  createReceipt: vi.fn(),
  createAdjustment: vi.fn(),
}));
vi.mock("@/api/mesPlant", () => api);
vi.mock("@/api/mes", () => ({ MES_UNREACHABLE: "MES 서버에 연결할 수 없습니다" }));

import { MesStockPage } from "./MesStockPage";

function login(role: Role): void {
  useAuthStore.getState().setAuth({ token: "t.t.t", username: "kim", role });
}

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.fetchStock.mockResolvedValue([
    stockRow(),
    stockRow({ item_code: "BOX", name: "포장 박스", item_type: "SUB", unit: "EA", on_hand: 20, safety_stock: 50, below_safety: true }),
    stockRow({ item_code: "GHOST", name: null, item_type: null, unit: null, on_hand: -5, safety_stock: 0 }),
  ]);
  api.fetchStockTxns.mockResolvedValue([
    stockTxn(),
    stockTxn({ id: 2, qty: -127.5, txn_type: "ISSUE", wo_id: 1, wo_no: "WO-20261014-001", lot: null }),
    stockTxn({ id: 3, item_code: "HP12", qty: 470, txn_type: "PRODUCE", wo_id: 1, wo_no: "WO-20261014-001" }),
    stockTxn({ id: 4, item_code: "HP12", qty: -300, txn_type: "SHIP", shipment_id: 5 }),
    stockTxn({ id: 5, qty: -3.5, txn_type: "ADJUST", note: "실사 차이" }),
  ]);
  api.fetchMesItems.mockResolvedValue([
    mesItem(),
    mesItem({ item_code: "AL-TUBE", name: "알루미늄 튜브", item_type: "RAW", unit: "M" }),
  ]);
  api.createReceipt.mockImplementation((b: object) => Promise.resolve(stockTxn({ id: 9, ...b })));
  api.createAdjustment.mockImplementation((b: object) =>
    Promise.resolve(stockTxn({ id: 10, txn_type: "ADJUST", ...b })),
  );
  login(Role.QUALITY);
});

describe("MesStockPage — 현재고", () => {
  it("품목별 현재고, 안전재고 미달은 기호 + 글자로 경고", async () => {
    renderApp(<MesStockPage />);
    const ok = await screen.findByTestId("stock-row-AL-TUBE");
    expect(within(ok).getByText("원자재")).toBeInTheDocument();
    expect(screen.getByTestId("stock-state-AL-TUBE")).toHaveTextContent("✓ 정상");
    expect(screen.getByTestId("stock-state-BOX")).toHaveTextContent("⚠ 안전재고 미달");
    expect(screen.getByTestId("stock-row-BOX").className).toContain("bg-amber-50");
    expect(screen.getByTestId("stock-below-count")).toHaveTextContent("안전재고 미달 1품목");
    // 기준정보에 없어도 수불이 있으면 나온다 — 음수 재고는 부호와 함께.
    expect(screen.getByTestId("stock-row-GHOST")).toHaveTextContent("기준정보에 없는 품목");
    expect(screen.getByTestId("stock-row-GHOST")).toHaveTextContent("−5");
    expect(api.fetchStock).toHaveBeenCalledWith(undefined);
  });

  it("유형 필터 → item_type 으로 다시 조회", async () => {
    const user = userEvent.setup();
    renderApp(<MesStockPage />);
    await screen.findByTestId("stock-row-AL-TUBE");
    await user.selectOptions(screen.getByTestId("stock-type"), "원자재");
    await waitFor(() => expect(api.fetchStock).toHaveBeenCalledWith("RAW"));
  });

  it("MES 서버가 없으면 오류 상자", async () => {
    api.fetchStock.mockRejectedValue(new Error("MES 서버에 연결할 수 없습니다"));
    api.fetchStockTxns.mockRejectedValue(new Error("MES 서버에 연결할 수 없습니다"));
    renderApp(<MesStockPage />);
    expect(await screen.findByTestId("stock-error")).toHaveTextContent(
      "재고 조회 실패: MES 서버에 연결할 수 없습니다",
    );
    expect(await screen.findByTestId("txn-error")).toBeInTheDocument();
  });
});

describe("MesStockPage — 입고·조정", () => {
  it("입고: 사용 중인 품목만 조회해 고르고 → POST receipts", async () => {
    const user = userEvent.setup();
    renderApp(<MesStockPage />);
    const form = await screen.findByTestId("receipt-form");
    await waitFor(() =>
      expect(within(form).getByRole("option", { name: /AL-TUBE/ })).toBeInTheDocument(),
    );
    expect(api.fetchMesItems).toHaveBeenCalledWith({ active: true });
    expect(within(form).getByTestId("receipt-submit")).toBeDisabled();
    await user.selectOptions(within(form).getByTestId("receipt-item"), "AL-TUBE");
    await user.type(within(form).getByTestId("receipt-qty"), "150.5");
    await user.type(within(form).getByLabelText("LOT(선택)"), "RM-002");
    await user.click(within(form).getByTestId("receipt-submit"));
    await waitFor(() =>
      expect(api.createReceipt).toHaveBeenCalledWith({
        item_code: "AL-TUBE", qty: 150.5, lot: "RM-002", note: null,
      }),
    );
    expect(await screen.findByTestId("receipt-result")).toHaveTextContent("AL-TUBE +150.5");
  });

  it("조정: 사유가 없거나 0 이면 보낼 수 없고, 음수 수량 → POST adjustments", async () => {
    const user = userEvent.setup();
    renderApp(<MesStockPage />);
    const form = await screen.findByTestId("adjust-form");
    await waitFor(() =>
      expect(within(form).getByRole("option", { name: /AL-TUBE/ })).toBeInTheDocument(),
    );
    await user.selectOptions(within(form).getByTestId("adjust-item"), "AL-TUBE");
    await user.type(within(form).getByTestId("adjust-qty"), "-3.5");
    expect(within(form).getByTestId("adjust-submit")).toBeDisabled(); // 사유 없음
    await user.type(within(form).getByTestId("adjust-note"), "실사 차이");
    expect(within(form).getByTestId("adjust-submit")).toBeEnabled();
    await user.clear(within(form).getByTestId("adjust-qty"));
    await user.type(within(form).getByTestId("adjust-qty"), "0");
    expect(within(form).getByTestId("adjust-submit")).toBeDisabled(); // 0 은 안 됨
    await user.clear(within(form).getByTestId("adjust-qty"));
    await user.type(within(form).getByTestId("adjust-qty"), "-3.5");
    await user.click(within(form).getByTestId("adjust-submit"));
    await waitFor(() =>
      expect(api.createAdjustment).toHaveBeenCalledWith({
        item_code: "AL-TUBE", qty: -3.5, lot: null, note: "실사 차이",
      }),
    );
    expect(await screen.findByTestId("adjust-result")).toHaveTextContent("AL-TUBE −3.5");
  });

  it("작업자: 입고는 되고 조정 폼은 없다", async () => {
    login(Role.OPERATOR);
    renderApp(<MesStockPage />);
    expect(await screen.findByTestId("receipt-form")).toBeInTheDocument();
    expect(screen.queryByTestId("adjust-form")).not.toBeInTheDocument();
  });
});

describe("MesStockPage — 수불 이력", () => {
  it("유형 라벨(입고/투입/생산/출하/조정)과 부호 있는 수량", async () => {
    renderApp(<MesStockPage />);
    await screen.findByTestId("txn-row-1");
    const label = (id: number) => screen.getByTestId(`txn-row-${id}`);
    expect(label(1)).toHaveTextContent("입고");
    expect(label(2)).toHaveTextContent("투입");
    expect(label(3)).toHaveTextContent("생산");
    expect(label(4)).toHaveTextContent("출하");
    expect(label(5)).toHaveTextContent("조정");
    expect(screen.getByTestId("txn-qty-1")).toHaveTextContent("+200");
    expect(screen.getByTestId("txn-qty-2")).toHaveTextContent("−127.5");
    expect(screen.getByTestId("txn-qty-3")).toHaveTextContent("+470");
    expect(label(2)).toHaveTextContent("WO-20261014-001");
    expect(api.fetchStockTxns).toHaveBeenCalledWith({ item_code: undefined, limit: 200 });
  });

  it("품목 필터 → item_code 로 다시 조회", async () => {
    const user = userEvent.setup();
    renderApp(<MesStockPage />);
    await screen.findByTestId("txn-row-1");
    await waitFor(() =>
      expect(within(screen.getByTestId("txn-item")).getByRole("option", { name: "HP12" })).toBeInTheDocument(),
    );
    await user.selectOptions(screen.getByTestId("txn-item"), "HP12");
    await waitFor(() =>
      expect(api.fetchStockTxns).toHaveBeenCalledWith({ item_code: "HP12", limit: 200 }),
    );
  });
});
