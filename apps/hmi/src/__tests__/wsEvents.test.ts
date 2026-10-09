import { describe, expect, it } from "vitest";
import {
  parseLiveEvent,
  isInspectionEvent,
  isAlarmEvent,
  isStatusEvent,
  matchesCam,
  eventCamId,
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

describe("matchesCam (2대 구성: 다른 스테이션 이벤트는 이 화면 것이 아니다)", () => {
  const insp = (cam: string | null | undefined) =>
    ({ event: "inspection", data: makeResult({ id: 1, cam_id: cam as string }) }) as const;
  const alarm = (cam?: string) =>
    ({ event: "alarm", data: { id: 1, lot: "L", defect_codes: null, cam_id: cam } }) as const;
  const status = (cam: string) =>
    ({
      event: "status",
      data: {
        cam_id: cam, item_code: "HP12", expected: 1, detected: 1, ng: 0,
        mismatch: false, proc_time_ms: 1, ts: "t", error: null,
      },
    }) as const;

  it("화면이 카메라를 고정하지 않으면(null) 전부 받는다", () => {
    expect(matchesCam(insp("PI-CAM2"), null)).toBe(true);
    expect(matchesCam(status("PI-CAM2"), null)).toBe(true);
  });
  it("같은 카메라만 통과 — 결과·알람·하트비트 모두", () => {
    expect(matchesCam(insp("PI-CAM1"), "PI-CAM1")).toBe(true);
    expect(matchesCam(insp("PI-CAM2"), "PI-CAM1")).toBe(false);
    expect(matchesCam(alarm("PI-CAM2"), "PI-CAM1")).toBe(false);
    expect(matchesCam(status("PI-CAM2"), "PI-CAM1")).toBe(false);
    expect(matchesCam(status("PI-CAM1"), "PI-CAM1")).toBe(true);
  });
  it("cam_id 가 없는 이벤트는 버리지 않는다(옛 서버 호환 — 모르는 것을 숨기지 않는다)", () => {
    expect(eventCamId(alarm(undefined))).toBeNull();
    expect(matchesCam(alarm(undefined), "PI-CAM1")).toBe(true);
    expect(matchesCam(insp(null), "PI-CAM1")).toBe(true);
  });
});
