import { describe, expect, it } from "vitest";
import {
  parseLiveEvent,
  isInspectionEvent,
  isAlarmEvent,
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

  it("형식 불일치/깨진 JSON 은 null 을 반환한다", () => {
    expect(parseLiveEvent("not json")).toBeNull();
    expect(parseLiveEvent(JSON.stringify({ event: "unknown" }))).toBeNull();
  });
});
