/**
 * 라이브 뷰 — 실시간 현황 카드에서 여는 카메라 영상 (2026-10-20).
 *
 * 사무실에서 원격으로 카메라 위치·초점·노출·검사 영역을 맞추고, 필요하면 1장 촬영으로
 * 확인 사진을 남긴다. 닫으면 영상 송출이 멈춘다(현장 무선 회선을 아낀다).
 * 작업자 HMI 의 LiveView 와 같은 지표·같은 안내다.
 */
import { useState } from "react";
import type { LiveViewState } from "@aivis/shared-types";
import { FOCUS_GOOD_RATIO, focusRatio, liveAdvice, useLiveView } from "@/hooks/useLiveView";

function Overlay({ roi, grid }: { roi?: number[] | null; grid: boolean }): JSX.Element {
  const area = roi && roi.length === 4 ? roi : null;
  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full"
      viewBox="0 0 1 1"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {grid && (
        <g stroke="white" strokeOpacity={0.55}>
          {[1 / 3, 2 / 3].map((v) => (
            <g key={v}>
              <line x1={v} y1={0} x2={v} y2={1} vectorEffect="non-scaling-stroke" />
              <line x1={0} y1={v} x2={1} y2={v} vectorEffect="non-scaling-stroke" />
            </g>
          ))}
        </g>
      )}
      {area && (
        <g data-testid="live-roi">
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
            strokeWidth={2}
            strokeDasharray="8 5"
            vectorEffect="non-scaling-stroke"
          />
        </g>
      )}
    </svg>
  );
}

function Metric({ k, v }: { k: string; v: string }): JSX.Element {
  return (
    <div className="rounded bg-slate-100 px-2 py-1 text-center">
      <dt className="text-xs text-slate-500">{k}</dt>
      <dd className="font-semibold tabular-nums">{v}</dd>
    </div>
  );
}

function FocusBar({ st }: { st: LiveViewState | null }): JSX.Element {
  const ratio = focusRatio(st);
  const pct = ratio == null ? 0 : Math.round(ratio * 100);
  const good = ratio != null && ratio >= FOCUS_GOOD_RATIO;
  return (
    <div data-testid="live-focus">
      <div className="flex justify-between text-sm">
        <span className="font-semibold">초점</span>
        <span className="tabular-nums">
          {ratio == null ? "—" : `${pct}%`}
          {st?.focus != null && st.focus_peak != null && (
            <span className="ml-1 text-xs text-slate-500">
              ({Math.round(st.focus)} / 최고 {Math.round(st.focus_peak ?? 0)})
            </span>
          )}
        </span>
      </div>
      <div
        className="mt-1 h-3 overflow-hidden rounded bg-slate-200"
        role="meter"
        aria-label="초점 (이번 세션 최고값 대비)"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div className={`h-full ${good ? "bg-ok" : "bg-warn"}`} style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-1 text-xs text-slate-500">
        {ratio == null
          ? "영상이 오면 표시됩니다"
          : good
            ? "✔ 최고 근처 — 이 자리에서 렌즈를 고정"
            : "렌즈를 천천히 돌려 막대가 가장 길어지는 자리를 찾으세요"}
      </p>
    </div>
  );
}

export function LiveViewModal({
  camId,
  onClose,
  pollMs,
}: {
  camId: string;
  onClose: () => void;
  pollMs?: number;
}): JSX.Element {
  const { state, frameUrl, error, ended, restart, capture, captureSent } = useLiveView(camId, pollMs);
  const [grid, setGrid] = useState(false);
  const advice = liveAdvice(state);
  const w = state?.width ?? 4;
  const h = state?.height ?? 3;
  const left = state?.session_left_s;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`라이브 뷰 ${camId}`}
      data-testid="live-view"
    >
      <div className="card flex max-h-full w-full max-w-6xl flex-col gap-3 overflow-y-auto p-4 lg:flex-row">
        <div className="relative min-w-0 flex-1 bg-black" style={{ aspectRatio: `${w} / ${h}` }}>
          {frameUrl ? (
            <img src={frameUrl} alt={`${camId} 라이브 영상`} className="h-full w-full" data-testid="live-frame" />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-sm text-slate-400">
              {ended ? "꺼짐" : "영상 기다리는 중…"}
            </div>
          )}
          <Overlay roi={state?.roi} grid={grid} />
          {state?.streaming && (
            <span className="absolute left-2 top-2 rounded bg-red-600 px-2 py-0.5 text-xs font-bold text-white">
              ● LIVE
            </span>
          )}
        </div>

        <aside className="flex w-full flex-none flex-col gap-3 lg:w-80">
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-lg font-bold">라이브 뷰 · {camId}</h2>
              <p className="text-xs text-slate-500">
                {state?.width ? `${state.width}x${state.height} · ` : ""}
                {left != null
                  ? `남은 시간 ${Math.floor(left / 60)}:${String(Math.floor(left % 60)).padStart(2, "0")}`
                  : ""}
              </p>
            </div>
            <button type="button" className="btn btn-ghost" onClick={onClose} data-testid="live-close">
              닫기
            </button>
          </div>

          {ended && (
            <div className="rounded bg-slate-100 p-2 text-sm" role="status">
              켜 둘 수 있는 시간이 끝났습니다.
              <button
                type="button"
                className="btn btn-primary mt-2 w-full"
                onClick={() => void restart()}
                data-testid="live-restart"
              >
                다시 켜기
              </button>
            </div>
          )}

          {error && (
            <p className="rounded bg-ng-bg p-2 text-sm text-ng-fg" role="alert">
              ✕ {error}
            </p>
          )}

          <FocusBar st={state} />

          <dl className="grid grid-cols-3 gap-2 text-sm" data-testid="live-exposure">
            <Metric k="밝기" v={state?.brightness != null ? String(Math.round(state.brightness)) : "—"} />
            <Metric k="포화" v={state?.saturated_pct != null ? `${state.saturated_pct.toFixed(1)}%` : "—"} />
            <Metric k="암부" v={state?.dark_pct != null ? `${state.dark_pct.toFixed(0)}%` : "—"} />
          </dl>

          {advice.length > 0 && (
            <ul className="space-y-1 text-sm" data-testid="live-advice">
              {advice.map((a) => (
                <li
                  key={a.text}
                  className={`rounded px-2 py-1 ${a.level === "warn" ? "bg-amber-100 text-amber-900" : "bg-slate-100 text-slate-700"}`}
                >
                  {a.level === "warn" ? "⚠ " : "ⓘ "}
                  {a.text}
                </li>
              ))}
            </ul>
          )}

          {state?.trigger && (
            <p className="text-xs text-slate-500" data-testid="live-trigger">
              영상 트리거 {state.trigger.mode ?? ""} · 움직임 {state.trigger.motion ?? "—"} · 변화{" "}
              {state.trigger.change ?? "—"}
            </p>
          )}

          <div className="mt-auto space-y-2">
            <button
              type="button"
              className="btn btn-primary w-full py-3 text-base"
              onClick={() => void capture()}
              disabled={captureSent || state?.capture_pending}
              data-testid="live-capture"
            >
              📷 1장 촬영
            </button>
            {captureSent && (
              <p className="text-center text-xs text-slate-500" role="status">
                촬영 요청됨 — 검사 이력·이 카드에 결과가 뜹니다
              </p>
            )}
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                className="btn btn-ghost"
                aria-pressed={grid}
                onClick={() => setGrid((g) => !g)}
                data-testid="live-grid"
              >
                격자 {grid ? "끄기" : "켜기"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => void restart()}
                data-testid="live-reset-peak"
              >
                초점 다시 재기
              </button>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
