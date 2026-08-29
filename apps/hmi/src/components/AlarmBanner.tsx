/**
 * NG 알람 배너 (CLAUDE.md §5 M6).
 * - 단건 NG: 서버 alarm 이벤트(lastAlarm) 표시 + 작업자 닫기.
 * - 연속 NG: consecutiveAlarmActive 시 강조 배너 + 관리자 확인 요청.
 * - 소리: NG 발생 시 비프(소리 켜짐 시). 토글 UI 는 상단 ⋯ 메뉴로 이동했다
 *   (좁은 현장 화면에서 자리를 아끼고 오조작을 줄이기 위해).
 * - 색약 고려 — 색+아이콘+텍스트 3중 표기.
 */
import { useEffect, useRef } from "react";
import { useLiveStore } from "@/store/liveStore";
import { useLatestBatch } from "@/hooks/useBatches";
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
  const latest = useLiveStore((s) => s.latest);
  const consecutiveActive = useLiveStore((s) => s.consecutiveAlarmActive);
  const consecutiveNg = useLiveStore((s) => s.consecutiveNg);
  const soundEnabled = useLiveStore((s) => s.soundEnabled);
  const dismissAlarm = useLiveStore((s) => s.dismissAlarm);
  const acknowledge = useLiveStore((s) => s.acknowledgeConsecutive);
  // 최신 배치가 다중 튜브 배치면 알람을 배치 단위로 요약(N개 중 M개 NG).
  const latestBatch = useLatestBatch();

  // 새 알람 도착 시 비프(소리 켜짐).
  const lastAlarmId = useRef<number | null | undefined>(undefined);
  useEffect(() => {
    if (!lastAlarm) return;
    if (lastAlarm.id !== lastAlarmId.current) {
      lastAlarmId.current = lastAlarm.id;
      if (soundEnabled) beep();
    }
  }, [lastAlarm, soundEnabled]);

  // 알람이 가리키는 LOT 이 최신 배치(다중 튜브)와 일치하면 배치 요약을 표시.
  const batchAlarm =
    lastAlarm &&
    latestBatch?.isBatch &&
    latestBatch.ngCount > 0 &&
    latestBatch.lot === lastAlarm.lot
      ? latestBatch
      : null;

  // 현재 화면이 이미 NG 를 크게 띄우고 있으면 단건 알람 줄은 **중복**이다
  // (같은 사실을 두 번 말하면서 재확인 버튼까지 가린다 — 800x480 실측 확인).
  // 이 줄의 존재 이유는 "NG 뒤에 양품이 지나가 화면이 조용해진 뒤에도 미확인
  // NG 를 붙잡아 두는 것"이므로, 최신 판정이 NG 가 아닐 때만 띄운다.
  const showLastAlarm = !!lastAlarm && latest?.final_verdict !== "NG";
  if (!showLastAlarm && !consecutiveActive) return null;

  // 이 알람은 App 에서 **본문 흐름 안**(판정 영역과 하단 스트립 사이)에 둔다.
  // 오버레이로 겹치면 판정·재확인 버튼을 가려버린다(실측 확인). 흐름에 두면
  // 판정 영역이 그만큼 줄어들 뿐 스크롤은 생기지 않는다(min-h-0 flex).
  return (
    <div className="flex flex-col gap-1.5">
      {consecutiveActive && (
        <div
          role="alert"
          data-testid="consecutive-alarm"
          className="flex items-center gap-2 rounded-xl border-4 border-white bg-ng px-3 py-2 text-white shadow-xl"
        >
          <span aria-hidden className="text-hmi-num font-black">
            ⚠
          </span>
          <span className="min-w-0 flex-1 text-hmi-body font-black">
            연속 NG {consecutiveNg}건 — 관리자 확인 필요
          </span>
          <button
            type="button"
            onClick={acknowledge}
            className="min-h-[2.75rem] flex-none rounded-lg bg-white px-4 text-hmi-body font-black text-ng-fg active:scale-95"
            data-testid="ack-consecutive"
          >
            관리자 확인
          </button>
        </div>
      )}

      {showLastAlarm && lastAlarm && (
        <div
          role="alert"
          data-testid="ng-alarm"
          className="flex items-center gap-2 rounded-xl border-2 border-ng bg-ng-bg px-3 py-1.5 shadow-lg"
        >
          <span aria-hidden className="text-hmi-body font-black text-ng">
            ✕
          </span>
          {/* 판정 패널은 '최신' 결과만 보여준다 — NG 뒤에 양품이 지나가면
              화면이 초록으로 바뀌므로, 확인 전까지 이 줄이 NG 를 붙잡아 둔다. */}
          <span className="min-w-0 flex-1 truncate text-hmi-cap font-black text-ng-fg">
            {batchAlarm ? (
              <span data-testid="ng-alarm-batch">
                미확인 NG · {batchAlarm.ngCount}/{batchAlarm.total}개
              </span>
            ) : (
              <>미확인 NG</>
            )}
          </span>
          <DefectBadges codes={lastAlarm.defect_codes} size="sm" />
          <button
            type="button"
            onClick={dismissAlarm}
            className="min-h-[2.75rem] flex-none rounded-lg border-2 border-ng bg-white px-4 text-hmi-cap font-black text-ng-fg active:scale-95"
            data-testid="dismiss-alarm"
          >
            확인
          </button>
        </div>
      )}
    </div>
  );
}
