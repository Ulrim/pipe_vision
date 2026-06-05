/**
 * 최신 검사결과 카드 (CLAUDE.md §5 M10).
 * 원본/결과 이미지, 길이값(meas/ref/deviation), OK/NG(색+아이콘 이중표기),
 * 불량유형 뱃지. 현장 대형 디스플레이용 큰 폰트.
 */
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";
import { VerdictBadge } from "./VerdictBadge";
import { DefectBadges } from "./DefectBadges";
import { ImageView } from "./ImageView";

function fmtMm(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(2)} mm`;
}

function fmtDeviation(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)} mm`;
}

export interface InspectionCardProps {
  result: InspectionResult | null;
  onReview?: (r: InspectionResult) => void;
}

export function InspectionCard({ result, onReview }: InspectionCardProps) {
  if (!result) {
    return (
      <div
        className="flex h-full min-h-[20rem] items-center justify-center rounded-2xl border-2 border-dashed border-gray-300 bg-white"
        data-testid="inspection-card-empty"
      >
        <span className="text-hmi-lg text-gray-400">검사 대기 중…</span>
      </div>
    );
  }

  const isNg = result.final_verdict === Verdict.NG;
  const frame = isNg ? "border-ng ring-4 ring-ng/30" : "border-ok ring-4 ring-ok/20";

  return (
    <section
      className={`flex flex-col gap-4 rounded-2xl border-4 bg-white p-6 shadow-lg ${frame}`}
      data-testid="inspection-card"
      data-verdict={result.final_verdict}
      aria-label={`검사결과 LOT ${result.lot}`}
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-hmi-lg font-black">
            {result.item_code}
            <span className="ml-3 text-hmi text-gray-500">LOT {result.lot}</span>
          </div>
          <div className="text-base text-gray-500">
            {result.cam_id} · {new Date(result.inspected_at).toLocaleString("ko-KR")}
            {result.proc_time_ms != null && ` · ${result.proc_time_ms}ms`}
          </div>
        </div>
        <VerdictBadge verdict={result.final_verdict} size="xl" />
      </header>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <ImageView label="원본" path={result.raw_image_path} />
        <ImageView label="판정 결과" path={result.result_image_path} />
      </div>

      <div className="grid grid-cols-3 gap-3 text-center">
        <Metric label="측정 길이" value={fmtMm(result.meas_length_mm)} emphasize />
        <Metric label="기준 길이" value={fmtMm(result.ref_length_mm)} />
        <Metric
          label="편차"
          value={fmtDeviation(result.deviation_mm)}
          warn={result.length_verdict === Verdict.NG}
        />
      </div>

      {isNg && (
        <div className="flex flex-col gap-3">
          <DefectBadges codes={result.defect_codes} />
          {onReview && (
            <button
              type="button"
              onClick={() => onReview(result)}
              className="self-start rounded-xl bg-ng px-6 py-3 text-hmi font-bold text-white shadow active:scale-95"
              data-testid="open-review"
            >
              재확인 입력
            </button>
          )}
        </div>
      )}

      {result.manual_verdict && (
        <div className="text-base text-gray-600">
          작업자 재확인:{" "}
          <span className="font-bold">{result.manual_verdict}</span>
        </div>
      )}
    </section>
  );
}

function Metric({
  label,
  value,
  emphasize,
  warn,
}: {
  label: string;
  value: string;
  emphasize?: boolean;
  warn?: boolean;
}) {
  return (
    <div className="rounded-xl bg-gray-50 p-3">
      <div className="text-sm font-medium text-gray-500">{label}</div>
      <div
        className={`font-black ${emphasize ? "text-hmi-lg" : "text-hmi"} ${
          warn ? "text-ng-fg" : "text-gray-900"
        }`}
      >
        {value}
      </div>
    </div>
  );
}
