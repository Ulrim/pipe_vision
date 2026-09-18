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

/**
 * event=status: 워커 라이브니스 하트비트(검사 이벤트가 0건이어도 주기 발행).
 * 워커가 살아있지만 튜브 미검출/취득 오류인 상황을 HMI 가 즉시 표시하기 위한 채널.
 * StatusData 는 HMI 로컬 표시 타입(도메인 타입 아님) — shared-types 에 두지 않는다.
 */
export interface StatusData {
  cam_id: string;
  item_code: string;
  expected: number;
  detected: number;
  ng: number;
  mismatch: boolean;
  proc_time_ms: number;
  /** ISO8601 워커 측 타임스탬프. */
  ts: string;
  error: string | null;
}

export interface StatusEvent {
  event: "status";
  data: StatusData;
}

export type LiveEvent = InspectionEvent | AlarmEvent | StatusEvent;

export function isInspectionEvent(e: LiveEvent): e is InspectionEvent {
  return e.event === "inspection";
}
export function isAlarmEvent(e: LiveEvent): e is AlarmEvent {
  return e.event === "alarm";
}
export function isStatusEvent(e: LiveEvent): e is StatusEvent {
  return e.event === "status";
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
    if (obj.event === "status" && obj.data) {
      return { event: "status", data: obj.data as StatusData };
    }
    return null;
  } catch {
    return null;
  }
}
