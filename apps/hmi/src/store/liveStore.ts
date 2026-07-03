/**
 * 전역 실시간 상태 (Zustand). CLAUDE.md §5 M6(알람), M10(작업자 UI).
 *
 * - WS /ws/live 로 들어온 검사결과를 최신순 목록으로 유지(마지막 상태 보존).
 * - 연결 상태(끊김/재연결) 인디케이터용 상태.
 * - 연속 NG 카운트 → 임계 초과 시 관리자 확인 요청 알람(M6 "연속 NG 발생 알림").
 *
 * 도메인 타입은 @aivis/shared-types 재사용(신규 정의 금지).
 */
import { create } from "zustand";
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";
import type { AlarmEvent } from "@/types/ws";

/** WS 연결 상태(인디케이터). */
export type ConnState = "connecting" | "open" | "reconnecting" | "closed";

/** 연속 NG 임계 — 이 횟수 이상 연속되면 관리자 확인 요청 알람(M6). */
export const CONSECUTIVE_NG_THRESHOLD = 3;

/** 목록 보존 최대 건수(현장 디스플레이 메모리 보호). */
const MAX_FEED = 100;

export function isNg(r: Pick<InspectionResult, "final_verdict">): boolean {
  return r.final_verdict === Verdict.NG;
}

export interface LiveState {
  /** 최신 검사결과(맨 앞이 최신). */
  feed: InspectionResult[];
  /** 가장 최근 검사결과(카드 강조). */
  latest: InspectionResult | null;
  /** WS 연결 상태. */
  conn: ConnState;
  /** 재연결 시도 횟수(인디케이터/디버그). */
  reconnectAttempts: number;
  /** 연속 NG 카운트(OK 수신 시 0 리셋). */
  consecutiveNg: number;
  /** 마지막 단건 알람(서버 alarm 이벤트). 미확인 시 배너 표시. */
  lastAlarm: AlarmEvent["data"] | null;
  /** 연속 NG 임계 초과 활성 여부(관리자 확인 시 해제). */
  consecutiveAlarmActive: boolean;
  /** 알람 소리 사용 토글. */
  soundEnabled: boolean;

  /** WS inspection 이벤트 수신. */
  pushInspection: (r: InspectionResult) => void;
  /** WS alarm 이벤트 수신(서버 NG 단건). */
  pushAlarm: (a: AlarmEvent["data"]) => void;
  /** 연결 상태 갱신. */
  setConn: (conn: ConnState, attempts?: number) => void;
  /** 단건 알람 배너 닫기(작업자 확인). */
  dismissAlarm: () => void;
  /** 연속 NG 알람 해제(관리자 확인). */
  acknowledgeConsecutive: () => void;
  /** 재확인(review) 응답으로 갱신된 행을 목록에 반영. */
  applyReview: (updated: InspectionResult) => void;
  /** 소리 토글. */
  toggleSound: () => void;
  /** 초기 적재(REST 조회 결과). 최신순 가정. */
  hydrate: (rows: InspectionResult[]) => void;
}

export const useLiveStore = create<LiveState>((set) => ({
  feed: [],
  latest: null,
  conn: "connecting",
  reconnectAttempts: 0,
  consecutiveNg: 0,
  lastAlarm: null,
  consecutiveAlarmActive: false,
  soundEnabled: true,

  pushInspection: (r) =>
    set((s) => {
      const feed = [r, ...s.feed].slice(0, MAX_FEED);
      const ng = isNg(r);
      const consecutiveNg = ng ? s.consecutiveNg + 1 : 0;
      const consecutiveAlarmActive =
        consecutiveNg >= CONSECUTIVE_NG_THRESHOLD
          ? true
          : ng
            ? s.consecutiveAlarmActive
            : false; // OK 수신 시 연속 알람도 자연 해제
      return { feed, latest: r, consecutiveNg, consecutiveAlarmActive };
    }),

  pushAlarm: (a) => set({ lastAlarm: a }),

  setConn: (conn, attempts) =>
    set((s) => ({
      conn,
      reconnectAttempts:
        attempts ?? (conn === "open" ? 0 : s.reconnectAttempts),
    })),

  dismissAlarm: () => set({ lastAlarm: null }),

  acknowledgeConsecutive: () =>
    set({ consecutiveAlarmActive: false, consecutiveNg: 0 }),

  applyReview: (updated) =>
    set((s) => ({
      feed: s.feed.map((r) => (r.id === updated.id ? updated : r)),
      latest:
        s.latest && s.latest.id === updated.id ? updated : s.latest,
    })),

  toggleSound: () => set((s) => ({ soundEnabled: !s.soundEnabled })),

  hydrate: (rows) =>
    set(() => ({
      feed: rows.slice(0, MAX_FEED),
      latest: rows[0] ?? null,
    })),
}));
