import { describe, expect, it } from "vitest";
import type { KpiSummary } from "@aivis/shared-types";
import { buildKpiGauges, gaugeRatio, kpiStatus, procTimeSpec } from "./kpi";
import type { KpiTarget } from "@/api/endpoints";

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

// 서버(GET /kpi/targets)가 내려주는 목표치. 화면은 이것만 쓴다.
const TARGETS: KpiTarget[] = [
  { key: "process_defect_ppm", label: "공정불량률 (ppm)", label_en: "Process defect (ppm)",
    target_text: "600 이하", target_value: 600, direction: "lower" },
  { key: "shipment_leak_ppm", label: "출하유출불량률 (ppm)", label_en: "Shipment leakage (ppm)",
    target_text: "1000 이하", target_value: 1000, direction: "lower" },
  { key: "inspection_defect_rate_pct", label: "검사불량률 (%)", label_en: "Inspection defect (%)",
    target_text: "30 이하", target_value: 30, direction: "lower" },
  { key: "auto_inspection_rate_pct", label: "자동검사율 (%)", label_en: "Auto inspection (%)",
    target_text: "100", target_value: 100, direction: "higher" },
  { key: "storage_mes_rate_pct", label: "저장·MES 연계율 (%)", label_en: "Storage/MES link (%)",
    target_text: "100", target_value: 100, direction: "higher" },
  { key: "p95_proc_time_ms", label: "처리속도 p95 (ms)", label_en: "Proc time p95 (ms)",
    target_text: "300 이하", target_value: 300, direction: "lower" },
];

describe("buildKpiGauges", () => {
  it("서버 목표치대로 게이지를 만든다", () => {
    const g = buildKpiGauges(summary, TARGETS);
    // 출하유출불량률은 실적(shipment_leak_ppm)이 없으므로 게이지에서 빠진다.
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

  it("목표치를 바꾸면 게이지 판정도 따라 바뀐다(코드 상수를 쓰지 않는다)", () => {
    const strict = TARGETS.map((t) =>
      t.key === "process_defect_ppm" ? { ...t, target_value: 100 } : t,
    );
    const g = buildKpiGauges({ ...summary, process_defect_ppm: 500 }, strict);
    expect(g[0].target).toBe(100);
    expect(g[0].status).toBe("fail");
  });

  it("출하유출불량률은 실적이 있을 때만 게이지가 생긴다", () => {
    const withLeak = buildKpiGauges(
      { ...summary, shipment_leak_ppm: 750 },
      TARGETS,
    );
    const leak = withLeak.find((x) => x.key === "shipment_leak_ppm");
    expect(leak?.target).toBe(1000);
    expect(leak?.status).toBe("pass");
  });

  it("데이터가 아직 없으면 빈 배열(로딩 중 렌더 안전)", () => {
    expect(buildKpiGauges(undefined, TARGETS)).toEqual([]);
    expect(buildKpiGauges(summary, undefined)).toEqual([]);
  });

  it("procTimeSpec: 서버 목표(300ms) 이하면 pass, null 안전", () => {
    expect(procTimeSpec(summary, TARGETS)?.status).toBe("pass");
    expect(procTimeSpec({ ...summary, avg_proc_time_ms: null }, TARGETS)).toBeNull();
    expect(procTimeSpec({ ...summary, avg_proc_time_ms: 400 }, TARGETS)?.status).toBe(
      "fail",
    );
  });
});
