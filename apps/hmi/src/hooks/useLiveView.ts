/**
 * 라이브 뷰 훅 (2026-10-20) — 설치할 때 카메라 위치·초점·노출·검사 영역 맞추기.
 *
 * 열면 켜고(POST start), 닫으면 끈다(POST stop). 그 사이 상태를 짧게 반복해 읽고(읽는 동안
 * 서버가 기한을 연장한다), 프레임 번호가 바뀔 때만 JPEG 을 다시 받는다. 화면이 닫혔는데 stop 이
 * 못 갔더라도(전원·네트워크) 서버 기한이 지나 워커가 멈춘다.
 *
 * 지표 해석(상용 카메라 설치 도구의 초점 값·노출 경고와 같은 방식):
 * - 초점 지수는 장면마다 절대값이 달라 **이번 세션 최고값 대비 %** 로 본다. 렌즈를 천천히
 *   돌리며 최고점을 지나쳤다가 되돌아와 90% 이상에서 고정한다.
 * - 포화(하얗게 날아간 화소)는 금속 반사에서 바로 판정을 흐리므로 1% 만 넘어도 알린다.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { LiveViewState } from "@aivis/shared-types";
import {
  fetchLiveFrame,
  fetchLiveState,
  requestCapture,
  startLive,
  stopLive,
} from "@/api/client";

export const LIVE_POLL_MS = 500;
/** 포화가 이 % 이상이면 노출·조명을 낮추라고 알린다. */
export const SATURATED_WARN_PCT = 1;
/** 암부가 이 % 이상이면 조명을 확인하라고 알린다. */
export const DARK_WARN_PCT = 25;
/** 초점 지수가 최고값의 이 비율 이상이면 '최고 근처'. */
export const FOCUS_GOOD_RATIO = 0.9;

export interface LiveAdvice {
  level: "info" | "warn";
  text: string;
}

/** 초점 지수 / 이번 세션 최고값(0~1). 아직 모르면 null. */
export function focusRatio(st: LiveViewState | null): number | null {
  if (!st || st.focus == null || !st.focus_peak) return null;
  return Math.max(0, Math.min(1, st.focus / st.focus_peak));
}

/** 지금 장면에 대한 안내(연결·노출·조명). 순서 = 먼저 손볼 것. */
export function liveAdvice(st: LiveViewState | null): LiveAdvice[] {
  if (!st || !st.requested) return [];
  const out: LiveAdvice[] = [];
  if (!st.streaming) {
    out.push(
      st.seq > 0 && st.frame_at
        ? { level: "warn", text: `영상 끊김 — 마지막 장면 ${Math.round(st.age_s ?? 0)}초 전` }
        : { level: "info", text: "카메라 연결 대기 중 — 검사기가 다음 상태 보고(최대 5초)에서 보내기 시작합니다" },
    );
    return out;
  }
  if ((st.saturated_pct ?? 0) >= SATURATED_WARN_PCT) {
    out.push({
      level: "warn",
      text: `반사로 하얗게 날아간 곳 ${st.saturated_pct?.toFixed(1)}% — 노출 또는 조명 밝기를 낮추세요`,
    });
  }
  if ((st.dark_pct ?? 0) >= DARK_WARN_PCT) {
    out.push({
      level: "warn",
      text: `검게 뭉개진 곳 ${st.dark_pct?.toFixed(0)}% — 조명이 검사 영역을 비추는지 확인하세요`,
    });
  }
  const b = st.brightness;
  if (b != null && b < 50) out.push({ level: "warn", text: `너무 어둡습니다(평균 밝기 ${Math.round(b)}/255)` });
  if (b != null && b > 210) out.push({ level: "warn", text: `너무 밝습니다(평균 밝기 ${Math.round(b)}/255)` });
  return out;
}

export interface UseLiveView {
  state: LiveViewState | null;
  /** 최신 프레임 objectURL. */
  frameUrl: string | null;
  error: string | null;
  /** 켜 둔 시간이 다 됐다(서버 상한) — restart 로 다시 켠다. */
  ended: boolean;
  /** 껐다 켜기 — 초점 최고값을 새로 잰다, 시간 종료 후 다시 보기. */
  restart: () => Promise<void>;
  capture: () => Promise<void>;
  /** 1장 촬영 요청이 막 접수됨(몇 초간 true). */
  captureSent: boolean;
}

export function useLiveView(camId: string | null, pollMs: number = LIVE_POLL_MS): UseLiveView {
  const [state, setState] = useState<LiveViewState | null>(null);
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState(false);
  const [captureSent, setCaptureSent] = useState(false);
  const seqRef = useRef(0);

  useEffect(() => {
    if (!camId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let url: string | null = null;

    const tick = async () => {
      try {
        const st = await fetchLiveState(camId);
        if (cancelled) return;
        setState(st);
        setError(null);
        if (st.seq > 0 && st.seq !== seqRef.current) {
          const blob = await fetchLiveFrame(camId);
          if (cancelled) return;
          if (blob) {
            const next = URL.createObjectURL(blob);
            if (url) URL.revokeObjectURL(url);
            url = next;
            seqRef.current = st.seq;
            setFrameUrl(next);
          }
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
      if (!cancelled) timer = setTimeout(tick, pollMs);
    };

    startLive(camId)
      .then((st) => {
        if (cancelled) return;
        setState(st);
        setStarted(true);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) void tick();
      });

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (url) URL.revokeObjectURL(url);
      stopLive(camId).catch(() => undefined);
    };
  }, [camId, pollMs]);

  const restart = useCallback(async () => {
    if (!camId) return;
    try {
      // 순서가 중요하다 — stop 이 start 보다 늦게 도착하면 켜자마자 꺼진다.
      await stopLive(camId);
      setState(await startLive(camId));
      setStarted(true);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [camId]);

  useEffect(() => {
    if (!captureSent) return;
    const id = setTimeout(() => setCaptureSent(false), 4000);
    return () => clearTimeout(id);
  }, [captureSent]);

  const capture = useCallback(async () => {
    if (!camId) return;
    try {
      await requestCapture(camId);
      setCaptureSent(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [camId]);

  const ended = started && state != null && !state.requested;
  return { state, frameUrl, error, ended, restart, capture, captureSent };
}
