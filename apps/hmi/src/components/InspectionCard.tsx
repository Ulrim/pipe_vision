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
 * "검사가 살아있는가"는 색이 아니라 상단 상태 표시와 하단 누적 타일이
 * 알려주므로, 양품을 무채색으로 둬도 멈춘 화면과 혼동되지 않는다.
 */
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";
import { DefectBadges } from "./DefectBadges";
import { ImageView } from "./ImageView";

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
}

export function InspectionCard({ result, onReview }: InspectionCardProps) {
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
          isNg ? "border-ng bg-ng" : "border-gray-300 bg-white"
        }`}
      >
        {/* 판정 — 남는 높이를 차지하고 세로 중앙에 둔다(시선이 먼저 닿는 자리). */}
        <div className="flex min-h-0 flex-1 items-center gap-3">
          <span
            aria-hidden
            className={`text-verdict font-black leading-none ${
              isNg ? "text-white" : "text-ok"
            }`}
          >
            {isNg ? "✕" : "✓"}
          </span>
          <span
            className={`text-verdict font-black leading-none tracking-tight ${
              isNg ? "text-white" : "text-gray-800"
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
        <div className="grid grid-cols-2 gap-2">
          <Metric label="측정" value={fmtMm(result.meas_length_mm)} unit="mm" />
          <Metric
            label="편차"
            value={fmtDeviation(result.deviation_mm)}
            unit="mm"
            caption={`기준 ${fmtMm(result.ref_length_mm)}`}
            warn={result.length_verdict === Verdict.NG}
          />
        </div>

        {isNg ? (
          <div className="flex flex-col gap-2">
            <DefectBadges codes={result.defect_codes} />
            {onReview && (
              <button
                type="button"
                onClick={() => onReview(result)}
                className="min-h-touch w-full rounded-xl bg-white text-hmi-num font-black text-ng-fg shadow active:scale-95"
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

      {/* 우: 판정 오버레이(측정선·끝단이 그려진 이미지). */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border-4 border-gray-300 bg-white">
        <ImageView
          label="판정 이미지"
          inspectionId={result.id}
          kind="result"
          fill
        />
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  unit,
  caption,
  warn,
}: {
  label: string;
  value: string;
  unit: string;
  /** 보조 정보(예: 기준값) — 자리를 아끼려 값 아래 작게 붙인다. */
  caption?: string;
  warn?: boolean;
}) {
  return (
    <div className="min-w-0 overflow-hidden rounded-xl bg-white/80 px-2 py-1">
      <div className="text-hmi-cap font-semibold text-gray-500">{label}</div>
      <div
        className={`flex items-baseline gap-1 whitespace-nowrap font-black tabular-nums text-hmi-num ${
          warn ? "text-ng-fg" : "text-gray-900"
        }`}
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
