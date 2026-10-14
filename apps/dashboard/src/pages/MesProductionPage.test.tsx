/**
 * MES 생산실적 — 작업지시를 고르면 단계별 실적과 양품 출처가 나온다.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Role } from "@aivis/shared-types";
import { renderApp } from "@/test/utils";
import { useAuthStore } from "@/store/auth";
import { production, workOrder } from "@/test/mesFixtures";

const fetchWorkOrders = vi.fn();
const fetchWorkOrder = vi.fn();
const fetchUnassignedLots = vi.fn();
vi.mock("@/api/mes", () => ({
  MES_UNREACHABLE: "MES 서버에 연결할 수 없습니다",
  fetchWorkOrders: (...a: unknown[]) => fetchWorkOrders(...a),
  fetchWorkOrder: (...a: unknown[]) => fetchWorkOrder(...a),
  fetchUnassignedLots: (...a: unknown[]) => fetchUnassignedLots(...a),
}));

import { MesProductionPage } from "./MesProductionPage";

const WO1 = workOrder({ status: "RUNNING" });
const WO2 = workOrder({ id: 2, wo_no: "WO-20261013-004", lot: "L-20261013-04", status: "DONE" });

beforeEach(() => {
  fetchWorkOrders.mockReset().mockResolvedValue([WO1, WO2]);
  fetchWorkOrder.mockReset().mockImplementation((id: number) =>
    Promise.resolve(
      id === 1
        ? { work_order: WO1, production: production() }
        : {
            work_order: WO2,
            production: production({
              stages: [],
              by_code: {},
              defect_total: 0,
              good_qty: 480,
              good_source: "crate",
              crate_count: 24,
              crate_tubes: 480,
              crate_mismatch: 2,
            }),
          },
    ),
  );
  fetchUnassignedLots.mockReset().mockResolvedValue([
    {
      lot: "LOT-TYPO-1",
      work_order: null,
      item_code: "HP12",
      production: production({ inspected_rows: 40, good_qty: 38, defect_total: 2 }),
    },
  ]);
  useAuthStore.getState().setAuth({ token: "t.t.t", username: "op", role: Role.OPERATOR });
});

describe("MesProductionPage", () => {
  it("전체 작업지시 목록(상태 필터 없이)과 작업지시 없는 LOT 을 보여 준다", async () => {
    renderApp(<MesProductionPage />);
    expect(await screen.findByTestId("prod-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("prod-row-2")).toBeInTheDocument();
    expect(fetchWorkOrders).toHaveBeenCalledWith();
    expect(screen.getByTestId("prod-hint")).toBeInTheDocument();
    const lot = await screen.findByTestId("unassigned-LOT-TYPO-1");
    expect(within(lot).getByText("40")).toBeInTheDocument();
    expect(screen.getByTestId("unassigned")).toHaveTextContent("자동으로 이어집니다");
  });

  it("작업지시를 고르면 단계표·양품 출처·불량유형이 나온다", async () => {
    const user = userEvent.setup();
    renderApp(<MesProductionPage />);
    await user.click(await screen.findByTestId("prod-select-1"));

    expect(fetchWorkOrder).toHaveBeenCalledWith(1);
    const table = await screen.findByTestId("stage-table");
    const len = within(table).getByTestId("stage-CUT_LENGTH");
    expect(within(len).getByText("길이 검사")).toBeInTheDocument();
    expect(within(len).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "길이 검사", "130", "6", "4", "3", "126",
    ]);
    expect(within(table).getByTestId("stage-POST_WASH_SURFACE")).toBeInTheDocument();

    expect(screen.getByTestId("tile-good")).toHaveTextContent("117");
    expect(screen.getByTestId("good-source")).toHaveTextContent("표면 검사 양품");
    expect(screen.getByTestId("defect-LEN")).toHaveTextContent("길이 4");
    expect(screen.getByTestId("defect-MANUAL")).toHaveTextContent("재확인 NG 1");
    expect(screen.queryByTestId("crate-info")).not.toBeInTheDocument();
    expect(screen.getByTestId("prod-select-1")).toHaveAttribute("aria-pressed", "true");
  });

  it("크레이트 개수 확인이 있으면 양품 출처는 크레이트, 판 수·불일치 판 표시", async () => {
    const user = userEvent.setup();
    renderApp(<MesProductionPage />);
    await user.click(await screen.findByTestId("prod-row-2"));
    expect(await screen.findByTestId("good-source")).toHaveTextContent("크레이트 개수 확인 합계");
    const crate = screen.getByTestId("crate-info");
    expect(crate).toHaveTextContent("24");
    expect(crate).toHaveTextContent("480");
    expect(crate).toHaveTextContent("⚠ 2");
    expect(screen.getByTestId("stage-empty")).toHaveTextContent("개수 확인만");
    expect(screen.getByTestId("defects-empty")).toBeInTheDocument();
  });

  it("작업지시·미지정 LOT 이 없으면 빈 상태", async () => {
    fetchWorkOrders.mockResolvedValue([]);
    fetchUnassignedLots.mockResolvedValue([]);
    renderApp(<MesProductionPage />);
    expect(await screen.findByTestId("prod-empty")).toHaveTextContent("작업지시가 없습니다");
    expect(await screen.findByTestId("unassigned-empty")).toBeInTheDocument();
  });

  it("MES 서버가 없으면 오류 상자", async () => {
    fetchWorkOrders.mockRejectedValue(new Error("MES 서버에 연결할 수 없습니다"));
    fetchUnassignedLots.mockRejectedValue(new Error("MES 서버에 연결할 수 없습니다"));
    renderApp(<MesProductionPage />);
    expect(await screen.findByTestId("prod-error")).toHaveTextContent(
      "작업지시 조회 실패: MES 서버에 연결할 수 없습니다",
    );
    expect(await screen.findByTestId("unassigned-error")).toBeInTheDocument();
  });

  it("상세 조회가 실패하면 그 구역에만 오류", async () => {
    fetchWorkOrder.mockRejectedValue(new Error("작업지시 없음"));
    const user = userEvent.setup();
    renderApp(<MesProductionPage />);
    await user.click(await screen.findByTestId("prod-select-1"));
    expect(await screen.findByTestId("prod-detail-error")).toHaveTextContent("작업지시 없음");
    expect(screen.getByTestId("prod-wo-table")).toBeInTheDocument();
  });
});
