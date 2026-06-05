import ReactECharts from "echarts-for-react";
import type { MonthlyTrendPoint } from "@/lib/stats";

/** 월별 불량률 추이 (ECharts 고밀도 시계열, M11). */
export function TrendChart({ data }: { data: MonthlyTrendPoint[] }): JSX.Element {
  if (data.length === 0) {
    return (
      <div className="flex h-[280px] items-center justify-center text-sm text-slate-400">
        추이 데이터 없음
      </div>
    );
  }
  const option = {
    tooltip: { trigger: "axis" },
    legend: { data: ["불량률(%)", "검사수"] },
    grid: { left: 48, right: 48, top: 36, bottom: 32 },
    xAxis: { type: "category", data: data.map((d) => d.month) },
    yAxis: [
      { type: "value", name: "불량률(%)", min: 0 },
      { type: "value", name: "검사수", min: 0 },
    ],
    series: [
      {
        name: "불량률(%)",
        type: "line",
        smooth: true,
        yAxisIndex: 0,
        data: data.map((d) => Number(d.defectRatePct.toFixed(3))),
        itemStyle: { color: "#dc2626" },
      },
      {
        name: "검사수",
        type: "bar",
        yAxisIndex: 1,
        data: data.map((d) => d.total),
        itemStyle: { color: "#94a3b8" },
      },
    ],
  };
  return (
    <div data-testid="trend-chart">
      <ReactECharts option={option} style={{ height: 280 }} notMerge />
    </div>
  );
}
