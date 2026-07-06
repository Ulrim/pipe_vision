import { describe, expect, it } from "vitest";
import { groupFeed, batchKey } from "@/lib/batching";
import { Verdict } from "@aivis/shared-types";
import { makeResult, makeNg, makeBatch } from "./factories";

describe("batching.groupFeed (다중 튜브 배치 그룹핑)", () => {
  it("같은 lot+inspected_at 의 다중 tube_index 를 배치 1개로 묶는다", () => {
    // feed 는 최신순(맨 앞이 최신). 배치 튜브 4개를 역순으로 넣어 실제 수신 순서 모사.
    const tubes = makeBatch(4, { ngIdx: [1, 3] });
    const feed = [...tubes].reverse();
    const groups = groupFeed(feed);

    expect(groups).toHaveLength(1);
    const g = groups[0];
    expect(g.isBatch).toBe(true);
    expect(g.total).toBe(4);
    // tube_index 오름차순 정렬.
    expect(g.tubes.map((t) => t.tube_index)).toEqual([0, 1, 2, 3]);
  });

  it("NG 개수/OK 개수/종합 판정을 요약한다", () => {
    const feed = makeBatch(5, { ngIdx: [0, 2] });
    const g = groupFeed(feed)[0];
    expect(g.ngCount).toBe(2);
    expect(g.okCount).toBe(3);
    expect(g.verdict).toBe(Verdict.NG); // NG 튜브 하나라도 있으면 NG
  });

  it("NG 튜브가 없으면 종합 판정 OK", () => {
    const feed = makeBatch(3, { ngIdx: [] });
    const g = groupFeed(feed)[0];
    expect(g.ngCount).toBe(0);
    expect(g.verdict).toBe(Verdict.OK);
  });

  it("대표 이미지 id 는 배치 내 첫 유효 id(tube_index 최소)", () => {
    const feed = makeBatch(3, { baseId: 500 });
    const g = groupFeed(feed)[0];
    expect(g.representativeId).toBe(500); // tube_index 0 = id 500
  });

  it("단일 튜브(tube_index 없음)는 배치로 취급하지 않는다(하위호환)", () => {
    const feed = [makeResult({ id: 1 })];
    const g = groupFeed(feed)[0];
    expect(g.isBatch).toBe(false);
    expect(g.total).toBe(1);
  });

  it("tube_index 는 있으나 1건뿐이면 아직 배치가 아니다", () => {
    const feed = [makeResult({ id: 1, tube_index: 0 })];
    expect(groupFeed(feed)[0].isBatch).toBe(false);
  });

  it("늦게 도착한 tube_index 를 같은 배치에 누적한다", () => {
    // 먼저 2개 수신 → 배치. 이후 tube_index 2 추가 → 같은 그룹에 누적.
    const [t0, t1, t2] = makeBatch(3);
    const before = groupFeed([t1, t0]);
    expect(before[0].total).toBe(2);
    const after = groupFeed([t2, t1, t0]);
    expect(after).toHaveLength(1);
    expect(after[0].total).toBe(3);
    expect(after[0].tubes.map((t) => t.tube_index)).toEqual([0, 1, 2]);
  });

  it("서로 다른 배치는 분리하고 최신 배치를 앞에 둔다", () => {
    const older = makeBatch(2, {
      lot: "LOT-A",
      inspected_at: "2026-07-06T08:00:00+09:00",
      baseId: 10,
    });
    const newer = makeBatch(2, {
      lot: "LOT-B",
      inspected_at: "2026-07-06T09:00:00+09:00",
      baseId: 20,
    });
    // 최신순 feed: 새 배치가 앞.
    const feed = [...[...newer].reverse(), ...[...older].reverse()];
    const groups = groupFeed(feed);
    expect(groups).toHaveLength(2);
    expect(groups[0].lot).toBe("LOT-B");
    expect(groups[1].lot).toBe("LOT-A");
  });

  it("배치 키는 lot 과 inspected_at 을 함께 사용한다", () => {
    const a = batchKey({ lot: "L1", inspected_at: "T1" });
    const b = batchKey({ lot: "L1", inspected_at: "T2" });
    const c = batchKey({ lot: "L2", inspected_at: "T1" });
    expect(a).not.toBe(b);
    expect(a).not.toBe(c);
  });

  it("단일 NG 도 그룹으로 표현되며 요약이 맞다", () => {
    const g = groupFeed([makeNg({ id: 9 })])[0];
    expect(g.isBatch).toBe(false);
    expect(g.ngCount).toBe(1);
    expect(g.verdict).toBe(Verdict.NG);
  });
});
