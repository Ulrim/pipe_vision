/**
 * MES 설비가동 — 현재 상태(색 + 글자), 상태 전환(고장·점검은 사유 필수), 가동률, 이력.
 */
import { todayKst } from "@/lib/mesPlant";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Role } from "@aivis/shared-types";
import { renderApp } from "@/test/utils";
import { useAuthStore } from "@/store/auth";
import { equipment, equipmentEvent, utilization } from "@/test/mesPlantFixtures";

const api = vi.hoisted(() => ({
  fetchEquipment: vi.fn(),
  setEquipmentState: vi.fn(),
  fetchEquipmentEvents: vi.fn(),
  fetchUtilization: vi.fn(),
}));
vi.mock("@/api/mesPlant", () => api);
vi.mock("@/api/mes", () => ({ MES_UNREACHABLE: "MES 서버에 연결할 수 없습니다" }));

import { MesEquipmentPage } from "./MesEquipmentPage";

const twoHoursAgo = () => new Date(Date.now() - 2 * 3600_000 - 5 * 60_000).toISOString();

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.fetchEquipment.mockImplementation(() =>
    Promise.resolve([
      equipment({ state: "RUN", state_since: twoHoursAgo(), wo_id: 1, wo_no: "WO-20261014-001" }),
      equipment({ code: "WASH-1", name: "세척기", process_code: "WASH", state: "IDLE" }),
      equipment({ code: "DEB-1", name: "디버링기", state: "DOWN", state_reason: "모터 과열" }),
      equipment({ code: "PACK-1", name: "포장기", state: "PM", state_reason: "정기 점검" }),
      equipment({ code: "NEW-1", name: "신규 설비", state: null, state_since: null }),
      equipment({ code: "OLD-1", name: "폐기 설비", active: false }),
    ]),
  );
  api.setEquipmentState.mockImplementation((code: string, b: { state: string }) =>
    Promise.resolve(equipment({ code, name: code === "SAW-1" ? "절단기 1호" : "세척기", state: b.state as never })),
  );
  api.fetchEquipmentEvents.mockResolvedValue([
    equipmentEvent({ id: 2, state: "DOWN", reason: "톱날 파손", started_at: "2026-11-15T02:05:00Z", ended_at: null, wo_id: null }),
    equipmentEvent(),
  ]);
  api.fetchUtilization.mockResolvedValue(utilization());
  // 상태 전환은 작업자도 한다.
  useAuthStore.getState().setAuth({ token: "t.t.t", username: "kim", role: Role.OPERATOR });
});

describe("MesEquipmentPage — 설비 카드", () => {
  it("현재 상태를 기호 + 글자로, 경과 시간·작업지시·사유를 보여 준다", async () => {
    renderApp(<MesEquipmentPage />);
    await screen.findByTestId("eq-card-SAW-1");
    expect(screen.getByTestId("eq-state-SAW-1")).toHaveTextContent("▶가동");
    expect(screen.getByTestId("eq-state-WASH-1")).toHaveTextContent("‖대기");
    expect(screen.getByTestId("eq-state-DEB-1")).toHaveTextContent("✕고장");
    expect(screen.getByTestId("eq-state-PACK-1")).toHaveTextContent("◇점검");
    expect(screen.getByTestId("eq-state-NEW-1")).toHaveTextContent("기록 없음");
    expect(screen.getByTestId("eq-elapsed-SAW-1")).toHaveTextContent("2시간 5분째");
    expect(screen.getByTestId("eq-wo-SAW-1")).toHaveTextContent("WO-20261014-001");
    expect(screen.getByTestId("eq-card-DEB-1")).toHaveTextContent("모터 과열");
    // 사용 중지 설비는 카드에 없다.
    expect(screen.queryByTestId("eq-card-OLD-1")).not.toBeInTheDocument();
    // 지금 상태로 가는 버튼은 없다.
    expect(screen.queryByTestId("eq-set-SAW-1-RUN")).not.toBeInTheDocument();
    expect(screen.getByTestId("eq-set-SAW-1-IDLE")).toBeInTheDocument();
  });

  it("가동·대기는 바로 전환", async () => {
    const user = userEvent.setup();
    renderApp(<MesEquipmentPage />);
    await user.click(await screen.findByTestId("eq-set-WASH-1-RUN"));
    await waitFor(() =>
      expect(api.setEquipmentState).toHaveBeenCalledWith("WASH-1", { state: "RUN", reason: null }),
    );
    expect(await screen.findByTestId("eq-msg")).toHaveTextContent("가동(으)로 바꿨습니다");
  });

  it("고장은 사유를 넣어야 전환된다", async () => {
    const user = userEvent.setup();
    renderApp(<MesEquipmentPage />);
    await user.click(await screen.findByTestId("eq-set-SAW-1-DOWN"));
    expect(api.setEquipmentState).not.toHaveBeenCalled();
    const form = screen.getByTestId("eq-reason-form-SAW-1");
    expect(within(form).getByTestId("eq-reason-submit-SAW-1")).toBeDisabled();
    await user.type(within(form).getByLabelText("고장 사유(필수)"), "톱날 파손");
    await user.click(within(form).getByTestId("eq-reason-submit-SAW-1"));
    await waitFor(() =>
      expect(api.setEquipmentState).toHaveBeenCalledWith("SAW-1", { state: "DOWN", reason: "톱날 파손" }),
    );
    await waitFor(() => expect(screen.queryByTestId("eq-reason-form-SAW-1")).not.toBeInTheDocument());
  });

  it("점검도 사유 필수", async () => {
    const user = userEvent.setup();
    renderApp(<MesEquipmentPage />);
    await user.click(await screen.findByTestId("eq-set-WASH-1-PM"));
    expect(screen.getByLabelText("점검 사유(필수)")).toBeInTheDocument();
    expect(api.setEquipmentState).not.toHaveBeenCalled();
  });

  it("MES 서버가 없으면 오류 상자", async () => {
    api.fetchEquipment.mockRejectedValue(new Error("MES 서버에 연결할 수 없습니다"));
    renderApp(<MesEquipmentPage />);
    expect(await screen.findByTestId("eq-error")).toHaveTextContent(
      "설비 조회 실패: MES 서버에 연결할 수 없습니다",
    );
  });
});

describe("MesEquipmentPage — 가동률", () => {
  it("기본 기간은 오늘, 가동률·시간·고장 횟수·MTTR·MTBF", async () => {
    renderApp(<MesEquipmentPage />);
    const row = await screen.findByTestId("util-row-SAW-1");
    expect(api.fetchUtilization).toHaveBeenCalledWith(todayKst(), todayKst());
    expect(screen.getByTestId("util-pct-SAW-1")).toHaveTextContent("80%");
    const cells = within(row).getAllByRole("cell").map((c) => c.textContent);
    expect(cells.slice(2)).toEqual(["6시간", "1시간", "30분", "30분", "2", "15", "180"]);
    // 기록이 없으면 0% 가 아니라 빈칸.
    expect(screen.getByTestId("util-pct-WASH-1")).toHaveTextContent("-");
  });

  it("날짜를 바꾸면 그 기간으로 다시 조회, 거꾸로면 조회하지 않고 안내", async () => {
    renderApp(<MesEquipmentPage />);
    await screen.findByTestId("util-row-SAW-1");
    fireEvent.change(screen.getByTestId("util-from"), { target: { value: "2026-11-01" } });
    await waitFor(() => expect(api.fetchUtilization).toHaveBeenCalledWith("2026-11-01", todayKst()));
    const calls = api.fetchUtilization.mock.calls.length;
    fireEvent.change(screen.getByTestId("util-to"), { target: { value: "2026-10-01" } });
    expect(await screen.findByTestId("util-bad-range")).toHaveTextContent("종료일이 시작일보다 앞");
    expect(api.fetchUtilization.mock.calls.length).toBe(calls);
  });
});

describe("MesEquipmentPage — 상태 이력", () => {
  it("설비의 [이력] 을 누르면 그 설비 이력(진행 중 구간 표시)", async () => {
    const user = userEvent.setup();
    renderApp(<MesEquipmentPage />);
    expect(await screen.findByTestId("ev-hint")).toBeInTheDocument();
    expect(api.fetchEquipmentEvents).not.toHaveBeenCalled();
    await user.click(await screen.findByTestId("eq-history-SAW-1"));
    const open = await screen.findByTestId("ev-row-2");
    expect(api.fetchEquipmentEvents).toHaveBeenCalledWith("SAW-1", 50);
    expect(open).toHaveTextContent("✕고장");
    expect(open).toHaveTextContent("톱날 파손");
    expect(open).toHaveTextContent("진행 중");
    const closed = screen.getByTestId("ev-row-1");
    expect(closed).toHaveTextContent("▶가동");
    expect(closed).toHaveTextContent("2시간 5분");
    expect(closed).toHaveTextContent("#1");
  });
});
