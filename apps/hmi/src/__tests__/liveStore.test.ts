import { beforeEach, describe, expect, it } from "vitest";
import {
  useLiveStore,
  CONSECUTIVE_NG_THRESHOLD,
} from "@/store/liveStore";
import { DefectCode } from "@aivis/shared-types";
import { makeResult, makeNg } from "./factories";

function reset() {
  useLiveStore.setState({
    feed: [],
    latest: null,
    conn: "connecting",
    reconnectAttempts: 0,
    consecutiveNg: 0,
    lastAlarm: null,
    consecutiveAlarmActive: false,
    soundEnabled: true,
    status: null,
    statusAt: null,
  });
}

describe("liveStore", () => {
  beforeEach(reset);

  it("pushInspection 가 최신 카드와 목록 맨 앞을 갱신한다", () => {
    const r = makeResult({ id: 1 });
    useLiveStore.getState().pushInspection(r);
    expect(useLiveStore.getState().latest?.id).toBe(1);
    expect(useLiveStore.getState().feed[0].id).toBe(1);
  });

  it("연속 NG 임계 도달 시 consecutiveAlarmActive 가 켜진다", () => {
    for (let i = 0; i < CONSECUTIVE_NG_THRESHOLD; i++) {
      useLiveStore.getState().pushInspection(makeNg({ id: 100 + i }));
    }
    expect(useLiveStore.getState().consecutiveNg).toBe(
      CONSECUTIVE_NG_THRESHOLD,
    );
    expect(useLiveStore.getState().consecutiveAlarmActive).toBe(true);
  });

  it("OK 수신 시 연속 NG 카운트와 연속 알람이 리셋된다", () => {
    for (let i = 0; i < CONSECUTIVE_NG_THRESHOLD; i++) {
      useLiveStore.getState().pushInspection(makeNg({ id: 200 + i }));
    }
    useLiveStore.getState().pushInspection(makeResult({ id: 999 }));
    expect(useLiveStore.getState().consecutiveNg).toBe(0);
    expect(useLiveStore.getState().consecutiveAlarmActive).toBe(false);
  });

  it("acknowledgeConsecutive 가 연속 알람을 해제한다", () => {
    for (let i = 0; i < CONSECUTIVE_NG_THRESHOLD; i++) {
      useLiveStore.getState().pushInspection(makeNg({ id: 300 + i }));
    }
    useLiveStore.getState().acknowledgeConsecutive();
    expect(useLiveStore.getState().consecutiveAlarmActive).toBe(false);
    expect(useLiveStore.getState().consecutiveNg).toBe(0);
  });

  it("pushAlarm/dismissAlarm 가 단건 알람을 토글한다", () => {
    useLiveStore.getState().pushAlarm({
      id: 5,
      lot: "LOT-9",
      defect_codes: [DefectCode.SCR],
    });
    expect(useLiveStore.getState().lastAlarm?.lot).toBe("LOT-9");
    useLiveStore.getState().dismissAlarm();
    expect(useLiveStore.getState().lastAlarm).toBeNull();
  });

  it("applyReview 가 목록/최신 카드의 해당 행을 갱신한다", () => {
    const r = makeNg({ id: 42 });
    useLiveStore.getState().pushInspection(r);
    const updated = { ...r, manual_verdict: r.final_verdict, review_flag: false };
    useLiveStore.getState().applyReview(updated);
    expect(useLiveStore.getState().feed[0].review_flag).toBe(false);
    expect(useLiveStore.getState().latest?.manual_verdict).toBe("NG");
  });

  it("pushStatus 가 워커 하트비트(status)와 수신시각(statusAt)을 갱신한다", () => {
    expect(useLiveStore.getState().status).toBeNull();
    expect(useLiveStore.getState().statusAt).toBeNull();
    const before = Date.now();
    useLiveStore.getState().pushStatus({
      cam_id: "CAM-1",
      item_code: "HP12",
      expected: 4,
      detected: 0,
      ng: 0,
      mismatch: true,
      proc_time_ms: 120,
      ts: "2026-07-20T10:00:00+09:00",
      error: null,
    });
    const st = useLiveStore.getState();
    expect(st.status?.cam_id).toBe("CAM-1");
    expect(st.status?.detected).toBe(0);
    expect(st.status?.expected).toBe(4);
    expect(st.statusAt).not.toBeNull();
    expect(st.statusAt!).toBeGreaterThanOrEqual(before);
  });

  it("setConn open 시 재연결 시도 횟수가 0으로 리셋된다", () => {
    useLiveStore.getState().setConn("reconnecting", 4);
    expect(useLiveStore.getState().reconnectAttempts).toBe(4);
    useLiveStore.getState().setConn("open", 0);
    expect(useLiveStore.getState().conn).toBe("open");
    expect(useLiveStore.getState().reconnectAttempts).toBe(0);
  });
});
