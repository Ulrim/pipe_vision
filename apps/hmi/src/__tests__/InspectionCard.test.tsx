import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { InspectionCard } from "@/components/InspectionCard";
import { makeResult, makeNg } from "./factories";

describe("InspectionCard (M10 색+아이콘 이중표기)", () => {
  it("결과 없으면 대기 메시지를 보여준다", () => {
    render(<InspectionCard result={null} />);
    expect(screen.getByTestId("inspection-card-empty")).toBeInTheDocument();
  });

  it("OK 결과를 길이값과 함께 렌더하고 OK 아이콘(✓)을 표기한다", () => {
    render(<InspectionCard result={makeResult({ id: 1 })} />);
    const card = screen.getByTestId("inspection-card");
    expect(card).toHaveAttribute("data-verdict", "OK");
    // 색약 고려: 체크 아이콘 + OK 텍스트.
    expect(screen.getByText("✓")).toBeInTheDocument();
    // 측정값은 보조로 내렸다 — 작업자가 판단에 쓰는 값은 편차이기 때문이다.
    expect(screen.getByText(/측정\s*250\.10/)).toBeInTheDocument();
  });

  it("NG 결과에 X 아이콘 + 불량유형 뱃지 + 재확인 버튼을 보여준다", () => {
    render(<InspectionCard result={makeNg({ id: 2 })} onReview={() => {}} />);
    expect(screen.getByText("✕")).toBeInTheDocument();
    expect(screen.getByTestId("defect-badges")).toBeInTheDocument();
    expect(screen.getByTestId("open-review")).toBeInTheDocument();
  });
});

describe("InspectionCard — 공차 밴드와 이미지 확대", () => {
  it("편차를 측정값보다 크게 보여준다(판단 근거가 주인공)", () => {
    render(
      <InspectionCard result={makeNg({ id: 3, deviation_mm: 1.5 })} />,
    );
    // 편차 타일은 큰 활자(text-hmi-lg), 측정은 캡션 크기.
    const dev = screen.getByText("+1.50").closest("div");
    expect(dev?.className).toContain("text-hmi-lg");
  });

  it("공차를 주면 밴드를 그리고, 벗어났는지 표시한다", () => {
    render(
      <InspectionCard
        result={makeNg({ id: 4, deviation_mm: 19.75 })}
        tolPlusMm={0.5}
        tolMinusMm={0.5}
      />,
    );
    const gauge = screen.getByTestId("tolerance-gauge");
    expect(gauge).toHaveAttribute("data-within", "no");
  });

  it("공차 안이면 밴드가 '안쪽'으로 표시된다", () => {
    render(
      <InspectionCard
        result={makeResult({ id: 5, deviation_mm: 0.1 })}
        tolPlusMm={0.5}
        tolMinusMm={0.5}
      />,
    );
    expect(screen.getByTestId("tolerance-gauge")).toHaveAttribute(
      "data-within",
      "yes",
    );
  });

  it("공차 정보가 없으면 밴드를 그리지 않는다(추정값으로 그리면 오독)", () => {
    render(<InspectionCard result={makeResult({ id: 6 })} />);
    expect(screen.queryByTestId("tolerance-gauge")).toBeNull();
  });

  it("판정 이미지를 눌러 크게 볼 수 있다(7인치에서는 작아서 안 보인다)", () => {
    const onZoom = vi.fn();
    render(
      <InspectionCard result={makeResult({ id: 7 })} onZoomImage={onZoom} />,
    );
    screen.getByTestId("zoom-image").click();
    expect(onZoom).toHaveBeenCalled();
  });
});
