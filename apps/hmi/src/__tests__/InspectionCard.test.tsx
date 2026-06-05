import { describe, expect, it } from "vitest";
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
    expect(screen.getByText(/250.10 mm/)).toBeInTheDocument();
  });

  it("NG 결과에 X 아이콘 + 불량유형 뱃지 + 재확인 버튼을 보여준다", () => {
    render(<InspectionCard result={makeNg({ id: 2 })} onReview={() => {}} />);
    expect(screen.getByText("✕")).toBeInTheDocument();
    expect(screen.getByTestId("defect-badges")).toBeInTheDocument();
    expect(screen.getByTestId("open-review")).toBeInTheDocument();
  });
});
