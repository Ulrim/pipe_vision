import { describe, expect, it } from "vitest";
import {
  todayKst,
  equipStateView,
  fmtDuration,
  fmtQty,
  fmtSignedQty,
  itemTypeLabel,
  needsReason,
  opStatusView,
  parseQty,
  routingSourceView,
  secondsSince,
  txnTypeLabel,
} from "./mesPlant";

describe("lib/mesPlant — 표기", () => {
  it("공정·설비 상태는 기호 + 한국어, 모르는 값은 원문", () => {
    expect(opStatusView("RUNNING")).toMatchObject({ symbol: "▶", text: "진행 중" });
    expect(opStatusView("SKIPPED").text).toBe("건너뜀");
    expect(opStatusView("HOLD")).toMatchObject({ symbol: "?", text: "HOLD" });
    expect(["RUN", "IDLE", "DOWN", "PM"].map((s) => equipStateView(s).text)).toEqual([
      "가동", "대기", "고장", "점검",
    ]);
    // 네 상태의 기호가 서로 달라야 색 없이도 구분된다.
    expect(new Set(["RUN", "IDLE", "DOWN", "PM"].map((s) => equipStateView(s).symbol)).size).toBe(4);
    expect(equipStateView(null).text).toBe("기록 없음");
  });

  it("고장·점검만 사유 필수", () => {
    expect(["RUN", "IDLE", "DOWN", "PM"].filter(needsReason)).toEqual(["DOWN", "PM"]);
  });

  it("품목 유형·수불 유형 라벨", () => {
    expect(["RAW", "FG", "SUB", "X", null].map(itemTypeLabel)).toEqual([
      "원자재", "제품", "부자재", "X", "-",
    ]);
    expect(["RECEIPT", "ISSUE", "PRODUCE", "SHIP", "ADJUST", "MOVE"].map(txnTypeLabel)).toEqual([
      "입고", "투입", "생산", "출하", "조정", "MOVE",
    ]);
  });

  it("라우팅 출처", () => {
    expect(routingSourceView("item", "HP12").text).toBe("이 품목 전용 라우팅");
    expect(routingSourceView("item", "*").text).toBe("공통 라우팅");
    expect(routingSourceView("default", "HP12").text).toContain("공통 라우팅(*)");
    expect(routingSourceView("processes", "HP12").text).toContain("공정 전부");
  });

  it("수량: 부호 표시", () => {
    expect(fmtQty(127.5)).toBe("127.5");
    expect(fmtQty(1200)).toBe("1,200");
    expect(fmtQty(null)).toBe("-");
    expect(fmtSignedQty(200)).toBe("+200");
    expect(fmtSignedQty(-3.5)).toBe("−3.5");
    expect(fmtSignedQty(0)).toBe("0");
  });

  it("시간 길이", () => {
    expect(fmtDuration(40)).toBe("40초");
    expect(fmtDuration(1800)).toBe("30분");
    expect(fmtDuration(7500)).toBe("2시간 5분");
    expect(fmtDuration(21600)).toBe("6시간");
    expect(fmtDuration(90000)).toBe("1일 1시간");
    expect(fmtDuration(null)).toBe("-");
    expect(secondsSince("2026-11-15T00:00:00Z", "2026-11-15T02:05:00Z")).toBe(7500);
    expect(secondsSince("2026-11-15T00:00:00Z", Date.parse("2026-11-15T00:01:00Z"))).toBe(60);
    expect(secondsSince(null)).toBeNull();
  });

  it("parseQty: 범위 밖·빈 값은 null", () => {
    expect(parseQty("0.25", { min: 0, exclusiveMin: true })).toBe(0.25);
    expect(parseQty("0", { min: 0, exclusiveMin: true })).toBeNull();
    expect(parseQty("0", { min: 0 })).toBe(0);
    expect(parseQty("100", { min: 0, max: 100, exclusiveMax: true })).toBeNull();
    expect(parseQty("-3.5")).toBe(-3.5);
    expect(parseQty("")).toBeNull();
    expect(parseQty("abc")).toBeNull();
  });
  it("todayKst — 브라우저 시간대와 무관한 KST 날짜", () => {
    expect(todayKst(new Date("2026-11-15T14:59:59Z"))).toBe("2026-11-15");
    expect(todayKst(new Date("2026-11-15T15:00:00Z"))).toBe("2026-11-16");
  });
});
