import { describe, expect, it } from "vitest";
import { parseFilename, toQuery } from "./client";

describe("toQuery", () => {
  it("빈/undefined/null 값 제외", () => {
    expect(toQuery({ lot: "L1", item: "", verdict: undefined, offset: 0 }))
      .toBe("?lot=L1&offset=0");
  });
  it("값 없으면 빈 문자열", () => {
    expect(toQuery({ a: "", b: null })).toBe("");
  });
});

describe("parseFilename", () => {
  it("filename* (RFC5987) 우선", () => {
    expect(parseFilename("attachment; filename*=UTF-8''report%20.pdf"))
      .toBe("report .pdf");
  });
  it("일반 filename fallback", () => {
    expect(parseFilename('attachment; filename="r.xlsx"')).toBe("r.xlsx");
  });
});
