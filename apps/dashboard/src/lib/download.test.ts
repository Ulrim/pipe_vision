import { describe, expect, it, vi } from "vitest";
import { rowsToCsv, triggerBlobDownload } from "./download";

describe("rowsToCsv", () => {
  it("헤더 + 행, 배열은 | 결합, 특수문자 이스케이프", () => {
    const csv = rowsToCsv(
      [
        { id: 1, lot: "L1", defect_codes: ["LEN", "OIL"] },
        { id: 2, lot: 'a,b"c', defect_codes: [] },
      ],
      [
        { key: "id", header: "id" },
        { key: "lot", header: "lot" },
        { key: "defect_codes", header: "defect_codes" },
      ],
    );
    const lines = csv.trim().split("\n");
    expect(lines[0]).toBe("id,lot,defect_codes");
    expect(lines[1]).toBe("1,L1,LEN|OIL");
    expect(lines[2]).toBe('2,"a,b""c",');
  });
});

describe("triggerBlobDownload", () => {
  it("a[download] 클릭으로 다운로드 트리거", () => {
    const click = vi.fn();
    const orig = document.createElement.bind(document);
    const spy = vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = orig(tag) as HTMLAnchorElement;
      if (tag === "a") el.click = click;
      return el;
    });
    triggerBlobDownload(new Blob(["x"], { type: "text/csv" }), "out.csv");
    expect(click).toHaveBeenCalledTimes(1);
    spy.mockRestore();
  });
});
