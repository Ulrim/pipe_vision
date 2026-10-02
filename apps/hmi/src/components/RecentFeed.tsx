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
 *
 * **타일에 시각을 찍지 않는다.** 전에는 모든 타일이 `✕ 오전 12:40` 처럼 같은
 * 분을 반복해서, 12칸이 사실상 같은 글자였다 — 480px 화면의 귀한 한 줄을
 * 쓰면서 아무것도 알려주지 않았다. 작업자가 이 줄에서 읽어야 하는 것은
 * "불량이 몰리는가, 어떤 불량이 반복되는가" 다. 그래서 NG 타일에는 **불량유형**
 * (LEN/OIL/DIS/SCR)을 찍고 양품 타일은 기호만 남겨 좁힌다. 그러면
 * `✓ ✓ ✓ ✕LEN ✕LEN ✕LEN ✓` 처럼 **길이 쪽으로 쏠리기 시작했다**는 신호가
 * 한눈에 보인다. 정확한 시각은 읽을 수 있게 aria-label 에만 남긴다.
 */
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";
import type { BatchGroup } from "@/lib/batching";
import { RECENT_BATCHES } from "@/store/liveStore";

/** 화면 폭에 들어가는 만큼만(넘치면 가로 스크롤 대신 잘라낸다).
 *  타일에서 시각을 빼 좁아진 만큼 더 많은 이력을 보여준다 — 패턴을 읽으려면
 *  최근 몇 개가 아니라 흐름이 보여야 한다.
 *
 *  liveStore 의 보존 건수가 이 값에 맞춰져 있다(RECENT_BATCHES × 배치 최대
 *  크기). 두 값이 어긋나면 타일 자리는 18칸인데 데이터가 2칸치만 남는 식이
 *  된다. 그래서 같은 상수를 쓴다. */
const MAX_TILES = RECENT_BATCHES;

/** NG 배치의 대표 불량유형. 여러 개면 가장 많이 나온 코드(반복되는 문제). */
export function dominantDefect(batch: BatchGroup): string | null {
  const counts = new Map<string, number>();
  for (const t of batch.tubes) {
    for (const c of t.defect_codes ?? []) {
      if (c === "MULTI") continue; // 복합 표식은 유형이 아니다
      counts.set(c, (counts.get(c) ?? 0) + 1);
    }
  }
  let best: string | null = null;
  let bestN = 0;
  for (const [code, n] of counts) {
    if (n > bestN) {
      best = code;
      bestN = n;
    }
  }
  return best;
}

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
    second: "2-digit",
  });
  const defect = isNg ? dominantDefect(batch) : null;

  return (
    <li className="flex-none">
      <button
        type="button"
        disabled={!clickable}
        onClick={() => clickable && onSelect?.(target)}
        data-testid={batch.isBatch ? "batch-feed-row" : "feed-row"}
        data-verdict={batch.verdict}
        aria-label={`${time} ${
          isNg ? `불량 ${batch.ngCount}개${defect ? ` ${defect}` : ""}` : "양품"
        }`}
        title={time}
        className={`flex h-11 items-center gap-1 rounded-lg border-2 px-1.5 ${
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
        {/* 불량유형만 적는다 — 반복되는 유형이 보여야 공정 쏠림을 읽는다. */}
        {defect && (
          <span className="text-hmi-cap font-bold" data-testid="feed-defect">
            {defect}
          </span>
        )}
      </button>
    </li>
  );
}
