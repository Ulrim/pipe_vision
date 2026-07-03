/**
 * WebSocket /ws/live 이벤트 봉투 (services/api/ws/hub.py make_event,
 * docs/API.md: `{event, data}`, event = inspection|alarm).
 *
 * 이건 "전송 봉투" 타입일 뿐, 도메인 데이터(InspectionResult/DefectCode)는
 * @aivis/shared-types 를 재사용한다(신규 도메인 타입 정의 금지).
 */
import type { InspectionResult, DefectCode } from "@aivis/shared-types";

/** event=inspection: data 는 적재된 InspectionResult 전체(서버 POST /inspection 푸시). */
export interface InspectionEvent {
  event: "inspection";
  data: InspectionResult;
}

/** event=alarm: NG 발생 시 1건 = 1알람. (연속 NG 카운트는 클라이언트가 집계.) */
export interface AlarmEvent {
  event: "alarm";
  data: {
    id: number | null;
    lot: string;
    defect_codes: DefectCode[] | null;
  };
}

export type LiveEvent = InspectionEvent | AlarmEvent;

export function isInspectionEvent(e: LiveEvent): e is InspectionEvent {
  return e.event === "inspection";
}
export function isAlarmEvent(e: LiveEvent): e is AlarmEvent {
  return e.event === "alarm";
}

/** 봉투를 안전 파싱. 형식 불일치면 null. */
export function parseLiveEvent(raw: string): LiveEvent | null {
  try {
    const obj = JSON.parse(raw) as { event?: string; data?: unknown };
    if (obj.event === "inspection" && obj.data) {
      return { event: "inspection", data: obj.data as InspectionResult };
    }
    if (obj.event === "alarm" && obj.data) {
      return { event: "alarm", data: obj.data as AlarmEvent["data"] };
    }
    return null;
  } catch {
    return null;
  }
}
