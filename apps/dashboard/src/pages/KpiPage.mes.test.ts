import { describe, expect, it } from "vitest";
import { mesModeLabel } from "./KpiPage";

describe("mesModeLabel — 무엇으로 연계를 셌는지", () => {
  it("가짜 전송은 증빙이 아니라고 적는다", () => {
    expect(mesModeLabel("rest_fake")).toContain("증빙 아님");
    expect(mesModeLabel("table")).toBe("DB 테이블 적재");
    expect(mesModeLabel("rest")).toContain("실제 MES");
    expect(mesModeLabel("rest_unconfigured")).toContain("미설정");
    expect(mesModeLabel(null)).toBe("-");
  });
});
