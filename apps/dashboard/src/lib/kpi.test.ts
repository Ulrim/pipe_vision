import { describe, expect, it } from "vitest";
import type { KpiSummary } from "@aivis/shared-types";
import { buildKpiGauges, gaugeRatio, kpiStatus, procTimeSpec } from "./kpi";

describe("gaugeRatio", () => {
  it("lower-is-better: 목표 이하면 1, 초과하면 비율 하락", () => {
    expect(gaugeRatio(600, 600, "lower")).toBe(1); // 정확히 목표
    expect(gaugeRatio(300, 600, "lower")).toBe(1); // 목표보다 좋음 → 클램프 1
    expect(gaugeRatio(1200, 600, "lower")).toBe(0.5); // 2배 초과 → 0.5
    expect(gaugeRatio(0, 600, "lower")).toBe(1); // 완벽
  });
  it("higher-is-better: value/target, 클램프 0~1", () => {
    expect(gaugeRatio(100, 100, "higher")).toBe(1);
    expect(gaugeRatio(80, 100, "higher")).toBe(0.8);
    expect(gaugeRatio(120, 100, "higher")).toBe(1); // 초과 클램프
  });
  it("비유한 입력은 0", () => {
    expect(gaugeRatio(NaN, 100, "higher")).toBe(0);
  });
});

describe("kpiStatus", () => {
  it("공정불량률 목표 600ppm 이하면 pass", () => {
    expect(kpiStatus(550, 600, "lower")).toBe("pass");
    expect(kpiStatus(600, 600, "lower")).toBe("pass");
  });
  it("목표 10% 이내 초과면 warn, 그 이상이면 fail", () => {
    expect(kpiStatus(650, 600, "lower")).toBe("warn"); // +50 ≤ 60(10%)
    expect(kpiStatus(700, 600, "lower")).toBe("fail"); // +100 > 60
  });
  it("자동검사율 100% 목표(higher)", () => {
    expect(kpiStatus(100, 100, "higher")).toBe("pass");
    expect(kpiStatus(95, 100, "higher")).toBe("warn"); // -5 ≤ 10
    expect(kpiStatus(80, 100, "higher")).toBe("fail"); // -20 > 10
  });
});

const summary: KpiSummary = {
  period: "2026-06",
  total_inspected: 10000,
  defect_count: 5,
  process_defect_ppm: 500, // 목표 600 → pass
  auto_inspected: 10000,
  auto_inspection_rate_pct: 100, // pass
  misjudge_count: 1,
  miss_count: 1,
  inspection_defect_rate_pct: 0.02, // 목표 30 → pass
  stored_count: 10000,
  mes_synced_count: 10000,
  storage_mes_rate_pct: 100, // pass
  avg_proc_time_ms: 250,
};

describe("buildKpiGauges", () => {
  it("§1.1 4종 게이지를 목표값과 함께 생성", () => {
    const g = buildKpiGauges(summary);
    expect(g.map((x) => x.key)).toEqual([
      "process_defect_ppm",
      "inspection_defect_rate_pct",
      "auto_inspection_rate_pct",
      "storage_mes_rate_pct",
    ]);
    const ppm = g[0];
    expect(ppm.target).toBe(600);
    expect(ppm.direction).toBe("lower");
    expect(ppm.status).toBe("pass");
    expect(g.every((x) => x.status === "pass")).toBe(true);
  });
  it("procTimeSpec: 목표 300ms 이하면 pass, null 안전", () => {
    expect(procTimeSpec(summary)?.status).toBe("pass");
    expect(procTimeSpec({ ...summary, avg_proc_time_ms: null })).toBeNull();
    expect(procTimeSpec({ ...summary, avg_proc_time_ms: 400 })?.status).toBe("fail");
  });
});
