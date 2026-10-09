/**
 * 2대 구성 — 화면이 한 카메라에 고정되면(?cam=) 다른 스테이션의 결과·하트비트·
 * 알람은 store 에 들어가지 않는다. 길이 라인 화면이 크레이트 개수 결과로
 * 깜빡이면 작업자는 어느 판정이 자기 것인지 모른다.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { useLiveSocket } from "@/hooks/useLiveSocket";
import { useLiveStore } from "@/store/liveStore";
import { MockWebSocket } from "./mockWebSocket";
import { makeResult } from "./factories";

function Probe({ camId }: { camId: string | null }) {
  useLiveSocket({ url: "ws://test/ws/live", camId });
  return null;
}

const status = (cam: string, detected: number) => ({
  event: "status",
  data: {
    cam_id: cam, item_code: "HP12", expected: 20, detected, ng: 0,
    mismatch: false, proc_time_ms: 10, ts: new Date().toISOString(), error: null,
    stage: cam === "PI-CAM1" ? "CUT_LENGTH" : "CRATE_COUNT",
  },
});

beforeEach(() => {
  useLiveStore.setState({ feed: [], latest: null, status: null, statusAt: null,
    lastAlarm: null, soundEnabled: false });
  MockWebSocket.reset();
  vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useLiveSocket — 카메라 고정", () => {
  it("고정된 카메라의 이벤트만 store 에 들어간다", () => {
    render(<Probe camId="PI-CAM1" />);
    const ws = MockWebSocket.last();
    ws.triggerOpen();
    ws.triggerMessage({ event: "inspection", data: makeResult({ id: 1, cam_id: "PI-CAM2" }) });
    ws.triggerMessage(status("PI-CAM2", 18));
    ws.triggerMessage({ event: "alarm", data: { id: 1, lot: "L", defect_codes: ["COUNT"], cam_id: "PI-CAM2" } });
    expect(useLiveStore.getState().latest).toBeNull();
    expect(useLiveStore.getState().status).toBeNull();
    expect(useLiveStore.getState().lastAlarm).toBeNull();

    ws.triggerMessage({ event: "inspection", data: makeResult({ id: 2, cam_id: "PI-CAM1" }) });
    ws.triggerMessage(status("PI-CAM1", 20));
    expect(useLiveStore.getState().latest?.id).toBe(2);
    expect(useLiveStore.getState().status?.cam_id).toBe("PI-CAM1");
    expect(useLiveStore.getState().status?.stage).toBe("CUT_LENGTH");
  });

  it("고정이 없으면(null) 두 스테이션 모두 받는다 — 단일 구성과 같다", () => {
    render(<Probe camId={null} />);
    const ws = MockWebSocket.last();
    ws.triggerOpen();
    ws.triggerMessage({ event: "inspection", data: makeResult({ id: 1, cam_id: "PI-CAM2" }) });
    expect(useLiveStore.getState().latest?.id).toBe(1);
    ws.triggerMessage({ event: "inspection", data: makeResult({ id: 2, cam_id: "PI-CAM1" }) });
    expect(useLiveStore.getState().latest?.id).toBe(2);
  });
});
