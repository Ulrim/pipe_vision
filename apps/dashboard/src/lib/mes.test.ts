import { describe, expect, it } from "vitest";
import type { IntakeStatus } from "@/api/mes";
import {
  claimStatusView,
  defectLabel,
  goodSourceLabel,
  isPendingStale,
  parseCount,
  sortedDefects,
  todayIso,
  woStatusView,
} from "./mes";

describe("goodSourceLabel — 양품 숫자의 출처", () => {
  it("알려진 출처", () => {
    expect(goodSourceLabel("crate")).toBe("크레이트 개수 확인 합계");
    expect(goodSourceLabel("stage:CUT_LENGTH")).toBe("길이 검사 양품");
    expect(goodSourceLabel("stage:POST_WASH_SURFACE")).toBe("표면 검사 양품");
    expect(goodSourceLabel("none")).toBe("아직 검사 결과 없음");
  });
  it("모르는 단계는 숨기지 않고 원문", () => {
    expect(goodSourceLabel("stage:UNKNOWN")).toBe("UNKNOWN 단계 양품");
  });
});

describe("상태 배지 — 색 + 기호 + 한국어", () => {
  it("작업지시", () => {
    expect(woStatusView("RUNNING")).toMatchObject({ symbol: "▶", text: "진행 중" });
    expect(woStatusView("DONE")).toMatchObject({ symbol: "✓", text: "완료", tone: "ok" });
    expect(woStatusView("WEIRD").text).toBe("WEIRD");
  });
  it("미결 클레임은 주의 색", () => {
    expect(claimStatusView("OPEN").tone).toBe("warn");
  });
});

describe("불량유형", () => {
  it("코드표 순으로, 0건은 빼고", () => {
    expect(sortedDefects({ SCR: 1, MANUAL: 2, LEN: 3, OIL: 0, ZZZ: 1 })).toEqual([
      ["LEN", 3],
      ["SCR", 1],
      ["MANUAL", 2],
      ["ZZZ", 1],
    ]);
    expect(defectLabel("MANUAL")).toBe("재확인 NG");
    expect(defectLabel(null)).toBe("-");
  });
});

function status(over: Partial<IntakeStatus>): IntakeStatus {
  return {
    table_enabled: true,
    poller_running: true,
    poll_interval_s: 10,
    last_poll_at: null,
    last_result: null,
    interface_pending: 0,
    interface_error: null,
    rest_token_required: false,
    records_total: 0,
    records_today: 0,
    last_received_at: null,
    runs: [],
    ...over,
  };
}

describe("isPendingStale — 쌓인 건 자체가 아니라 폴링이 멈춘 것만 경고", () => {
  const now = Date.parse("2026-10-14T03:00:00Z");
  it("쌓여 있어도 최근에 가져갔으면 정상", () => {
    expect(
      isPendingStale(status({ interface_pending: 5, last_poll_at: "2026-10-14T02:59:55Z" }), now),
    ).toBe(false);
  });
  it("쌓여 있는데 주기의 2배(20초)보다 오래됐으면 경고", () => {
    expect(
      isPendingStale(status({ interface_pending: 5, last_poll_at: "2026-10-14T02:59:30Z" }), now),
    ).toBe(true);
  });
  it("한 번도 안 가져갔으면 경고, 쌓인 게 없으면 경고 없음", () => {
    expect(isPendingStale(status({ interface_pending: 1, last_poll_at: null }), now)).toBe(true);
    expect(isPendingStale(status({ interface_pending: 0, last_poll_at: null }), now)).toBe(false);
    expect(isPendingStale(status({ interface_pending: null }), now)).toBe(false);
  });
});

describe("입력 보조", () => {
  it("parseCount — 정수만, 최솟값 이상", () => {
    expect(parseCount("3")).toBe(3);
    expect(parseCount("0")).toBeNull();
    expect(parseCount("0", 0)).toBe(0);
    expect(parseCount("1.5")).toBeNull();
    expect(parseCount("")).toBeNull();
  });
  it("todayIso — 현지 날짜", () => {
    expect(todayIso(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
  });
});
