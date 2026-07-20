/**
 * 워커 라이브니스 상태 스트립 (CLAUDE.md §5 M6/M10, 원칙: 현장 가독성·색약 고려).
 *
 * 배경: 워커가 살아있어도 튜브 0개 검출/취득 실패면 검사 이벤트가 0건이라
 * "실시간 연결됨"인데도 화면이 죽은 듯 보인다. WS status(하트비트)를 받아
 * 상단 스트립으로 원인을 즉시 표기한다.
 *
 * 표기 원칙: 큰 폰트 + 색 + 아이콘 이중표기(색 단독 의존 금지). 한국어.
 * 신선도: statusAt 6초 초과 무신호면 "워커 응답 없음"으로 전환하기 위해
 * 1초 간격으로 재렌더(setInterval, cleanup 필수)한다.
 */
import { useEffect, useState } from "react";
import { useLiveStore } from "@/store/liveStore";
import type { StatusData } from "@/types/ws";

/** 이 시간(ms)을 넘겨 무신호면 "워커 응답 없음"으로 간주. */
export const STATUS_STALE_MS = 6000;

type Tone = "idle" | "danger" | "orange" | "yellow" | "ok" | "ok-ng";

const TONE: Record<Tone, { container: string; icon: string }> = {
  idle: {
    container: "bg-gray-100 border-gray-300 text-gray-600",
    icon: "text-gray-400",
  },
  danger: {
    container: "bg-ng-bg border-ng text-ng-fg",
    icon: "text-ng",
  },
  orange: {
    container: "bg-orange-100 border-orange-400 text-orange-900",
    icon: "text-orange-500",
  },
  yellow: {
    container: "bg-yellow-100 border-yellow-400 text-yellow-900",
    icon: "text-yellow-600",
  },
  ok: {
    container: "bg-ok-bg border-ok text-ok-fg",
    icon: "text-ok",
  },
  "ok-ng": {
    container: "bg-ok-bg border-ok text-ok-fg",
    icon: "text-ok",
  },
};

interface Descriptor {
  tone: Tone;
  icon: string;
  aria: string;
  content: React.ReactNode;
}

/** 순수 함수: 현재 status/경과시각 → 표기 서술자. 테스트/재사용 용이. */
export function describeStatus(
  status: StatusData | null,
  statusAt: number | null,
  now: number,
): Descriptor {
  if (!status || statusAt == null) {
    return {
      tone: "idle",
      icon: "•",
      aria: "검사 대기 중",
      content: "검사 대기 중…",
    };
  }

  const elapsedMs = now - statusAt;
  if (elapsedMs > STATUS_STALE_MS) {
    const secs = Math.floor(elapsedMs / 1000);
    return {
      tone: "danger",
      icon: "⚠",
      aria: `워커 응답 없음, ${secs}초 무신호`,
      content: `워커 응답 없음 (${secs}s 무신호)`,
    };
  }

  if (status.error) {
    return {
      tone: "danger",
      icon: "⚠",
      aria: `취득 또는 검사 오류: ${status.error}`,
      content: `취득/검사 오류: ${status.error}`,
    };
  }

  const { detected, expected, ng } = status;

  if (detected === 0) {
    return {
      tone: "orange",
      icon: "●",
      aria: `튜브 미검출 0 / ${expected}, 조명 배치 노출 확인`,
      content: `튜브 미검출 0/${expected} — 조명·배치·노출 확인`,
    };
  }

  if (detected !== expected) {
    // 개수 불일치(검출은 됨). NG 가 함께 있으면 NG 수를 병기.
    return {
      tone: "yellow",
      icon: "▲",
      aria: `검출 ${detected} / ${expected} 개수 불일치${
        ng > 0 ? `, NG ${ng}` : ""
      }`,
      content: (
        <>
          검출 {detected}/{expected} (개수 불일치)
          {ng > 0 && (
            <span className="ml-1 font-black text-ng-fg">· NG {ng}</span>
          )}
        </>
      ),
    };
  }

  // detected === expected
  if (ng === 0) {
    return {
      tone: "ok",
      icon: "✓",
      aria: `검출 ${detected} / ${expected} 정상`,
      content: `검출 ${detected}/${expected} · 정상`,
    };
  }

  // 정상 검출이지만 NG 존재 → 초록/빨강 혼합.
  return {
    tone: "ok-ng",
    icon: "✓",
    aria: `검출 ${detected} / ${expected}, NG ${ng}`,
    content: (
      <>
        검출 {detected}/{expected} ·{" "}
        <span className="font-black text-ng-fg">NG {ng}</span>
      </>
    ),
  };
}

export function LiveStatusStrip() {
  const status = useLiveStore((s) => s.status);
  const statusAt = useLiveStore((s) => s.statusAt);

  // 신선도 tick: 1초마다 재렌더해 무신호(6초) 판정을 갱신한다.
  // 하트비트가 아직 없으면(idle) 재렌더할 이유가 없으므로 타이머를 돌리지 않는다.
  const [, setTick] = useState(0);
  const active = statusAt != null;
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [active]);

  const d = describeStatus(status, statusAt, Date.now());
  const tone = TONE[d.tone];

  return (
    <div
      role="status"
      data-testid="live-status-strip"
      data-tone={d.tone}
      aria-label={`검사 상태: ${d.aria}`}
      className={`flex w-full items-center gap-3 rounded-xl border-2 px-5 py-3 shadow-sm ${tone.container}`}
    >
      <span aria-hidden className={`text-hmi-lg font-black ${tone.icon}`}>
        {d.icon}
      </span>
      <span className="text-hmi font-bold">{d.content}</span>
    </div>
  );
}
