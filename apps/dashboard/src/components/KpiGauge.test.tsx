import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { KpiGauge } from "./KpiGauge";
import type { KpiGaugeSpec } from "@/lib/kpi";

const spec: KpiGaugeSpec = {
  key: "process_defect_ppm", label: "공정불량률", unit: "ppm",
  value: 500, target: 600, direction: "lower", status: "pass", ratio: 1,
};

describe("KpiGauge", () => {
  it("값/목표/상태(달성) 표시 + 상태 데이터 속성", () => {
    render(<KpiGauge spec={spec} />);
    const card = screen.getByTestId("kpi-process_defect_ppm");
    expect(card).toHaveAttribute("data-status", "pass");
    expect(card).toHaveTextContent("500");
    expect(card).toHaveTextContent("목표 ≤ 600ppm");
    expect(card).toHaveTextContent("달성");
  });

  it("미달 상태(fail)도 표기", () => {
    render(<KpiGauge spec={{ ...spec, value: 900, status: "fail", ratio: 0.66 }} />);
    expect(screen.getByTestId("kpi-process_defect_ppm")).toHaveAttribute("data-status", "fail");
    expect(screen.getByText("미달")).toBeInTheDocument();
  });
});
