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
import { WS_URL, withWsToken } from "@/lib/config";
import { parseLiveEvent, isInspectionEvent, isAlarmEvent } from "@/types/ws";
import { useLiveStore } from "@/store/liveStore";
import { useAuthStore } from "@/store/authStore";

const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30000;
const KEEPALIVE_MS = 25000;

/**
 * WS close code 1008 = Policy Violation. 백엔드는 토큰 검증 실패 시 1008 로 닫는다.
 * 이 경우 토큰이 무효/만료된 것이므로 무한 재연결 폭주를 막고 로그인 화면으로 복귀시킨다.
 */
const WS_AUTH_FAILED_CODE = 1008;

function backoffDelay(attempt: number): number {
  const exp = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt);
  // 지터로 동시 재연결 폭주 방지(±20%).
  const jitter = exp * 0.2 * (Math.random() - 0.5) * 2;
  return Math.max(BASE_DELAY_MS, Math.round(exp + jitter));
}

export interface UseLiveSocketOptions {
  /**
   * 테스트/주입용 URL 오버라이드. 기본 WS_URL.
   * 지정 시 토큰 부착 없이 그대로 연결한다(테스트가 URL 을 완전 제어).
   */
  url?: string;
}

export function useLiveSocket(opts: UseLiveSocketOptions = {}): void {
  const overrideUrl = opts.url;
  // 토큰이 바뀌면(로그인/로그아웃) 재구독해 새 토큰으로 연결한다.
  const token = useAuthStore((s) => s.token());
  const wsRef = useRef<WebSocket | null>(null);
  const attemptRef = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const keepaliveTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const closedByUser = useRef(false);

  useEffect(() => {
    closedByUser.current = false;
    const store = useLiveStore.getState();

    /**
     * 매 (재)연결 시 현재 토큰으로 URL 을 구성한다.
     * - override URL 이 있으면(테스트) 그대로 사용.
     * - 그 외에는 authStore 의 현재 토큰을 ?token= 으로 부착.
     */
    const resolveUrl = (): string | null => {
      if (overrideUrl) return overrideUrl;
      const current = useAuthStore.getState().token();
      if (!current) return null; // 토큰 없으면 연결 시도 안 함(게이트로 차단됨)
      return withWsToken(WS_URL, current);
    };

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
      const target = resolveUrl();
      if (!target) {
        // 토큰이 없어 연결할 수 없음 → 폭주 방지 위해 closed 로 두고 멈춘다.
        // (App 게이트가 로그인 화면을 띄워 사용자가 인증하면 토큰 변경으로 재구독.)
        useLiveStore.getState().setConn("closed");
        return;
      }
      useLiveStore
        .getState()
        .setConn(
          attemptRef.current === 0 ? "connecting" : "reconnecting",
          attemptRef.current,
        );
      let ws: WebSocket;
      try {
        ws = new WebSocket(target);
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

      ws.onclose = (ev: CloseEvent) => {
        if (keepaliveTimer.current) {
          clearInterval(keepaliveTimer.current);
          keepaliveTimer.current = null;
        }
        if (closedByUser.current) {
          useLiveStore.getState().setConn("closed");
          return;
        }
        // 인증 실패(1008): 토큰 무효/만료 → 재연결 폭주 금지, 로그인 화면 복귀.
        if (ev?.code === WS_AUTH_FAILED_CODE) {
          closedByUser.current = true; // 이 인스턴스의 후속 재연결 차단
          clearTimers();
          useLiveStore.getState().setConn("closed");
          // 세션 폐기 → App 게이트가 로그인 화면을 띄운다(기존 401 처리와 동일 복귀).
          useAuthStore.getState().logout();
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
    // override URL 또는 토큰(로그인/로그아웃) 변경 시 재구독.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overrideUrl, token]);
}
