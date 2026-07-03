/**
 * NG 알람 배너 (CLAUDE.md §5 M6).
 * - 단건 NG: 서버 alarm 이벤트(lastAlarm) 표시 + 작업자 닫기.
 * - 연속 NG: consecutiveAlarmActive 시 강조 배너 + 관리자 확인 요청.
 * - 소리 토글: NG 발생 시 비프(소리 켜짐 시). 색약 고려 — 색+아이콘+텍스트.
 */
import { useEffect, useRef } from "react";
import { useLiveStore, CONSECUTIVE_NG_THRESHOLD } from "@/store/liveStore";
import { DefectBadges } from "./DefectBadges";

/** WebAudio 비프(자원 사전로딩 불필요). 실패해도 무시. */
function beep() {
  try {
    const Ctx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "square";
    osc.frequency.value = 880;
    gain.gain.value = 0.08;
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.25);
    osc.onended = () => ctx.close();
  } catch {
    /* 오디오 미지원/차단 — 무시 */
  }
}

export function AlarmBanner() {
  const lastAlarm = useLiveStore((s) => s.lastAlarm);
  const consecutiveActive = useLiveStore((s) => s.consecutiveAlarmActive);
  const consecutiveNg = useLiveStore((s) => s.consecutiveNg);
  const soundEnabled = useLiveStore((s) => s.soundEnabled);
  const dismissAlarm = useLiveStore((s) => s.dismissAlarm);
  const acknowledge = useLiveStore((s) => s.acknowledgeConsecutive);
  const toggleSound = useLiveStore((s) => s.toggleSound);

  // 새 알람 도착 시 비프(소리 켜짐).
  const lastAlarmId = useRef<number | null | undefined>(undefined);
  useEffect(() => {
    if (!lastAlarm) return;
    if (lastAlarm.id !== lastAlarmId.current) {
      lastAlarmId.current = lastAlarm.id;
      if (soundEnabled) beep();
    }
  }, [lastAlarm, soundEnabled]);

  if (!lastAlarm && !consecutiveActive) {
    return (
      <div className="flex items-center justify-end">
        <SoundToggle enabled={soundEnabled} onToggle={toggleSound} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {consecutiveActive && (
        <div
          role="alert"
          data-testid="consecutive-alarm"
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border-4 border-ng bg-ng px-5 py-4 text-white shadow-lg"
        >
          <div className="flex items-center gap-3">
            <span aria-hidden className="text-hmi-lg font-black">
              ⚠
            </span>
            <span className="text-hmi font-bold">
              연속 NG {consecutiveNg}건 (임계 {CONSECUTIVE_NG_THRESHOLD}) —
              관리자 확인 요청
            </span>
          </div>
          <button
            type="button"
            onClick={acknowledge}
            className="rounded-lg bg-white px-5 py-2 text-hmi font-bold text-ng-fg active:scale-95"
            data-testid="ack-consecutive"
          >
            관리자 확인
          </button>
        </div>
      )}

      {lastAlarm && (
        <div
          role="alert"
          data-testid="ng-alarm"
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border-2 border-ng bg-ng-bg px-5 py-3"
        >
          <div className="flex items-center gap-3">
            <span aria-hidden className="text-hmi-lg font-black text-ng">
              ✕
            </span>
            <span className="text-hmi font-bold text-ng-fg">
              NG 발생 · LOT {lastAlarm.lot}
            </span>
            <DefectBadges codes={lastAlarm.defect_codes} size="sm" />
          </div>
          <div className="flex items-center gap-2">
            <SoundToggle enabled={soundEnabled} onToggle={toggleSound} />
            <button
              type="button"
              onClick={dismissAlarm}
              className="rounded-lg border-2 border-ng bg-white px-4 py-2 font-bold text-ng-fg active:scale-95"
              data-testid="dismiss-alarm"
            >
              확인
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function SoundToggle({
  enabled,
  onToggle,
}: {
  enabled: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={enabled}
      className="rounded-lg bg-white/80 px-3 py-2 font-semibold text-gray-700 shadow-sm active:scale-95"
      data-testid="sound-toggle"
    >
      {enabled ? "🔊 소리 켬" : "🔇 소리 끔"}
    </button>
  );
}
