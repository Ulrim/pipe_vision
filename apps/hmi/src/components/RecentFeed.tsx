/**
 * 최근 검사 이력 목록 (M10 보조). 배치 단위로 묶어 표시한다.
 * - 배치 그룹(튜브 2건 이상): 요약 행 1개(총 N개 중 NG M개, 전체 판정).
 *   행 클릭 시 배치 내 NG 튜브를 재확인 대상으로 넘긴다.
 * - 단일 튜브(하위호환): 기존 개별 행(feed-row) 유지, NG 클릭 시 재확인.
 * 최신 배치가 위(그룹 순서는 groupFeed 가 최신순 보장).
 */
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";
import type { BatchGroup } from "@/lib/batching";
import { VerdictBadge } from "./VerdictBadge";

export interface RecentFeedProps {
  batches: BatchGroup[];
  onSelect?: (r: InspectionResult) => void;
}

export function RecentFeed({ batches, onSelect }: RecentFeedProps) {
  if (batches.length === 0) {
    return (
      <div className="rounded-xl bg-white p-4 text-center text-gray-400">
        수신된 검사결과가 없습니다.
      </div>
    );
  }
  return (
    <ul className="flex flex-col gap-2" data-testid="recent-feed">
      {batches.map((b, i) =>
        b.isBatch ? (
          <BatchRow key={b.key} batch={b} onSelect={onSelect} />
        ) : (
          <SingleRow
            key={b.tubes[0]?.id ?? `idx-${i}`}
            result={b.tubes[0]}
            onSelect={onSelect}
          />
        ),
      )}
    </ul>
  );
}

function BatchRow({
  batch,
  onSelect,
}: {
  batch: BatchGroup;
  onSelect?: (r: InspectionResult) => void;
}) {
  const ng = batch.verdict === Verdict.NG;
  // 배치 행 클릭 시 첫 NG 튜브를 재확인 대상으로.
  const firstNg = batch.tubes.find((t) => t.final_verdict === Verdict.NG);
  return (
    <li>
      <button
        type="button"
        onClick={() => firstNg && onSelect?.(firstNg)}
        className={`flex w-full items-center justify-between gap-3 rounded-lg border-2 bg-white px-4 py-3 text-left ${
          ng ? "border-ng" : "border-gray-200"
        } ${firstNg ? "active:scale-[0.99]" : "cursor-default"}`}
        data-testid="batch-feed-row"
        data-verdict={batch.verdict}
        data-total={batch.total}
        data-ng={batch.ngCount}
      >
        <span className="flex flex-col">
          <span className="text-hmi font-bold">
            {batch.item_code}
            <span className="ml-2 rounded bg-gray-800 px-2 py-0.5 text-sm font-bold text-white">
              배치 {batch.total}개
            </span>
          </span>
          <span className="text-sm text-gray-500">
            LOT {batch.lot} ·{" "}
            {new Date(batch.inspected_at).toLocaleTimeString("ko-KR")}
            {ng && (
              <span className="ml-1 font-bold text-ng-fg">
                · NG {batch.ngCount}/{batch.total}
              </span>
            )}
          </span>
        </span>
        <VerdictBadge
          verdict={batch.verdict}
          size="sm"
          label={ng ? "NG" : "OK"}
        />
      </button>
    </li>
  );
}

function SingleRow({
  result,
  onSelect,
}: {
  result: InspectionResult;
  onSelect?: (r: InspectionResult) => void;
}) {
  const ng = result.final_verdict === Verdict.NG;
  return (
    <li>
      <button
        type="button"
        onClick={() => ng && onSelect?.(result)}
        className={`flex w-full items-center justify-between gap-3 rounded-lg border-2 bg-white px-4 py-3 text-left ${
          ng ? "border-ng" : "border-gray-200"
        } ${ng ? "active:scale-[0.99]" : "cursor-default"}`}
        data-testid="feed-row"
        data-verdict={result.final_verdict}
      >
        <span className="flex flex-col">
          <span className="text-hmi font-bold">{result.item_code}</span>
          <span className="text-sm text-gray-500">
            LOT {result.lot} ·{" "}
            {new Date(result.inspected_at).toLocaleTimeString("ko-KR")}
          </span>
        </span>
        <VerdictBadge verdict={result.final_verdict} size="sm" />
      </button>
    </li>
  );
}
