/**
 * 다중 튜브 배치 결과 — 현장 고정화면용 (CLAUDE.md §5 M10, 부록 A.1).
 *
 * 설계 근거(파이 7" 800x480 실측 후 재설계):
 * - 한 번에 최대 20개를 검사하므로, 작업자의 질문은 **"이번 판에 불량이 몇 개
 *   있고 어느 것인가?"** 다. 그래서 좌측에 "NG n개 / 총 m개"를 초대형으로 두고,
 *   튜브별 상태는 **번호 타일 한 줄**로 압축한다. 이전의 큰 카드 그리드는
 *   480px 안에 들어가지 않아 스크롤을 유발했다.
 * - NG 타일만 누를 수 있고(재확인 대상), 타일은 장갑 터치를 고려해 최소 44px.
 * - 원본 토글은 뺐다 — 작은 화면에서 원본은 판독에 도움이 안 되고(관리자
 *   웹에서 확인), 오조작만 늘린다.
 *
 * **색 사용 규칙(고성능 HMI / ISA-101)**: 전량 양품이면 무채색, NG 가 있을
 * 때만 화면이 빨강으로 바뀐다. 늘 초록인 화면은 작업자를 색에 둔감하게
 * 만들어 정작 NG 를 놓치게 하기 때문이다.
 */
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";
import type { BatchGroup } from "@/lib/batching";
import { ImageView } from "./ImageView";

export interface BatchCardProps {
  batch: BatchGroup;
  onReview?: (r: InspectionResult) => void;
}

export function BatchCard({ batch, onReview }: BatchCardProps) {
  const isNg = batch.verdict === Verdict.NG;

  return (
    <section
      className="flex min-h-0 flex-1 gap-2"
      data-testid="batch-card"
      data-verdict={batch.verdict}
      data-total={batch.total}
      data-ng={batch.ngCount}
      aria-label={`배치 검사결과 ${batch.item_code}, 총 ${batch.total}개 중 NG ${batch.ngCount}개`}
    >
      {/* 좌: 이번 판의 결론 + 튜브별 상태. */}
      <div
        className={`flex min-w-0 flex-[1.05] flex-col justify-between rounded-2xl border-4 p-3 ${
          isNg ? "border-ng bg-ng" : "border-gray-300 bg-white"
        }`}
      >
        <div className="flex items-baseline gap-3">
          <span
            aria-hidden
            className={`text-verdict font-black leading-none ${isNg ? "text-white" : "text-ok"}`}
          >
            {isNg ? "✕" : "✓"}
          </span>
          <div className="min-w-0">
            <div
              className={`text-verdict font-black leading-none tracking-tight ${
                isNg ? "text-white" : "text-gray-800"
              }`}
              data-testid="batch-verdict-text"
            >
              {isNg ? `NG ${batch.ngCount}` : "전량 양품"}
            </div>
            <div
              className={`mt-1 text-hmi-body font-bold ${isNg ? "text-white/90" : "text-gray-600"}`}
            >
              총 {batch.total}개 검사 · 양품 {batch.okCount}개
            </div>
          </div>
        </div>

        {/* 튜브별 상태 타일 — 어느 번호가 불량인지 한눈에. */}
        <ul
          className="flex flex-wrap content-start gap-1.5"
          data-testid="batch-tube-grid"
        >
          {batch.tubes.map((t, i) => (
            <TubeTile
              key={t.id ?? `tube-${i}`}
              tube={t}
              index={t.tube_index ?? i}
              onReview={onReview}
            />
          ))}
        </ul>

        {isNg && onReview && (
          <p className="text-hmi-cap font-bold text-white">
            빨간 번호를 누르면 재확인을 입력합니다.
          </p>
        )}
      </div>

      {/* 우: 배치 오버레이(모든 튜브가 박스로 표기된 판정 이미지). */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border-4 border-gray-300 bg-white">
        <ImageView
          label="판정 이미지"
          inspectionId={batch.representativeId}
          kind="result"
          fill
        />
      </div>
    </section>
  );
}

function TubeTile({
  tube,
  index,
  onReview,
}: {
  tube: InspectionResult;
  index: number;
  onReview?: (r: InspectionResult) => void;
}) {
  const isNg = tube.final_verdict === Verdict.NG;
  const clickable = isNg && !!onReview;
  return (
    <li>
      <button
        type="button"
        disabled={!clickable}
        onClick={() => clickable && onReview?.(tube)}
        data-testid="batch-tube"
        data-verdict={tube.final_verdict}
        data-tube-index={index}
        aria-label={`${index + 1}번 튜브 ${isNg ? "불량" : "양품"}`}
        className={`flex h-11 min-w-[2.75rem] items-center justify-center rounded-lg border-2 px-2 text-hmi-body font-black tabular-nums ${
          isNg
            ? "border-white bg-white text-ng-fg"
            : "border-gray-300 bg-white text-gray-500"
        } ${clickable ? "active:scale-95" : "cursor-default"}`}
      >
        <span aria-hidden className="mr-1 text-hmi-cap">
          {isNg ? "✕" : "✓"}
        </span>
        {index + 1}
      </button>
    </li>
  );
}
