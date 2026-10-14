/**
 * MES 작업지시 — 등록·상태 변경·"검사기에 오더 내리기"(AIVIS 현재 검사 오더로 넘김).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Role } from "@aivis/shared-types";
import { renderApp } from "@/test/utils";
import { useAuthStore } from "@/store/auth";
import { salesOrder, workOrder } from "@/test/mesFixtures";
import { todayIso } from "@/lib/mes";

const fetchWorkOrders = vi.fn();
const fetchSalesOrders = vi.fn();
const createWorkOrder = vi.fn();
const createSalesOrder = vi.fn();
const patchWorkOrder = vi.fn();
const patchSalesOrder = vi.fn();
vi.mock("@/api/mes", () => ({
  MES_UNREACHABLE: "MES 서버에 연결할 수 없습니다",
  fetchWorkOrders: (...a: unknown[]) => fetchWorkOrders(...a),
  fetchSalesOrders: (...a: unknown[]) => fetchSalesOrders(...a),
  createWorkOrder: (...a: unknown[]) => createWorkOrder(...a),
  createSalesOrder: (...a: unknown[]) => createSalesOrder(...a),
  patchWorkOrder: (...a: unknown[]) => patchWorkOrder(...a),
  patchSalesOrder: (...a: unknown[]) => patchSalesOrder(...a),
}));

const fetchItems = vi.fn();
const fetchActiveOrder = vi.fn();
const putActiveOrder = vi.fn();
vi.mock("@/api/endpoints", () => ({
  fetchItems: (...a: unknown[]) => fetchItems(...a),
  fetchActiveOrder: (...a: unknown[]) => fetchActiveOrder(...a),
  putActiveOrder: (...a: unknown[]) => putActiveOrder(...a),
}));

import { MesOrdersPage } from "./MesOrdersPage";

const ITEMS = [
  { item_code: "HP12", item_name: "헤더파이프12" },
  { item_code: "HP16", item_name: "헤더파이프16" },
];

function login(role: Role): void {
  useAuthStore.getState().setAuth({ token: "t.t.t", username: "kim", role });
}

beforeEach(() => {
  fetchWorkOrders.mockReset().mockResolvedValue([
    workOrder(),
    workOrder({ id: 2, wo_no: "WO-20261014-002", lot: "L-20261014-02", status: "RUNNING", progress_pct: 80 }),
  ]);
  fetchSalesOrders.mockReset().mockImplementation((status?: string) =>
    Promise.resolve(
      status === "OPEN"
        ? [salesOrder({ shipped_qty: 100 })]
        : [salesOrder(), salesOrder({ id: 8, so_no: "SO-20261001-003", status: "CLOSED" })],
    ),
  );
  createWorkOrder.mockReset().mockResolvedValue(workOrder({ id: 3, wo_no: "WO-NEW", lot: "L-TEST-01" }));
  createSalesOrder.mockReset().mockResolvedValue(salesOrder({ id: 9, so_no: "SO-NEW" }));
  patchWorkOrder.mockReset().mockImplementation((id: number, body: { status: string }) =>
    Promise.resolve(workOrder({ id, status: body.status })),
  );
  patchSalesOrder.mockReset().mockResolvedValue(salesOrder({ status: "CLOSED" }));
  fetchItems.mockReset().mockResolvedValue(ITEMS);
  fetchActiveOrder.mockReset().mockResolvedValue(null);
  putActiveOrder.mockReset().mockResolvedValue({
    item_code: "HP12", lot: "L-20261014-01", work_order: "WO-20261014-001",
    updated_by: "kim", updated_at: "2026-10-14T03:00:00Z",
  });
  login(Role.QUALITY);
});

describe("MesOrdersPage — 목록", () => {
  it("작업지시·수주를 보여 주고, 기본 필터는 진행 중(PLANNED,RUNNING)", async () => {
    renderApp(<MesOrdersPage />);
    const row = await screen.findByTestId("wo-row-1");
    expect(within(row).getByText("WO-20261014-001")).toBeInTheDocument();
    expect(within(row).getByText("L-20261014-01")).toBeInTheDocument();
    expect(within(row).getByText(/한라공조/)).toBeInTheDocument();
    expect(within(row).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "24");
    // 상태는 색만이 아니라 기호 + 글자.
    expect(screen.getByTestId("wo-status-1")).toHaveTextContent("○계획");
    expect(screen.getByTestId("wo-status-2")).toHaveTextContent("▶진행 중");
    expect(fetchWorkOrders).toHaveBeenCalledWith("PLANNED,RUNNING");

    const so = await screen.findByTestId("so-row-7");
    expect(within(so).getByText("0 / 500")).toBeInTheDocument();
    expect(screen.getByTestId("so-close-7")).toBeInTheDocument();
    expect(screen.queryByTestId("so-close-8")).not.toBeInTheDocument();
  });

  it("필터 '완료' 로 바꾸면 DONE 으로 다시 조회", async () => {
    const user = userEvent.setup();
    renderApp(<MesOrdersPage />);
    await screen.findByTestId("wo-row-1");
    await user.selectOptions(screen.getByTestId("wo-filter"), "완료");
    await waitFor(() => expect(fetchWorkOrders).toHaveBeenCalledWith("DONE"));
  });

  it("작업지시가 없으면 빈 상태 문구", async () => {
    fetchWorkOrders.mockResolvedValue([]);
    renderApp(<MesOrdersPage />);
    expect(await screen.findByTestId("wo-empty")).toHaveTextContent("진행 중인 작업지시가 없습니다.");
  });

  it("MES 서버가 없으면 오류 상자(화면은 깨지지 않는다)", async () => {
    fetchWorkOrders.mockRejectedValue(new Error("MES 서버에 연결할 수 없습니다"));
    fetchSalesOrders.mockRejectedValue(new Error("MES 서버에 연결할 수 없습니다"));
    renderApp(<MesOrdersPage />);
    const box = await screen.findByTestId("wo-error");
    expect(box).toHaveTextContent("작업지시 조회 실패: MES 서버에 연결할 수 없습니다");
    expect(box).toHaveTextContent("8100");
    expect(await screen.findByTestId("so-error")).toBeInTheDocument();
  });
});

describe("MesOrdersPage — 등록", () => {
  it("작업지시 등록: 수주를 고르면 품목·남은 수량이 채워지고, 그 본문으로 POST", async () => {
    const user = userEvent.setup();
    renderApp(<MesOrdersPage />);
    const form = await screen.findByTestId("wo-form");
    await waitFor(() =>
      expect(within(form).getByRole("option", { name: /SO-20261014-001/ })).toBeInTheDocument(),
    );
    await user.selectOptions(within(form).getByTestId("wo-so"), "7");
    expect(within(form).getByTestId("wo-item")).toHaveValue("HP12");
    expect(within(form).getByLabelText("지시수량")).toHaveValue(400); // 500 − 출하 100
    await user.type(within(form).getByLabelText("LOT(선택)"), "L-TEST-01");
    await user.click(within(form).getByTestId("wo-submit"));

    await waitFor(() => expect(createWorkOrder).toHaveBeenCalledTimes(1));
    expect(createWorkOrder.mock.calls[0][0]).toEqual({
      so_id: 7,
      item_code: "HP12",
      lot: "L-TEST-01",
      plan_qty: 400,
      plan_date: todayIso(),
    });
    expect(await within(form).findByText(/작업지시 WO-NEW/)).toBeInTheDocument();
  });

  it("작업지시 등록: 수주 없이 — LOT 을 비우면 null(서버 자동 채번)", async () => {
    const user = userEvent.setup();
    renderApp(<MesOrdersPage />);
    const form = await screen.findByTestId("wo-form");
    await waitFor(() =>
      expect(within(form).getByRole("option", { name: /HP16/ })).toBeInTheDocument(),
    );
    await user.selectOptions(within(form).getByTestId("wo-item"), "HP16");
    await user.type(within(form).getByLabelText("지시수량"), "300");
    await user.click(within(form).getByTestId("wo-submit"));
    await waitFor(() =>
      expect(createWorkOrder).toHaveBeenCalledWith({
        so_id: null,
        item_code: "HP16",
        lot: null,
        plan_qty: 300,
        plan_date: todayIso(),
      }),
    );
  });

  it("수주 등록: 고객·품목·수량·수주일(기본 오늘)", async () => {
    const user = userEvent.setup();
    renderApp(<MesOrdersPage />);
    const form = await screen.findByTestId("so-form");
    await waitFor(() =>
      expect(within(form).getByRole("option", { name: /HP12/ })).toBeInTheDocument(),
    );
    expect(within(form).getByTestId("so-submit")).toBeDisabled();
    await user.type(within(form).getByLabelText("고객"), "두원공조");
    await user.selectOptions(within(form).getByTestId("so-item"), "HP12");
    await user.type(within(form).getByLabelText("수량"), "1000");
    await user.click(within(form).getByTestId("so-submit"));
    await waitFor(() =>
      expect(createSalesOrder).toHaveBeenCalledWith({
        customer: "두원공조",
        item_code: "HP12",
        qty: 1000,
        order_date: todayIso(),
        due_date: null,
        note: null,
      }),
    );
  });

  it("수주 마감 → PATCH status CLOSED", async () => {
    const user = userEvent.setup();
    renderApp(<MesOrdersPage />);
    await user.click(await screen.findByTestId("so-close-7"));
    await waitFor(() => expect(patchSalesOrder).toHaveBeenCalledWith(7, { status: "CLOSED" }));
  });
});

describe("MesOrdersPage — 검사기에 오더 내리기", () => {
  it("계획(PLANNED) 작업지시: AIVIS 현재 오더로 넘긴 뒤 진행 중(RUNNING)으로 바꾼다", async () => {
    const user = userEvent.setup();
    renderApp(<MesOrdersPage />);
    await user.click(await screen.findByTestId("wo-dispatch-1"));

    await waitFor(() =>
      expect(putActiveOrder).toHaveBeenCalledWith({
        item_code: "HP12",
        lot: "L-20261014-01",
        work_order: "WO-20261014-001",
      }),
    );
    await waitFor(() => expect(patchWorkOrder).toHaveBeenCalledWith(1, { status: "RUNNING" }));
    // 오더 → 상태 순서(검사기 전환이 먼저).
    expect(putActiveOrder.mock.invocationCallOrder[0]).toBeLessThan(
      patchWorkOrder.mock.invocationCallOrder[0],
    );
    const msg = await screen.findByTestId("wo-msg");
    expect(msg).toHaveTextContent("15초");
    expect(msg).toHaveTextContent("진행 중");
  });

  it("이미 진행 중(RUNNING)이면 오더만 다시 내리고 상태는 건드리지 않는다", async () => {
    const user = userEvent.setup();
    renderApp(<MesOrdersPage />);
    await user.click(await screen.findByTestId("wo-dispatch-2"));
    await waitFor(() => expect(putActiveOrder).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId("wo-msg")).toHaveTextContent("15초");
    expect(patchWorkOrder).not.toHaveBeenCalled();
  });

  it("검사기 오더가 실패하면 작업지시 상태도 바꾸지 않는다", async () => {
    const { ApiError } = await import("@/api/client");
    putActiveOrder.mockRejectedValue(new ApiError(404, "품목 없음"));
    const user = userEvent.setup();
    renderApp(<MesOrdersPage />);
    await user.click(await screen.findByTestId("wo-dispatch-1"));
    expect(await screen.findByTestId("wo-msg")).toHaveTextContent("기준정보에 품목 HP12");
    expect(patchWorkOrder).not.toHaveBeenCalled();
  });

  it("지금 검사기에 걸린 작업지시는 표에 표시", async () => {
    fetchActiveOrder.mockResolvedValue({
      item_code: "HP12", lot: "L-20261014-02", work_order: "WO-20261014-002",
      updated_by: "kim", updated_at: null,
    });
    renderApp(<MesOrdersPage />);
    expect(await screen.findByTestId("wo-on-inspector-2")).toBeInTheDocument();
    expect(screen.queryByTestId("wo-on-inspector-1")).not.toBeInTheDocument();
  });
});

describe("MesOrdersPage — 작업자(operator)", () => {
  beforeEach(() => login(Role.OPERATOR));

  it("등록 폼·검사기 오더·마감은 안 보이고, 시작·완료·취소는 할 수 있다", async () => {
    const user = userEvent.setup();
    renderApp(<MesOrdersPage />);
    await screen.findByTestId("wo-row-1");
    expect(screen.queryByTestId("wo-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("so-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("wo-dispatch-1")).not.toBeInTheDocument();
    await screen.findByTestId("so-row-7");
    expect(screen.queryByTestId("so-close-7")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("wo-start-1"));
    await waitFor(() => expect(patchWorkOrder).toHaveBeenCalledWith(1, { status: "RUNNING" }));
  });

  it("취소는 한 번 더 확인해야 실행된다", async () => {
    const user = userEvent.setup();
    renderApp(<MesOrdersPage />);
    await user.click(await screen.findByTestId("wo-cancel-2"));
    expect(patchWorkOrder).not.toHaveBeenCalled();
    await user.click(screen.getByTestId("wo-cancel-confirm-2"));
    await waitFor(() => expect(patchWorkOrder).toHaveBeenCalledWith(2, { status: "CANCELLED" }));
  });
});
