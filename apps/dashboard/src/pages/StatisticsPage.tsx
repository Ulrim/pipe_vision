import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchInspections } from "@/api/endpoints";
import { DefectPie } from "@/components/DefectPie";
import { TrendChart } from "@/components/TrendChart";
import { defectDistribution, monthlyDefectTrend } from "@/lib/stats";

/** M11 — 불량유형별 통계 + 월별 추이. 기간/품목 필터. */
export function StatisticsPage(): JSX.Element {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [item, setItem] = useState("");
  const [applied, setApplied] = useState({ from: "", to: "", item: "" });

  const { data, isFetching } = useQuery({
    queryKey: ["stats-inspections", applied],
    queryFn: () =>
      fetchInspections({
        from: applied.from,
        to: applied.to,
        item: applied.item,
        limit: 5000, // 집계 표본
      }),
  });

  const rows = data ?? [];
  const dist = useMemo(() => defectDistribution(rows), [rows]);
  const trend = useMemo(() => monthlyDefectTrend(rows), [rows]);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">불량유형 통계 · 월별 추이</h1>

      <div className="card flex flex-wrap items-end gap-3 p-4">
        <div>
          <span className="label">시작일</span>
          <input type="date" className="input" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div>
          <span className="label">종료일</span>
          <input type="date" className="input" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        <div>
          <span className="label">품목</span>
          <input className="input" value={item} placeholder="전체"
            onChange={(e) => setItem(e.target.value)} />
        </div>
        <button type="button" className="btn-primary"
          onClick={() => setApplied({ from, to, item })} data-testid="stats-apply">
          적용
        </button>
        {isFetching && <span className="text-sm text-slate-400">집계 중…</span>}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="card p-4">
          <h2 className="mb-2 font-semibold">불량유형 분포</h2>
          <DefectPie data={dist} />
        </div>
        <div className="card p-4">
          <h2 className="mb-2 font-semibold">월별 불량률 추이</h2>
          <TrendChart data={trend} />
        </div>
      </div>
    </div>
  );
}
