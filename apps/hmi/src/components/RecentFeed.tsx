/**
 * 최근 검사 이력 — 하단 압축 스트립 (M10 보조).
 *
 * 설계 근거(파이 7" 800x480 실측 후 재설계):
 * - 이전에는 우측 세로 목록이라 판정 영역을 좁혔고, 480px 화면에서는 잘려서
 *   보이지도 않았다. 작업자에게 필요한 건 **흐름**("방금 몇 개나 나갔고 불량이
 *   몰리고 있나")이지 개별 행의 상세가 아니다. 상세는 관리자 웹의 일이다.
 * - 그래서 하단 한 줄(약 56px)로 압축한다:
 *      누적 카운터(검사 n · 불량 m)  +  최근 결과 타일(최신이 왼쪽)
 * - 타일은 색 단독이 아니라 ✓/✕ 기호를 함께 쓴다(색약 고려). NG 타일은
 *   눌러서 재확인할 수 있고 터치 타겟은 44px 이상.
 * - **색 규칙(고성능 HMI)**: 양품 타일은 무채색, NG 타일만 빨강. 그래야
 *   조용한 회색 줄에서 빨강이 튀어 불량이 몰리는 구간이 한눈에 보인다.
 */
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";
import type { BatchGroup } from "@/lib/batching";

/** 화면 폭에 들어가는 만큼만(넘치면 가로 스크롤 대신 잘라낸다). */
const MAX_TILES = 12;

export interface RecentFeedProps {
  batches: BatchGroup[];
  onSelect?: (r: InspectionResult) => void;
}

export function RecentFeed({ batches, onSelect }: RecentFeedProps) {
  // 누적 집계: 이 화면이 켜진 뒤 수신한 전체(튜브 단위).
  let total = 0;
  let ng = 0;
  for (const b of batches) {
    total += b.total;
    ng += b.ngCount;
  }

  return (
    <footer
      className="flex flex-none items-center gap-3 border-t-2 border-gray-300 bg-white px-3 py-2"
      data-testid="recent-feed"
    >
      <div className="flex flex-none items-baseline gap-2">
        <span className="text-hmi-cap font-semibold text-gray-500">누적</span>
        <span className="text-hmi-body font-black tabular-nums text-gray-900">
          {total}
        </span>
        <span className="text-hmi-cap font-semibold text-gray-500">불량</span>
        <span
          className={`text-hmi-body font-black tabular-nums ${
            ng > 0 ? "text-ng-fg" : "text-gray-400"
          }`}
          data-testid="recent-ng-count"
        >
          {ng}
        </span>
      </div>

      {batches.length === 0 ? (
        <span className="text-hmi-cap text-gray-400">
          수신된 검사결과가 없습니다.
        </span>
      ) : (
        <ul className="flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden">
          {batches.slice(0, MAX_TILES).map((b, i) => (
            <BatchTile
              key={b.key ?? `b-${i}`}
              batch={b}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </footer>
  );
}

function BatchTile({
  batch,
  onSelect,
}: {
  batch: BatchGroup;
  onSelect?: (r: InspectionResult) => void;
}) {
  const isNg = batch.verdict === Verdict.NG;
  // NG 배치는 첫 NG 튜브를 재확인 대상으로 넘긴다.
  const target = isNg
    ? (batch.tubes.find((t) => t.final_verdict === Verdict.NG) ?? batch.tubes[0])
    : null;
  const clickable = !!target && !!onSelect;
  const time = new Date(batch.inspected_at).toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <li className="flex-none">
      <button
        type="button"
        disabled={!clickable}
        onClick={() => clickable && onSelect?.(target)}
        data-testid={batch.isBatch ? "batch-feed-row" : "feed-row"}
        data-verdict={batch.verdict}
        aria-label={`${time} ${isNg ? `불량 ${batch.ngCount}개` : "양품"}`}
        className={`flex h-11 items-center gap-1 rounded-lg border-2 px-2 ${
          isNg
            ? "border-ng bg-ng text-white"
            : "border-gray-300 bg-white text-gray-500"
        } ${clickable ? "active:scale-95" : "cursor-default"}`}
      >
        <span aria-hidden className="text-hmi-cap font-black">
          {isNg ? "✕" : "✓"}
        </span>
        {batch.isBatch && (
          <span className="text-hmi-cap font-black tabular-nums">
            {isNg ? batch.ngCount : batch.total}
          </span>
        )}
        <span className="text-hmi-cap font-semibold tabular-nums opacity-80">
          {time}
        </span>
      </button>
    </li>
  );
}
