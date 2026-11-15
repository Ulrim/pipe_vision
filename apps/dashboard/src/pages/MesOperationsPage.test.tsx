/**
 * MES 공정진행 — 공정 시작·완료·건너뛰기, 자재 소요·투입(409 → 재고 부족 강행).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Role } from "@aivis/shared-types";
import { renderApp } from "@/test/utils";
import { useAuthStore } from "@/store/auth";
import { workOrder } from "@/test/mesFixtures";
import { equipment, materialLine, materials, operation } from "@/test/mesPlantFixtures";

const api = vi.hoisted(() => ({
  fetchOperations: vi.fn(),
  startOperation: vi.fn(),
  completeOperation: vi.fn(),
  skipOperation: vi.fn(),
  fetchMaterials: vi.fn(),
  issueMaterials: vi.fn(),
  fetchEquipment: vi.fn(),
}));
vi.mock("@/api/mesPlant", () => api);

const fetchWorkOrders = vi.fn();
vi.mock("@/api/mes", () => ({
  MES_UNREACHABLE: "MES 서버에 연결할 수 없습니다",
  fetchWorkOrders: (...a: unknown[]) => fetchWorkOrders(...a),
}));

import { ApiError } from "@/api/client";
import { MesOperationsPage } from "./MesOperationsPage";

const OPS = [
  operation({ id: 11, seq: 10, equipment_code: "SAW-1" }),
  operation({
    id: 12, seq: 20, process_code: "WASH", process_name: "세척", status: "RUNNING",
    started_at: "2026-11-15T01:00:00Z", worker: "lee",
  }),
  operation({
    id: 13, seq: 30, process_code: "INSP", process_name: "검사", status: "RUNNING",
    auto: true, auto_good_qty: 480, auto_defect_qty: 6,
  }),
  operation({
    id: 14, seq: 40, process_code: "PACK", process_name: "포장", status: "DONE",
    good_qty: 470, defect_qty: 1, ended_at: "2026-11-15T03:00:00Z",
  }),
];

function login(role: Role): void {
  useAuthStore.getState().setAuth({ token: "t.t.t", username: "kim", role });
}

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  fetchWorkOrders.mockReset().mockResolvedValue([
    workOrder(),
    workOrder({ id: 2, wo_no: "WO-20261014-002", lot: "L-20261014-02", status: "RUNNING" }),
  ]);
  api.fetchOperations.mockResolvedValue(OPS);
  api.startOperation.mockImplementation((_wo: number, id: number, b: object) =>
    Promise.resolve({ ...OPS.find((o) => o.id === id), status: "RUNNING", ...b }),
  );
  api.completeOperation.mockImplementation((_wo: number, id: number) =>
    Promise.resolve({ ...OPS.find((o) => o.id === id), status: "DONE" }),
  );
  api.skipOperation.mockImplementation((_wo: number, id: number) =>
    Promise.resolve({ ...OPS.find((o) => o.id === id), status: "SKIPPED" }),
  );
  api.fetchMaterials.mockResolvedValue(
    materials({
      lines: [
        materialLine(),
        materialLine({ item_code: "BOX", name: "포장 박스", unit: "EA", required: 50, on_hand: 20, shortage: 30 }),
      ],
    }),
  );
  api.issueMaterials.mockResolvedValue(
    materials({ lines: [materialLine({ issued: 127.5, on_hand: 72.5 })] }),
  );
  api.fetchEquipment.mockResolvedValue([
    equipment(),
    equipment({ code: "WASH-1", name: "세척기", process_code: "WASH" }),
    equipment({ code: "OLD-1", name: "폐기 설비", active: false }),
  ]);
  login(Role.QUALITY);
});

describe("MesOperationsPage — 작업지시 선택", () => {
  it("진행 중 작업지시만 조회하고, 고르기 전에는 안내", async () => {
    renderApp(<MesOperationsPage />);
    expect(await screen.findByTestId("ops-hint")).toBeInTheDocument();
    expect(fetchWorkOrders).toHaveBeenCalledWith("PLANNED,RUNNING");
    expect(api.fetchOperations).not.toHaveBeenCalled();
  });

  it("고르면 그 작업지시의 공정·자재를 읽는다", async () => {
    const user = userEvent.setup();
    renderApp(<MesOperationsPage />);
    await waitFor(() =>
      expect(within(screen.getByTestId("ops-wo")).getByRole("option", { name: /WO-20261014-002/ })).toBeInTheDocument(),
    );
    await user.selectOptions(screen.getByTestId("ops-wo"), "2");
    await screen.findByTestId("op-card-11");
    expect(api.fetchOperations).toHaveBeenCalledWith(2);
    expect(api.fetchMaterials).toHaveBeenCalledWith(2);
  });

  it("URL ?wo=<id> 로도 선택된다(목록에 없는 완료 지시도)", async () => {
    renderApp(<MesOperationsPage />, "/mes/operations?wo=9");
    await screen.findByTestId("op-card-11");
    expect(api.fetchOperations).toHaveBeenCalledWith(9);
    expect(screen.getByTestId("ops-wo")).toHaveValue("9");
  });
});

describe("MesOperationsPage — 공정 카드", () => {
  it("순서·상태(기호+글자)·수량을 보여 주고, 검사 공정은 검사기 집계", async () => {
    renderApp(<MesOperationsPage />, "/mes/operations?wo=1");
    await screen.findByTestId("op-card-11");
    expect(screen.getByTestId("ops-wo-summary")).toHaveTextContent("WO-20261014-001");
    expect(screen.getByTestId("op-status-11")).toHaveTextContent("○대기");
    expect(screen.getByTestId("op-status-12")).toHaveTextContent("▶진행 중");
    expect(screen.getByTestId("op-status-14")).toHaveTextContent("✓완료");
    expect(screen.getByTestId("op-qty-14")).toHaveTextContent("양품 470 · 불량 1");
    expect(screen.getByTestId("op-auto-13")).toHaveTextContent("양품 480 · 불량 6");
    // 끝난 공정에는 버튼이 없다.
    expect(screen.queryByTestId("op-start-14")).not.toBeInTheDocument();
    expect(screen.queryByTestId("op-skip-14")).not.toBeInTheDocument();
  });

  it("시작: 설비(라우팅 기본값)·작업자(로그인 사용자) → POST start", async () => {
    const user = userEvent.setup();
    renderApp(<MesOperationsPage />, "/mes/operations?wo=1");
    await user.click(await screen.findByTestId("op-start-11"));
    const form = screen.getByTestId("op-start-form-11");
    const eq = within(form).getByLabelText("설비(선택)");
    await waitFor(() => expect(within(eq).getByRole("option", { name: /절단기 1호/ })).toBeInTheDocument());
    expect(eq).toHaveValue("SAW-1");
    expect(within(eq).queryByRole("option", { name: /폐기 설비/ })).not.toBeInTheDocument();
    expect(within(form).getByLabelText("작업자(선택)")).toHaveValue("kim");
    await user.click(within(form).getByTestId("op-start-submit-11"));
    await waitFor(() =>
      expect(api.startOperation).toHaveBeenCalledWith(1, 11, { equipment_code: "SAW-1", worker: "kim" }),
    );
    expect(await screen.findByTestId("ops-msg")).toHaveTextContent("절단 공정을 시작했습니다");
  });

  it("완료: 양품을 넣어야 보낼 수 있고, 양품·불량 → POST complete", async () => {
    const user = userEvent.setup();
    renderApp(<MesOperationsPage />, "/mes/operations?wo=1");
    await user.click(await screen.findByTestId("op-complete-12"));
    const form = screen.getByTestId("op-complete-form-12");
    expect(within(form).getByTestId("op-complete-submit-12")).toBeDisabled();
    await user.type(within(form).getByLabelText("양품"), "495");
    await user.clear(within(form).getByLabelText("불량"));
    await user.type(within(form).getByLabelText("불량"), "5");
    await user.click(within(form).getByTestId("op-complete-submit-12"));
    await waitFor(() =>
      expect(api.completeOperation).toHaveBeenCalledWith(1, 12, { good_qty: 495, defect_qty: 5, note: null }),
    );
  });

  it("검사 공정 완료: 입력 없이 검사기 집계를 보여 주고 수량을 보내지 않는다", async () => {
    const user = userEvent.setup();
    renderApp(<MesOperationsPage />, "/mes/operations?wo=1");
    await user.click(await screen.findByTestId("op-complete-13"));
    const form = screen.getByTestId("op-complete-form-13");
    expect(within(form).getByTestId("op-complete-auto-13")).toHaveTextContent(
      "검사기 집계 — 양품 480 · 불량 6",
    );
    expect(within(form).queryByLabelText("양품")).not.toBeInTheDocument();
    await user.click(within(form).getByTestId("op-complete-submit-13"));
    await waitFor(() => expect(api.completeOperation).toHaveBeenCalledWith(1, 13, { note: null }));
  });

  it("건너뛰기: 한 번 더 확인해야 실행", async () => {
    const user = userEvent.setup();
    renderApp(<MesOperationsPage />, "/mes/operations?wo=1");
    await user.click(await screen.findByTestId("op-skip-11"));
    expect(api.skipOperation).not.toHaveBeenCalled();
    await user.click(screen.getByTestId("op-skip-confirm-11"));
    await waitFor(() => expect(api.skipOperation).toHaveBeenCalledWith(1, 11));
  });

  it("작업자에게는 건너뛰기가 안 보이고, 시작·완료는 보인다", async () => {
    login(Role.OPERATOR);
    renderApp(<MesOperationsPage />, "/mes/operations?wo=1");
    await screen.findByTestId("op-card-11");
    expect(screen.getByTestId("op-start-11")).toBeInTheDocument();
    expect(screen.getByTestId("op-complete-12")).toBeInTheDocument();
    expect(screen.queryByTestId("op-skip-11")).not.toBeInTheDocument();
    expect(screen.queryByTestId("op-skip-12")).not.toBeInTheDocument();
  });

  it("MES 서버가 없으면 오류 상자", async () => {
    api.fetchOperations.mockRejectedValue(new Error("MES 서버에 연결할 수 없습니다"));
    renderApp(<MesOperationsPage />, "/mes/operations?wo=1");
    expect(await screen.findByTestId("ops-error")).toHaveTextContent(
      "공정 조회 실패: MES 서버에 연결할 수 없습니다",
    );
  });
});

describe("MesOperationsPage — 자재 소요·투입", () => {
  it("필요·투입·현재고·부족을 보여 주고, 부족은 기호 + 글자로 경고", async () => {
    renderApp(<MesOperationsPage />, "/mes/operations?wo=1");
    const row = await screen.findByTestId("mat-row-AL-TUBE");
    expect(within(row).getByText("127.5")).toBeInTheDocument();
    expect(within(row).getByText("200")).toBeInTheDocument();
    expect(screen.getByTestId("mat-short-AL-TUBE")).toHaveTextContent("-");
    expect(screen.getByTestId("mat-short-BOX")).toHaveTextContent("⚠ 부족 30");
    expect(screen.getByTestId("mat-row-BOX").className).toContain("bg-amber-50");
    expect(screen.getByTestId("mat-short-count")).toHaveTextContent("재고 부족 1품목");
  });

  it("자재 투입 → 남은 필요량 전부(allow_negative false), 응답으로 표 갱신", async () => {
    const user = userEvent.setup();
    renderApp(<MesOperationsPage />, "/mes/operations?wo=1");
    await user.click(await screen.findByTestId("mat-issue"));
    await waitFor(() => expect(api.issueMaterials).toHaveBeenCalledWith(1, { allow_negative: false }));
    expect(await screen.findByTestId("mat-msg")).toHaveTextContent("남은 필요량을 모두 투입했습니다");
    expect(screen.queryByTestId("mat-issue-force")).not.toBeInTheDocument();
  });

  it("409(재고 부족)면 안내와 함께 '재고가 모자라도 투입' 으로 다시 보낸다", async () => {
    api.issueMaterials.mockRejectedValueOnce(new ApiError(409, "재고 부족: BOX 30"));
    const user = userEvent.setup();
    renderApp(<MesOperationsPage />, "/mes/operations?wo=1");
    await user.click(await screen.findByTestId("mat-issue"));
    expect(await screen.findByTestId("mat-msg")).toHaveTextContent(
      "재고가 모자라 투입하지 않았습니다: 재고 부족: BOX 30",
    );
    const force = await screen.findByTestId("mat-issue-force");
    expect(force).toHaveTextContent("재고가 모자라도 투입");
    await user.click(force);
    await waitFor(() => expect(api.issueMaterials).toHaveBeenLastCalledWith(1, { allow_negative: true }));
    await waitFor(() => expect(screen.queryByTestId("mat-short-box")).not.toBeInTheDocument());
    expect(screen.getByTestId("mat-msg")).toHaveTextContent("재고가 모자란 채로");
  });

  it("409 가 아닌 실패에는 강행 버튼을 내지 않는다", async () => {
    api.issueMaterials.mockRejectedValue(new ApiError(500, "서버 오류"));
    const user = userEvent.setup();
    renderApp(<MesOperationsPage />, "/mes/operations?wo=1");
    await user.click(await screen.findByTestId("mat-issue"));
    expect(await screen.findByTestId("mat-msg")).toHaveTextContent("자재 투입 실패: 서버 오류");
    expect(screen.queryByTestId("mat-issue-force")).not.toBeInTheDocument();
  });

  it("BOM 이 없으면 안내만(투입 버튼 없음)", async () => {
    api.fetchMaterials.mockResolvedValue(materials({ lines: [] }));
    renderApp(<MesOperationsPage />, "/mes/operations?wo=1");
    expect(await screen.findByTestId("mat-empty")).toHaveTextContent("BOM 이 없어");
    expect(screen.queryByTestId("mat-issue")).not.toBeInTheDocument();
  });
});
