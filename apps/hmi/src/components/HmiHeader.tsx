/**
 * 현장 HMI 상단 바 (CLAUDE.md §5 M10).
 *
 * 파이 7인치 LCD(800x480)에서 화면 높이의 대부분은 **판정**이 써야 한다.
 * 이전 헤더는 로고·연결배지·계정·상태스트립·소리버튼이 세로로 쌓여 화면의
 * 48% 를 먹었고, 정작 작업자가 볼 OK/NG 는 접힘선 아래로 밀려 스크롤해야
 * 보였다. 그래서 상단 바를 **한 줄(약 52px)** 로 압축한다:
 *
 *   [ 품목 · LOT ]            [ 검출 n/m ] [ 워커/연결 상태 ] [ 시각 ] [ ⋯ ]
 *
 * 표기 원칙: 상태는 색 단독이 아니라 기호+한국어를 함께 쓴다(색약 고려).
 * 로그아웃 등 관리 동작은 평소 숨기고 ⋯ 버튼 뒤에 둔다 — 현장에서 실수로
 * 누르면 검사 화면이 사라지기 때문이다.
 */
import { useEffect, useState } from "react";
import { useLiveStore } from "@/store/liveStore";
import { useAuthStore } from "@/store/authStore";

/** 이 시간(ms) 넘게 하트비트가 없으면 워커가 멈춘 것으로 본다. */
const STALE_MS = 6000;

/** 워커 하트비트/연결 상태 → 한 줄 표기(기호+문자+색). */
function useLiveHealth() {
  const conn = useLiveStore((s) => s.conn);
  const status = useLiveStore((s) => s.status);
  const statusAt = useLiveStore((s) => s.statusAt);

  // 무신호 전환을 감지하되 **매초 리렌더하지 않는다**: 정확히 만료 시점
  // 한 번만 깨운다. 이 화면은 파이에서 24시간 켜져 있어서, 1초마다 헤더를
  // 다시 그리면 종일 헛된 렌더가 쌓인다(약한 CPU 에서 특히 아깝다).
  const [staleAt, setStaleAt] = useState<number | null>(null);
  useEffect(() => {
    setStaleAt(null);
    if (statusAt == null) return;
    const remain = statusAt + STALE_MS - Date.now();
    if (remain <= 0) {
      setStaleAt(statusAt);
      return;
    }
    const id = setTimeout(() => setStaleAt(statusAt), remain);
    return () => clearTimeout(id);
  }, [statusAt]);

  if (conn !== "open") {
    return { mark: "✕", text: "연결 끊김", tone: "bad" as const };
  }
  if (statusAt == null) {
    return { mark: "•", text: "대기 중", tone: "idle" as const };
  }
  if (staleAt === statusAt) {
    return { mark: "!", text: "워커 응답 없음", tone: "bad" as const };
  }
  if (status?.error) {
    return { mark: "!", text: "취득 오류", tone: "bad" as const };
  }
  if (status && status.detected === 0) {
    return { mark: "!", text: "미검출", tone: "warn" as const };
  }
  return { mark: "✓", text: "검사 중", tone: "ok" as const };
}

// 고성능 HMI 규칙: 정상(검사 중)은 무채색 — 색은 이상 상태 전용.
const TONE = {
  ok: "bg-white text-gray-600 border-gray-300",
  warn: "bg-amber-100 text-amber-900 border-amber-500",
  bad: "bg-ng-bg text-ng-fg border-ng",
  idle: "bg-gray-100 text-gray-600 border-gray-300",
} as const;

export function HmiHeader() {
  const latest = useLiveStore((s) => s.latest);
  const status = useLiveStore((s) => s.status);
  const session = useAuthStore((s) => s.session);
  const logout = useAuthStore((s) => s.logout);
  const soundEnabled = useLiveStore((s) => s.soundEnabled);
  const toggleSound = useLiveStore((s) => s.toggleSound);
  const [menuOpen, setMenuOpen] = useState(false);
  const health = useLiveHealth();

  // 시계: 교대·이력 확인에 쓰이므로 분 단위면 충분(초는 시선을 뺏는다).
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 20_000);
    return () => clearInterval(id);
  }, []);

  // 품목/LOT: 최신 검사결과 우선, 없으면 워커 하트비트(오더 전환 즉시 반영).
  const itemCode = latest?.item_code ?? status?.item_code ?? "—";
  const lot = latest?.lot;

  return (
    <header
      className="flex flex-none items-center gap-3 border-b-2 border-gray-300 bg-white px-3 py-2"
      data-testid="hmi-header"
    >
      {/* 현재 무엇을 검사 중인가 — 작업자가 가장 먼저 확인하는 정보. */}
      <div className="flex min-w-0 items-baseline gap-2">
        <span
          className="truncate text-hmi-num font-black text-gray-900"
          data-testid="header-item"
        >
          {itemCode}
        </span>
        {lot && (
          <span
            className="truncate text-hmi-cap font-semibold text-gray-500"
            data-testid="header-lot"
          >
            {lot}
          </span>
        )}
      </div>

      <div className="ml-auto flex items-center gap-2">
        {/* 검출 개수(다중 튜브 오더에서 특히 중요). */}
        {status && (
          <span
            className="hidden whitespace-nowrap rounded-lg bg-gray-100 px-2 py-1 text-hmi-cap font-bold text-gray-700 sm:inline"
            data-testid="header-detected"
          >
            검출 {status.detected}/{status.expected}
          </span>
        )}
        {/* 라이브 상태: 기호+문자+색 3중 표기. */}
        <span
          className={`whitespace-nowrap rounded-lg border-2 px-2 py-1 text-hmi-cap font-bold ${TONE[health.tone]}`}
          role="status"
          data-testid="header-health"
          data-tone={health.tone}
        >
          <span aria-hidden className="mr-1 font-black">
            {health.mark}
          </span>
          {health.text}
        </span>
        <span className="whitespace-nowrap text-hmi-cap font-bold tabular-nums text-gray-500">
          {now.toLocaleTimeString("ko-KR", {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>
        {/* 관리 동작은 메뉴 뒤로 — 현장 오조작 방지. */}
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          aria-expanded={menuOpen}
          aria-label="설정 메뉴"
          className="rounded-lg border-2 border-gray-300 px-2 py-1 text-hmi-cap font-black text-gray-600 active:scale-95"
          data-testid="header-menu"
        >
          ⋯
        </button>
      </div>

      {menuOpen && (
        <div
          className="absolute right-2 top-14 z-30 flex w-56 flex-col gap-2 rounded-xl border-2 border-gray-300 bg-white p-3 shadow-xl"
          data-testid="header-menu-panel"
        >
          <button
            type="button"
            onClick={() => {
              toggleSound();
              setMenuOpen(false);
            }}
            className="rounded-lg border-2 border-gray-300 px-3 py-3 text-left text-hmi-cap font-bold text-gray-800 active:scale-95"
            data-testid="menu-sound"
          >
            NG 소리 {soundEnabled ? "끄기" : "켜기"}
          </button>
          <div className="text-hmi-cap text-gray-500">
            로그인: {session?.username}
          </div>
          <button
            type="button"
            onClick={() => {
              setMenuOpen(false);
              logout();
            }}
            className="rounded-lg border-2 border-gray-300 px-3 py-3 text-left text-hmi-cap font-bold text-gray-800 active:scale-95"
            data-testid="menu-logout"
          >
            로그아웃
          </button>
        </div>
      )}
    </header>
  );
}
