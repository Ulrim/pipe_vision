import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { KpiGauge, barFill } from "./KpiGauge";
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

describe("KpiGauge — 불릿 차트(반원 게이지에서 교체)", () => {
  it("큰 값을 축약해 카드 밖으로 넘치지 않게 한다", () => {
    // 실제로 깨졌던 값: 681,818.18ppm 이 게이지 호를 뚫고 나왔다.
    render(<KpiGauge spec={{ ...spec, value: 681818.18, status: "fail" }} />);
    const card = screen.getByTestId("kpi-process_defect_ppm");
    expect(card).toHaveTextContent("682k");
    expect(card).not.toHaveTextContent("681,818.18");
  });

  it("목표를 크게 초과하면 초과 배수를 글로 알려준다", () => {
    // 막대만으로는 2배 초과와 1000배 초과를 구분할 수 없다.
    render(<KpiGauge spec={{ ...spec, value: 681818, status: "fail" }} />);
    expect(
      screen.getByTestId("kpi-overflow-process_defect_ppm"),
    ).toHaveTextContent("목표의 1,136배");
  });

  it("목표 안쪽이면 초과 표기를 하지 않는다", () => {
    render(<KpiGauge spec={{ ...spec, value: 300, status: "pass" }} />);
    expect(screen.queryByTestId("kpi-overflow-process_defect_ppm")).toBeNull();
  });

  it("목표선은 낮을수록 좋은 지표에서 항상 같은 자리에 있다", () => {
    // 카드마다 목표선 위치가 달라지면 위치로 비교할 수 없다.
    const { rerender } = render(<KpiGauge spec={{ ...spec, value: 100 }} />);
    const at = () =>
      screen.getByTestId("kpi-target-process_defect_ppm").style.left;
    const first = at();
    rerender(<KpiGauge spec={{ ...spec, value: 50_000, status: "fail" }} />);
    expect(at()).toBe(first);
  });
});

describe("barFill", () => {
  it("낮을수록 좋은 지표: 목표에서 정확히 기준 위치", () => {
    const r = barFill(600, 600, "lower");
    expect(r.ratio).toBeCloseTo(0.6);
    expect(r.overflow).toBe(false);
  });

  it("낮을수록 좋은 지표: 척도를 넘으면 가득 차고 배수를 돌려준다", () => {
    const r = barFill(6000, 600, "lower");
    expect(r.ratio).toBe(1);
    expect(r.overflow).toBe(true);
    expect(r.multiple).toBeCloseTo(10);
  });

  it("높을수록 좋은 지표: 목표 도달이 가득 참", () => {
    expect(barFill(100, 100, "higher").ratio).toBe(1);
    expect(barFill(50, 100, "higher").ratio).toBeCloseTo(0.5);
  });

  it("목표가 0이거나 값이 비정상이면 0 (0으로 나누지 않는다)", () => {
    expect(barFill(10, 0, "lower").ratio).toBe(0);
    expect(barFill(NaN, 600, "lower").ratio).toBe(0);
  });
});
