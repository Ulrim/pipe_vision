import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ItemMaster } from "@aivis/shared-types";
import { OrderSetup, validateSpec } from "@/components/OrderSetup";
import { appendKey, stepValue } from "@/components/NumPad";

const ITEM: ItemMaster = {
  item_code: "HP12",
  item_name: "헤더파이프",
  ref_length_mm: 250,
  tol_plus_mm: 0.3,
  tol_minus_mm: 0.3,
  px_to_mm_scale: 0.25,
  oil_threshold: null,
  discolor_threshold: null,
  scratch_threshold: null,
  capture_recipe: null,
  expected_count: 12,
  outer_diameter_mm: null,
  version: 1,
  updated_by: null,
  updated_at: null,
};

function stubFetch(ok = true) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      ok
        ? new Response(JSON.stringify({ ...ITEM, ref_length_mm: 300, version: 2 }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          })
        : new Response(JSON.stringify({ detail: "권한 없음" }), { status: 403 }),
    ),
  );
}

describe("NumPad 입력 규칙", () => {
  it("소수점은 하나만, 맨 앞이면 0. 으로 시작한다", () => {
    expect(appendKey("", ".")).toBe("0.");
    expect(appendKey("0.5", ".")).toBe("0.5");
  });

  it("선행 0 뒤 숫자는 치환한다(05 방지)", () => {
    expect(appendKey("0", "7")).toBe("7");
  });

  it("소수 자리수를 제한한다", () => {
    expect(appendKey("1.234", "5", 3)).toBe("1.234");
    expect(appendKey("1.2", "5", 3)).toBe("1.25");
  });

  it("증감은 0 아래로 내려가지 않는다", () => {
    expect(stepValue("0.1", -0.1)).toBe("0");
    expect(stepValue("0", -1)).toBe("0");
    expect(stepValue("250", 1)).toBe("251");
  });
});

describe("사양 검증 — 서버와 같은 규칙", () => {
  it("공차 양쪽 0 은 막는다(전수 불량이 된다)", () => {
    expect(validateSpec(250, 0, 0, 1)).toMatch(/전부 불량/);
  });
  it("공차가 길이보다 크면 막는다(자릿수 오타)", () => {
    expect(validateSpec(250, 300, 0.3, 1)).toMatch(/자릿수/);
  });
  it("길이 0 이하를 막는다", () => {
    expect(validateSpec(0, 0.3, 0.3, 1)).toMatch(/기준 길이/);
  });
  it("정상값은 통과한다", () => {
    expect(validateSpec(250, 0.3, 0.3, 12)).toBeNull();
  });
});

describe("OrderSetup 화면", () => {
  beforeEach(() => stubFetch());
  afterEach(() => vi.unstubAllGlobals());

  it("현재 값과 합격 범위를 보여준다", () => {
    render(<OrderSetup item={ITEM} onClose={() => {}} />);
    expect(screen.getByTestId("spec-field-len")).toHaveTextContent("250");
    expect(screen.getByTestId("pass-band")).toHaveTextContent("249.70 ~ 250.30");
  });

  it("키패드 입력이 선택한 칸에 들어가고 합격 범위가 즉시 바뀐다", () => {
    render(<OrderSetup item={ITEM} onClose={() => {}} />);
    fireEvent.click(screen.getByTestId("numpad-clear"));
    for (const k of ["3", "0", "0"]) {
      fireEvent.click(screen.getByTestId(`numpad-key-${k}`));
    }
    expect(screen.getByTestId("spec-field-len")).toHaveTextContent("300");
    expect(screen.getByTestId("pass-band")).toHaveTextContent("299.70 ~ 300.30");
  });

  it("공차 양쪽 0 이면 오류를 띄우고 저장을 막는다", () => {
    render(<OrderSetup item={ITEM} onClose={() => {}} />);
    for (const f of ["plus", "minus"]) {
      fireEvent.click(screen.getByTestId(`spec-field-${f}`));
      fireEvent.click(screen.getByTestId("numpad-clear"));
      fireEvent.click(screen.getByTestId("numpad-key-0"));
    }
    expect(screen.getByTestId("spec-error")).toHaveTextContent("전부 불량");
    expect(screen.getByTestId("spec-review")).toBeDisabled();
  });

  it("변경이 없으면 확인 버튼이 잠겨 있다", () => {
    render(<OrderSetup item={ITEM} onClose={() => {}} />);
    expect(screen.getByTestId("spec-review")).toBeDisabled();
    expect(screen.getByTestId("spec-review")).toHaveTextContent("변경 없음");
  });

  it("저장 전에 이전값 → 새값을 보여준다", () => {
    render(<OrderSetup item={ITEM} onClose={() => {}} />);
    fireEvent.click(screen.getByTestId("numpad-inc")); // 250 → 251
    fireEvent.click(screen.getByTestId("spec-review"));
    const diff = screen.getByTestId("spec-diff");
    expect(diff).toHaveTextContent("250");
    expect(diff).toHaveTextContent("251");
  });

  it("확인 후 저장하면 서버에 보내고 닫는다", async () => {
    const onClose = vi.fn();
    const onSaved = vi.fn();
    render(<OrderSetup item={ITEM} onClose={onClose} onSaved={onSaved} />);
    fireEvent.click(screen.getByTestId("numpad-inc"));
    fireEvent.click(screen.getByTestId("spec-review"));
    fireEvent.click(screen.getByTestId("spec-save"));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onSaved).toHaveBeenCalled();
    const [url, init] = (globalThis.fetch as any).mock.calls[0];
    expect(String(url)).toContain("/master/items/HP12/spec");
    expect(init.method).toBe("PUT");
    const sent = JSON.parse(init.body);
    // 보정계수·표면 임계는 **보내지 않는다** — 라인에서 흔들릴 수 없어야 한다.
    expect(Object.keys(sent).sort()).toEqual(
      ["expected_count", "ref_length_mm", "tol_minus_mm", "tol_plus_mm"],
    );
  });

  it("저장 실패하면 화면을 닫지 않고 이유를 보여준다", async () => {
    vi.unstubAllGlobals();
    stubFetch(false);
    const onClose = vi.fn();
    render(<OrderSetup item={ITEM} onClose={onClose} />);
    fireEvent.click(screen.getByTestId("numpad-inc"));
    fireEvent.click(screen.getByTestId("spec-review"));
    fireEvent.click(screen.getByTestId("spec-save"));

    await waitFor(() => expect(screen.getByTestId("save-error")).toBeInTheDocument());
    expect(onClose).not.toHaveBeenCalled();
  });
});
