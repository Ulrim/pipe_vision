/**
 * 최근 검사 이력 목록 (M10 보조). 클릭 시 재확인(NG 대상).
 */
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";
import { VerdictBadge } from "./VerdictBadge";

export interface RecentFeedProps {
  feed: InspectionResult[];
  onSelect?: (r: InspectionResult) => void;
}

export function RecentFeed({ feed, onSelect }: RecentFeedProps) {
  if (feed.length === 0) {
    return (
      <div className="rounded-xl bg-white p-4 text-center text-gray-400">
        수신된 검사결과가 없습니다.
      </div>
    );
  }
  return (
    <ul className="flex flex-col gap-2" data-testid="recent-feed">
      {feed.map((r, i) => {
        const ng = r.final_verdict === Verdict.NG;
        return (
          <li key={r.id ?? `idx-${i}`}>
            <button
              type="button"
              onClick={() => ng && onSelect?.(r)}
              className={`flex w-full items-center justify-between gap-3 rounded-lg border-2 bg-white px-4 py-3 text-left ${
                ng ? "border-ng" : "border-gray-200"
              } ${ng ? "active:scale-[0.99]" : "cursor-default"}`}
              data-testid="feed-row"
              data-verdict={r.final_verdict}
            >
              <span className="flex flex-col">
                <span className="text-hmi font-bold">{r.item_code}</span>
                <span className="text-sm text-gray-500">
                  LOT {r.lot} ·{" "}
                  {new Date(r.inspected_at).toLocaleTimeString("ko-KR")}
                </span>
              </span>
              <VerdictBadge verdict={r.final_verdict} size="sm" />
            </button>
          </li>
        );
      })}
    </ul>
  );
}
