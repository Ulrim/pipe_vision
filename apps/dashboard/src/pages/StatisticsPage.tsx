import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchInspectionStats } from "@/api/endpoints";
import { DefectPie } from "@/components/DefectPie";
import { TrendChart } from "@/components/TrendChart";
import { distFromServer, trendFromServer } from "@/lib/stats";
import { fmtNum } from "@/lib/format";

/** M11 — 불량유형별 통계 + 월별 추이. 기간/품목 필터. */
export function StatisticsPage(): JSX.Element {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [item, setItem] = useState("");
  const [applied, setApplied] = useState({ from: "", to: "", item: "" });

  // 서버가 전 건을 센다(표본 아님). 종전에는 행 5,000건을 요청했는데 목록 API
  // 상한이 2,000건이라 요청이 실패했다(2026-10-10 점검 지적).
  const { data, isFetching, isError, error } = useQuery({
    queryKey: ["stats-server", applied],
    queryFn: () =>
      fetchInspectionStats({ from: applied.from, to: applied.to, item: applied.item }),
  });

  const dist = useMemo(() => distFromServer(data), [data]);
  const trend = useMemo(() => trendFromServer(data), [data]);

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
        {data && (
          <span className="text-sm text-slate-500" data-testid="stats-total">
            전체 {fmtNum(data.total, 0)}건 · NG {fmtNum(data.ng, 0)}건 (전 건 집계)
          </span>
        )}
      </div>

      {isError && (
        <div className="card bg-ng-bg p-3 text-sm text-ng-fg" data-testid="stats-error">
          통계 조회 실패: {(error as Error)?.message}
        </div>
      )}

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
