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
    /** 어느 스테이션의 알람인가(서버가 실어 준다). 2대 구성에서 화면 필터용. */
    cam_id?: string | null;
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
  /** 지금 돌고 있는 검사 모드(InspectionStage 값). 첫 결과 전에도 헤더가 모드를 보여준다. */
  stage?: string | null;
  /** 센서 트리거 대기 중 — 제품이 아직 안 와서 찍지 않았다(미검출 아님). */
  waiting?: boolean;
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

/** 이벤트가 어느 스테이션에서 왔는가. 모르면 null(필터하지 않는다). */
export function eventCamId(e: LiveEvent): string | null {
  const v = (e.data as { cam_id?: string | null }).cam_id;
  return v ? String(v) : null;
}

/**
 * 이 화면이 받아야 할 이벤트인가(2대 구성).
 * camId 가 null 이면 전부 받는다(단일 구성·사무실 PC 전체 보기).
 * 이벤트에 cam_id 가 없으면 버리지 않는다 — 옛 서버/테스트 호환이고,
 * 모르는 것을 숨기는 쪽이 더 위험하다.
 */
export function matchesCam(e: LiveEvent, camId: string | null): boolean {
  if (!camId) return true;
  const c = eventCamId(e);
  return c === null || c === camId;
}
