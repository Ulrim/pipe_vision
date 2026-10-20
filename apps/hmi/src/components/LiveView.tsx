/**
 * 라이브 뷰 — 카메라가 지금 보는 장면 (2026-10-20).
 *
 * 설치·점검할 때 카메라 위치·초점·노출·검사 영역을 맞추는 화면이다. 검사 화면 위에 전체
 * 화면으로 뜨고, 닫으면 영상 송출도 멈춘다(현장 무선 회선을 아낀다).
 *
 * - 영상 위에 **검사 영역**(노란 점선, 결과 사진과 같은 표시)과 선택적 3분할 격자.
 * - **초점 막대**: 이번 세션 최고값 대비 %. 렌즈를 돌려 최고점을 찾는다.
 * - 노출 경고: 반사 포화·암부·평균 밝기.
 * - **1장 촬영**: 지금 1장 찍어 평소처럼 검사·저장한다(제품이 안 보여도 남긴다).
 */
import { useState } from "react";
import type { LiveViewState } from "@aivis/shared-types";
import {
  FOCUS_GOOD_RATIO,
  focusRatio,
  liveAdvice,
  useLiveView,
} from "@/hooks/useLiveView";

export interface LiveViewProps {
  camId: string;
  onClose: () => void;
  pollMs?: number;
}

/** 검사 영역·격자 오버레이. 좌표는 화면 비율(0~1). */
function Overlay({ roi, grid }: { roi?: number[] | null; grid: boolean }) {
  const area = roi && roi.length === 4 ? roi : null;
  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full"
      viewBox="0 0 1 1"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {grid && (
        <g stroke="white" strokeOpacity={0.55} strokeWidth={1} vectorEffect="non-scaling-stroke">
          {[1 / 3, 2 / 3].map((v) => (
            <g key={v}>
              <line x1={v} y1={0} x2={v} y2={1} vectorEffect="non-scaling-stroke" />
              <line x1={0} y1={v} x2={1} y2={v} vectorEffect="non-scaling-stroke" />
            </g>
          ))}
          <line x1={0.48} y1={0.5} x2={0.52} y2={0.5} vectorEffect="non-scaling-stroke" />
          <line x1={0.5} y1={0.48} x2={0.5} y2={0.52} vectorEffect="non-scaling-stroke" />
        </g>
      )}
      {area && (
        <g data-testid="live-roi">
          {/* 영역 밖은 어둡게 — 결과 사진과 같은 표시 */}
          <path
            fillRule="evenodd"
            fill="black"
            fillOpacity={0.45}
            d={`M0 0H1V1H0Z M${area[0]} ${area[1]}H${area[2]}V${area[3]}H${area[0]}Z`}
          />
          <rect
            x={area[0]}
            y={area[1]}
            width={area[2] - area[0]}
            height={area[3] - area[1]}
            fill="none"
            stroke="#ffd700"
            strokeWidth={3}
            strokeDasharray="10 6"
            vectorEffect="non-scaling-stroke"
          />
        </g>
      )}
    </svg>
  );
}

function FocusBar({ st }: { st: LiveViewState | null }) {
  const ratio = focusRatio(st);
  const pct = ratio == null ? 0 : Math.round(ratio * 100);
  const good = ratio != null && ratio >= FOCUS_GOOD_RATIO;
  return (
    <div data-testid="live-focus">
      <div className="flex items-baseline justify-between">
        <span className="text-hmi-body font-black">초점</span>
        <span className="text-hmi-body font-bold tabular-nums">
          {ratio == null ? "—" : `${pct}%`}
        </span>
      </div>
      <div
        className="mt-1 h-8 overflow-hidden rounded-lg bg-gray-700"
        role="meter"
        aria-label="초점 (이번 세션 최고값 대비)"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div
          className={`h-full ${good ? "bg-ok" : "bg-amber-400"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="mt-1 text-hmi-cap text-gray-300">
        {ratio == null
          ? "영상이 오면 표시됩니다"
          : good
            ? "✔ 최고 근처 — 이 자리에서 렌즈를 고정하세요"
            : "렌즈를 천천히 돌려 막대가 가장 길어지는 자리를 찾으세요"}
        {st?.focus != null && st.focus_peak != null && (
          <span className="ml-2 tabular-nums text-gray-400">
            ({Math.round(st.focus)} / 최고 {Math.round(st.focus_peak ?? 0)})
          </span>
        )}
      </p>
    </div>
  );
}

export function LiveView({ camId, onClose, pollMs }: LiveViewProps) {
  const { state, frameUrl, error, ended, restart, capture, captureSent } = useLiveView(
    camId,
    pollMs,
  );
  const [grid, setGrid] = useState(false);
  const advice = liveAdvice(state);
  const w = state?.width ?? 4;
  const h = state?.height ?? 3;
  const left = state?.session_left_s;

  return (
    <div
      className="fixed inset-0 z-40 flex bg-gray-900 text-white"
      role="dialog"
      aria-modal="true"
      aria-label={`라이브 뷰 ${camId}`}
      data-testid="live-view"
    >
      {/* 영상 — 남는 공간을 전부 쓰되 비율은 카메라 그대로(검사 영역 좌표가 맞도록) */}
      <div className="flex min-w-0 flex-1 items-center justify-center p-3 [container-type:size]">
        <div
          className="relative bg-black"
          style={{
            aspectRatio: `${w} / ${h}`,
            width: `min(100cqw, calc(100cqh * ${w} / ${h}))`,
          }}
        >
          {frameUrl ? (
            <img
              src={frameUrl}
              alt={`${camId} 라이브 영상`}
              className="h-full w-full object-fill"
              data-testid="live-frame"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-hmi-body text-gray-400">
              {ended ? "꺼짐" : "영상 기다리는 중…"}
            </div>
          )}
          <Overlay roi={state?.roi} grid={grid} />
          {state?.streaming && (
            <span className="absolute left-3 top-3 rounded-md bg-red-600 px-3 py-1 text-hmi-cap font-black">
              ● LIVE
            </span>
          )}
        </div>
      </div>

      {/* 오른쪽 패널 — 지표와 조작 */}
      <aside className="flex w-[30rem] flex-none flex-col gap-4 overflow-y-auto border-l border-gray-700 p-4">
        <div className="flex items-start justify-between gap-2">
          <div>
            <h2 className="text-hmi-num font-black">라이브 뷰</h2>
            <p className="text-hmi-cap text-gray-400">
              {camId}
              {state?.width ? ` · ${state.width}x${state.height}` : ""}
              {left != null ? ` · 남은 시간 ${Math.floor(left / 60)}:${String(Math.floor(left % 60)).padStart(2, "0")}` : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="min-h-touch flex-none whitespace-nowrap rounded-xl border-2 border-gray-500 px-6 text-hmi-body font-black active:scale-95"
            data-testid="live-close"
          >
            닫기
          </button>
        </div>

        {ended && (
          <div className="rounded-xl bg-gray-800 p-3" role="status">
            <p className="text-hmi-body font-bold">켜 둘 수 있는 시간이 끝났습니다.</p>
            <button
              type="button"
              onClick={() => void restart()}
              className="mt-2 min-h-touch w-full rounded-xl bg-white text-hmi-body font-black text-gray-900 active:scale-95"
              data-testid="live-restart"
            >
              다시 켜기
            </button>
          </div>
        )}

        {error && (
          <p className="rounded-xl bg-ng-bg p-3 text-hmi-body font-bold text-ng-fg" role="alert">
            ⚠ {error}
          </p>
        )}

        <FocusBar st={state} />

        <dl className="grid grid-cols-3 gap-2 text-center" data-testid="live-exposure">
          <div className="rounded-lg bg-gray-800 p-2">
            <dt className="text-hmi-cap text-gray-400">밝기</dt>
            <dd className="text-hmi-body font-black tabular-nums">
              {state?.brightness != null ? Math.round(state.brightness) : "—"}
            </dd>
          </div>
          <div className="rounded-lg bg-gray-800 p-2">
            <dt className="text-hmi-cap text-gray-400">포화</dt>
            <dd className="text-hmi-body font-black tabular-nums">
              {state?.saturated_pct != null ? `${state.saturated_pct.toFixed(1)}%` : "—"}
            </dd>
          </div>
          <div className="rounded-lg bg-gray-800 p-2">
            <dt className="text-hmi-cap text-gray-400">암부</dt>
            <dd className="text-hmi-body font-black tabular-nums">
              {state?.dark_pct != null ? `${state.dark_pct.toFixed(0)}%` : "—"}
            </dd>
          </div>
        </dl>

        {advice.length > 0 && (
          <ul className="space-y-2" data-testid="live-advice">
            {advice.map((a) => (
              <li
                key={a.text}
                className={`rounded-lg p-2 text-hmi-cap font-bold ${a.level === "warn" ? "bg-amber-300 text-gray-900" : "bg-gray-800 text-gray-200"}`}
              >
                {a.level === "warn" ? "⚠ " : "ⓘ "}
                {a.text}
              </li>
            ))}
          </ul>
        )}

        {state?.trigger && (
          <p className="text-hmi-cap text-gray-400" data-testid="live-trigger">
            영상 트리거 {state.trigger.mode ?? ""} · 움직임 {state.trigger.motion ?? "—"} · 변화{" "}
            {state.trigger.change ?? "—"}
          </p>
        )}

        <div className="mt-auto space-y-3">
          <button
            type="button"
            onClick={() => void capture()}
            disabled={captureSent || state?.capture_pending}
            className="min-h-touch-lg w-full rounded-2xl bg-white text-hmi-num font-black text-gray-900 active:scale-95 disabled:opacity-50"
            data-testid="live-capture"
          >
            📷 1장 촬영
          </button>
          {captureSent && (
            <p className="text-center text-hmi-cap text-gray-300" role="status">
              촬영 요청됨 — 결과는 검사 화면에 뜹니다
            </p>
          )}
          <div className="grid grid-cols-1 gap-3">
            <button
              type="button"
              onClick={() => setGrid((g) => !g)}
              aria-pressed={grid}
              className="min-h-touch rounded-xl border-2 border-gray-500 text-hmi-body font-bold active:scale-95 aria-pressed:bg-gray-700"
              data-testid="live-grid"
            >
              격자 {grid ? "끄기" : "켜기"}
            </button>
            <button
              type="button"
              onClick={() => void restart()}
              className="min-h-touch rounded-xl border-2 border-gray-500 text-hmi-body font-bold active:scale-95"
              data-testid="live-reset-peak"
            >
              초점 다시 재기
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
}
