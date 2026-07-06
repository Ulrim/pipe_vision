/**
 * 다중 튜브 배치 그룹핑 (CLAUDE.md §5 M6/M10, 부록 A.1 다중 튜브 오더).
 *
 * 배경: 다중 튜브 오더에서 워커는 한 번 촬영(배치)마다 튜브 N개를 각각
 * InspectionResult 로 POST 하고 WS 로 실시간 푸시한다. 같은 배치의 튜브들은
 *   - lot / inspected_at 이 동일하고
 *   - tube_index(0..N-1) 로 구분되며
 *   - 동일한 result 오버레이 이미지(모든 튜브가 박스로 표기됨)를 공유한다.
 *
 * 이 모듈은 store 의 평면 feed(InspectionResult[]) 를 배치 키(lot+inspected_at)로
 * 묶어 BatchGroup[] 로 변환한다. store 는 그대로 평면 목록을 유지하므로
 * 연속 NG 카운트/재확인(applyReview) 등 기존 로직은 영향받지 않는다.
 *
 * 하위호환: tube_index 가 없거나 같은 키의 결과가 1건뿐이면 "단일 튜브"로 취급
 * (isBatch=false) → 기존 단일 카드/피드행 렌더를 유지한다.
 */
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";

/** 배치 자연키 = lot + inspected_at (§7.1 자연키 구성요소). */
export function batchKey(
  r: Pick<InspectionResult, "lot" | "inspected_at">,
): string {
  return `${r.lot} ${r.inspected_at}`;
}

/** 배치(또는 단일) 그룹 — feed 를 배치 키로 묶은 결과. */
export interface BatchGroup {
  /** 배치 자연키(lot+inspected_at). */
  key: string;
  lot: string;
  inspected_at: string;
  item_code: string;
  cam_id: string;
  /** tube_index 오름차순(없으면 id 순) 정렬된 튜브 목록. */
  tubes: InspectionResult[];
  /** 튜브 2건 이상 + tube_index 존재 → 배치. 아니면 단일(하위호환). */
  isBatch: boolean;
  /** 총 검출(튜브) 수. */
  total: number;
  /** OK 튜브 수. */
  okCount: number;
  /** NG 튜브 수. */
  ngCount: number;
  /** 배치 종합 판정: NG 튜브가 하나라도 있으면 NG. */
  verdict: Verdict;
  /** 대표 이미지용 id(모든 튜브가 공유하는 배치 오버레이). 첫 유효 id. */
  representativeId: number | null;
}

function tubeOrder(a: InspectionResult, b: InspectionResult): number {
  const ai = a.tube_index ?? Number.POSITIVE_INFINITY;
  const bi = b.tube_index ?? Number.POSITIVE_INFINITY;
  if (ai !== bi) return ai - bi;
  return (a.id ?? 0) - (b.id ?? 0);
}

function toGroup(key: string, tubes: InspectionResult[]): BatchGroup {
  const sorted = [...tubes].sort(tubeOrder);
  const first = sorted[0];
  const ngCount = sorted.filter(
    (t) => t.final_verdict === Verdict.NG,
  ).length;
  const okCount = sorted.length - ngCount;
  const hasTubeIndex = sorted.some((t) => typeof t.tube_index === "number");
  const representativeId =
    sorted.find((t) => t.id !== null && t.id !== undefined)?.id ?? null;
  return {
    key,
    lot: first.lot,
    inspected_at: first.inspected_at,
    item_code: first.item_code,
    cam_id: first.cam_id,
    tubes: sorted,
    isBatch: sorted.length >= 2 && hasTubeIndex,
    total: sorted.length,
    okCount,
    ngCount,
    verdict: ngCount > 0 ? Verdict.NG : Verdict.OK,
    representativeId,
  };
}

/**
 * 평면 feed(최신순 가정)를 배치 키로 그룹핑한다.
 * - 그룹 순서: feed 에서 처음 등장한 순서 = 최신 배치가 위(feed 가 최신순이므로).
 * - 늦게 도착한 tube_index 는 같은 키 그룹에 자연 누적된다.
 */
export function groupFeed(feed: InspectionResult[]): BatchGroup[] {
  const map = new Map<string, InspectionResult[]>();
  const order: string[] = [];
  for (const r of feed) {
    const key = batchKey(r);
    let bucket = map.get(key);
    if (!bucket) {
      bucket = [];
      map.set(key, bucket);
      order.push(key);
    }
    bucket.push(r);
  }
  return order.map((key) => toGroup(key, map.get(key)!));
}
