/**
 * 최신 검사결과 — 현장 고정화면용 (CLAUDE.md §5 M10).
 *
 * 설계 근거(파이 7" 800x480 실측 후 재설계):
 * - 작업자가 이 화면에서 얻어야 할 답은 **"이 제품 통과인가?"** 하나다.
 *   따라서 판정(OK/NG)을 화면 좌측 절반에 초대형으로 두어 1~2m 거리에서
 *   0.5초 안에 읽히게 한다. 이전 디자인은 판정이 접힘선 아래에 있었다.
 * - 이미지는 **판정 오버레이 1장만**. 작은 화면에 원본까지 나란히 두면 둘 다
 *   못 알아본다. 원본 대조는 관리자 웹(검사이력)의 일이다.
 * - 카메라ID·처리시간(ms)·초 단위 시각은 뺐다 — 작업자 판단에 쓰이지 않고
 *   자리만 차지한다(관리자 웹에서 확인 가능).
 * - NG 면 패널 전체가 빨강으로 바뀌고 재확인 버튼이 64px 이상으로 나온다
 *   (장갑 낀 손 터치).
 *
 * **색 사용 규칙(고성능 HMI / ISA-101)**: 정상(양품)은 **무채색**으로 두고
 * 색은 이상 상태에만 쓴다. 화면이 늘 초록이면 작업자가 색에 둔감해져
 * 정작 NG 가 떴을 때 눈에 안 들어오기 때문이다(업계에서 반복 지적되는
 * 실수). 그래서 양품일 때 화면은 조용한 회색이고, NG 일 때만 화면이
 * 빨강으로 확 바뀐다 — 이 **변화 자체**가 작업자의 주의를 끄는 신호다.
 *
 * **다만 빨강을 화면에 들이붓지는 않는다.** 처음에는 NG 일 때 좌측 패널 전체를
 * 빨강으로 칠했는데, 하단 알람바·이력 칩까지 더해 화면의 절반 이상이 포화
 * 빨강이 됐다. 그러면 카메라 고장이나 디스크 가득처럼 **더 급한 상황이 생겨도
 * 올릴 단계가 남지 않는다.** 색은 가리키는 것이지 뒤덮는 것이 아니다. 그래서
 * 지금은 패널 본문을 연한 빨강으로 두고 테두리와 글자로 강조한다 — 멀리서도
 * 빨간 화면으로 보이되, 진짜 경보(알람 밴드)가 뜨면 그쪽이 더 강하게 읽힌다.
 *
 * **숫자는 편차가 주인공이다.** 작업자가 판단에 쓰는 값은 "기준에서 얼마나
 * 벗어났나" 이지 측정 절대값이 아니다. 전에는 측정과 편차가 같은 크기였다.
 * 지금은 편차를 크게, 측정을 보조로 두고, 그 아래 공차 밴드를 그려 암산 없이
 * 벗어난 정도가 보이게 한다.
 */
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";
import { DefectBadges } from "./DefectBadges";
import { ImageView } from "./ImageView";
import { ToleranceGauge } from "./ToleranceGauge";

function fmtMm(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toFixed(2);
}

function fmtDeviation(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}`;
}

export interface InspectionCardProps {
  result: InspectionResult | null;
  onReview?: (r: InspectionResult) => void;
  /** 허용 공차(mm). 기준정보에서 온다. 없으면 공차 밴드를 그리지 않는다. */
  tolPlusMm?: number | null;
  tolMinusMm?: number | null;
  /** 판정 이미지를 크게 보기(작은 화면에서는 눌러야 결함이 보인다). */
  onZoomImage?: (r: InspectionResult) => void;
}

export function InspectionCard({
  result,
  onReview,
  tolPlusMm,
  tolMinusMm,
  onZoomImage,
}: InspectionCardProps) {
  if (!result) {
    return (
      <div
        className="flex min-h-0 flex-1 items-center justify-center rounded-2xl border-4 border-dashed border-gray-300 bg-white"
        data-testid="inspection-card-empty"
      >
        <span className="text-hmi-lg font-bold text-gray-400">
          검사 대기 중…
        </span>
      </div>
    );
  }

  const isNg = result.final_verdict === Verdict.NG;

  return (
    <section
      className="flex min-h-0 flex-1 gap-2"
      data-testid="inspection-card"
      data-verdict={result.final_verdict}
      aria-label={`검사결과 ${result.item_code} ${isNg ? "불량" : "정상"}`}
    >
      {/* 좌: 판정 — 화면의 주인공. */}
      <div
        className={`flex min-w-0 flex-[1.05] flex-col gap-2 rounded-2xl border-4 p-3 ${
          isNg ? "border-ng bg-ng-bg" : "border-gray-300 bg-white"
        }`}
      >
        {/* 판정 — 남는 높이를 차지하고 세로 중앙에 둔다(시선이 먼저 닿는 자리). */}
        <div className="flex min-h-0 flex-1 items-center gap-3">
          <span
            aria-hidden
            className={`text-verdict font-black leading-none ${
              isNg ? "text-ng" : "text-ok"
            }`}
          >
            {isNg ? "✕" : "✓"}
          </span>
          <span
            className={`text-verdict font-black leading-none tracking-tight ${
              isNg ? "text-ng-fg" : "text-gray-800"
            }`}
            data-testid="verdict-text"
          >
            {isNg ? "불량" : "양품"}
          </span>
        </div>

        {/* 길이 수치. 좌측 패널은 화면의 절반뿐이라 3칸이면 타일당 130px 이
            안 나와 단위(mm)가 잘린다(800x480 실측). 작업자가 실제로 보는
            **측정값과 편차** 두 칸만 크게 두고, 기준값은 편차 옆 캡션으로
            접는다(품목이 바뀌지 않는 한 고정값이라 매번 볼 필요가 없다). */}
        {/* 편차가 판단의 근거이므로 크게, 측정·기준은 보조로 접는다. */}
        <div className="flex items-end gap-3">
          <Metric
            label="편차"
            value={fmtDeviation(result.deviation_mm)}
            unit="mm"
            warn={result.length_verdict === Verdict.NG}
            big
          />
          <div className="min-w-0 pb-1 text-hmi-cap font-semibold text-gray-500">
            <div className="whitespace-nowrap tabular-nums">
              측정 {fmtMm(result.meas_length_mm)}
            </div>
            <div className="whitespace-nowrap tabular-nums">
              기준 {fmtMm(result.ref_length_mm)}
            </div>
          </div>
        </div>

        <ToleranceGauge
          deviationMm={result.deviation_mm}
          tolPlusMm={tolPlusMm}
          tolMinusMm={tolMinusMm}
        />

        {isNg ? (
          <div className="flex flex-col gap-2">
            <DefectBadges codes={result.defect_codes} />
            {onReview && (
              <button
                type="button"
                onClick={() => onReview(result)}
                className="min-h-touch w-full rounded-xl bg-ng text-hmi-num font-black text-white shadow active:scale-95"
                data-testid="open-review"
              >
                재확인 입력
              </button>
            )}
          </div>
        ) : (
          result.manual_verdict && (
            <div className="text-hmi-cap font-bold text-gray-600">
              작업자 재확인: {result.manual_verdict}
            </div>
          )
        )}
      </div>

      {/* 우: 판정 오버레이(측정선·끝단이 그려진 이미지).
          7인치를 팔 길이에서 보면 이 크기로는 가는 스크래치를 분간할 수 없다.
          눌러서 전체화면으로 볼 수 있게 한다 — 그래야 '확인용 이미지' 가 된다. */}
      <button
        type="button"
        onClick={onZoomImage ? () => onZoomImage(result) : undefined}
        disabled={!onZoomImage}
        aria-label="판정 이미지 크게 보기"
        className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border-4 border-gray-300 bg-white text-left enabled:active:scale-[0.99]"
        data-testid="zoom-image"
      >
        <ImageView
          label="판정 이미지"
          inspectionId={result.id}
          kind="result"
          fill
        />
      </button>
    </section>
  );
}

function Metric({
  label,
  value,
  unit,
  caption,
  warn,
  big,
}: {
  label: string;
  value: string;
  unit: string;
  /** 보조 정보(예: 기준값) — 자리를 아끼려 값 아래 작게 붙인다. */
  caption?: string;
  warn?: boolean;
  /** 판단의 근거가 되는 값(편차)은 크게 — 시선이 먼저 닿아야 한다. */
  big?: boolean;
}) {
  return (
    <div className="min-w-0 overflow-hidden rounded-xl bg-white/80 px-2 py-1">
      <div className="text-hmi-cap font-semibold text-gray-500">{label}</div>
      <div
        className={`flex items-baseline gap-1 whitespace-nowrap font-black tabular-nums ${
          big ? "text-hmi-lg" : "text-hmi-num"
        } ${warn ? "text-ng-fg" : "text-gray-900"}`}
      >
        {value}
        <span className="text-hmi-cap font-bold text-gray-400">{unit}</span>
      </div>
      {caption && (
        <div className="truncate text-hmi-cap font-semibold text-gray-400">
          {caption}
        </div>
      )}
    </div>
  );
}
