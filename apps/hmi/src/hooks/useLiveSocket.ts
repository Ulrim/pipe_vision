/**
 * WS /ws/live 구독 훅 (CLAUDE.md §5 M6/M10, 원칙: 끊김 시 자동 재연결).
 *
 * - 지수 백오프 재연결(1s→2s→4s…최대 30s, 지터).
 * - 끊겨도 store 의 마지막 상태는 유지(목록/최신 카드 보존).
 * - 연결 상태를 store 에 반영해 인디케이터로 표시.
 * - keepalive: 주기적으로 "ping" 텍스트 전송(서버 receive_text 루프 생존 감지).
 *
 * WebSocket 은 테스트에서 모킹한다(전역 WebSocket 주입 가능).
 */
import { useEffect, useRef } from "react";
import { WS_URL } from "@/lib/config";
import { parseLiveEvent, isInspectionEvent, isAlarmEvent } from "@/types/ws";
import { useLiveStore } from "@/store/liveStore";

const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30000;
const KEEPALIVE_MS = 25000;

function backoffDelay(attempt: number): number {
  const exp = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt);
  // 지터로 동시 재연결 폭주 방지(±20%).
  const jitter = exp * 0.2 * (Math.random() - 0.5) * 2;
  return Math.max(BASE_DELAY_MS, Math.round(exp + jitter));
}

export interface UseLiveSocketOptions {
  /** 테스트/주입용 URL 오버라이드. 기본 WS_URL. */
  url?: string;
}

export function useLiveSocket(opts: UseLiveSocketOptions = {}): void {
  const url = opts.url ?? WS_URL;
  const wsRef = useRef<WebSocket | null>(null);
  const attemptRef = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const keepaliveTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const closedByUser = useRef(false);

  useEffect(() => {
    closedByUser.current = false;
    const store = useLiveStore.getState();

    const clearTimers = () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (keepaliveTimer.current) clearInterval(keepaliveTimer.current);
      reconnectTimer.current = null;
      keepaliveTimer.current = null;
    };

    const scheduleReconnect = () => {
      if (closedByUser.current) return;
      const delay = backoffDelay(attemptRef.current);
      attemptRef.current += 1;
      useLiveStore
        .getState()
        .setConn("reconnecting", attemptRef.current);
      reconnectTimer.current = setTimeout(connect, delay);
    };

    function connect() {
      useLiveStore
        .getState()
        .setConn(
          attemptRef.current === 0 ? "connecting" : "reconnecting",
          attemptRef.current,
        );
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch {
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        useLiveStore.getState().setConn("open", 0);
        if (keepaliveTimer.current) clearInterval(keepaliveTimer.current);
        keepaliveTimer.current = setInterval(() => {
          try {
            if (ws.readyState === WebSocket.OPEN) ws.send("ping");
          } catch {
            /* ignore */
          }
        }, KEEPALIVE_MS);
      };

      ws.onmessage = (ev: MessageEvent) => {
        const evt = parseLiveEvent(
          typeof ev.data === "string" ? ev.data : String(ev.data),
        );
        if (!evt) return;
        const s = useLiveStore.getState();
        if (isInspectionEvent(evt)) s.pushInspection(evt.data);
        else if (isAlarmEvent(evt)) s.pushAlarm(evt.data);
      };

      ws.onerror = () => {
        // onclose 가 뒤따른다 — 거기서 재연결 스케줄.
      };

      ws.onclose = () => {
        if (keepaliveTimer.current) {
          clearInterval(keepaliveTimer.current);
          keepaliveTimer.current = null;
        }
        if (closedByUser.current) {
          useLiveStore.getState().setConn("closed");
          return;
        }
        scheduleReconnect();
      };
    }

    store.setConn("connecting", 0);
    connect();

    return () => {
      closedByUser.current = true;
      clearTimers();
      const ws = wsRef.current;
      if (ws) {
        ws.onclose = null;
        ws.onerror = null;
        ws.onmessage = null;
        ws.onopen = null;
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
      useLiveStore.getState().setConn("closed");
    };
    // url 변경 시에만 재구독.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);
}
