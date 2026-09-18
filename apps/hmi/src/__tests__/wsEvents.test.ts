import { describe, expect, it } from "vitest";
import {
  parseLiveEvent,
  isInspectionEvent,
  isAlarmEvent,
  isStatusEvent,
} from "@/types/ws";
import { makeResult } from "./factories";

describe("parseLiveEvent (WS 봉투 계약)", () => {
  it("inspection 이벤트를 파싱한다", () => {
    const raw = JSON.stringify({ event: "inspection", data: makeResult({ id: 1 }) });
    const evt = parseLiveEvent(raw);
    expect(evt).not.toBeNull();
    expect(evt && isInspectionEvent(evt)).toBe(true);
  });

  it("alarm 이벤트를 파싱한다(서버 hub.py 형식 {id,lot,defect_codes})", () => {
    const raw = JSON.stringify({
      event: "alarm",
      data: { id: 7, lot: "LOT-7", defect_codes: ["LEN"] },
    });
    const evt = parseLiveEvent(raw);
    expect(evt && isAlarmEvent(evt)).toBe(true);
  });

  it("status 이벤트(워커 하트비트)를 파싱한다", () => {
    const raw = JSON.stringify({
      event: "status",
      data: {
        cam_id: "CAM-1",
        item_code: "HP12",
        expected: 4,
        detected: 0,
        ng: 0,
        mismatch: true,
        proc_time_ms: 130,
        ts: "2026-07-20T10:00:00+09:00",
        error: null,
      },
    });
    const evt = parseLiveEvent(raw);
    expect(evt).not.toBeNull();
    expect(evt && isStatusEvent(evt)).toBe(true);
    expect(evt && isInspectionEvent(evt)).toBe(false);
    expect(evt && isAlarmEvent(evt)).toBe(false);
    if (evt && isStatusEvent(evt)) {
      expect(evt.data.detected).toBe(0);
      expect(evt.data.expected).toBe(4);
    }
  });

  it("형식 불일치/깨진 JSON 은 null 을 반환한다", () => {
    expect(parseLiveEvent("not json")).toBeNull();
    expect(parseLiveEvent(JSON.stringify({ event: "unknown" }))).toBeNull();
  });
});
