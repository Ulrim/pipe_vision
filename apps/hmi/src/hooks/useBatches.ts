/**
 * 실시간 feed → 배치 그룹 파생 훅 (CLAUDE.md §5 M6/M10).
 *
 * store 는 평면 feed(InspectionResult[]) 를 최신순으로 유지한다. 이 훅은 그
 * 평면 목록을 배치 키(lot+inspected_at)로 묶어 BatchGroup[] 로 파생한다.
 * feed 참조가 바뀔 때만 재계산(useMemo)하므로 렌더 비용을 억제한다.
 */
import { useMemo } from "react";
import { useLiveStore } from "@/store/liveStore";
import { groupFeed, type BatchGroup } from "@/lib/batching";

/** 최신순 배치 그룹 목록(최신 배치가 맨 앞). */
export function useBatches(): BatchGroup[] {
  const feed = useLiveStore((s) => s.feed);
  return useMemo(() => groupFeed(feed), [feed]);
}

/** 최신(맨 앞) 배치 그룹. 없으면 null. */
export function useLatestBatch(): BatchGroup | null {
  const batches = useBatches();
  return batches[0] ?? null;
}
