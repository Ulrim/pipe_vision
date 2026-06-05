import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchKpiSummary } from "@/api/endpoints";
import { KpiGauge } from "@/components/KpiGauge";
import { buildKpiGauges, procTimeSpec } from "@/lib/kpi";
import { fmtNum, currentPeriod } from "@/lib/format";

/** M12 — KPI 카드(§1.1 목표 대비 현재값 게이지). */
export function KpiPage(): JSX.Element {
  const [period, setPeriod] = useState(currentPeriod());

  const { data, isFetching, isError, error } = useQuery({
    queryKey: ["kpi-summary", period],
    queryFn: () => fetchKpiSummary(period),
  });

  const gauges = data ? buildKpiGauges(data) : [];
  const procSpec = data ? procTimeSpec(data) : null;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-bold">품질 KPI</h1>
        <input
          type="month"
          className="input"
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          data-testid="kpi-period"
        />
        {isFetching && <span className="text-sm text-slate-400">불러오는 중…</span>}
      </div>

      {isError && (
        <div className="card bg-ng-bg p-3 text-sm text-ng-fg">
          KPI 조회 실패: {(error as Error)?.message}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {gauges.map((g) => (
          <KpiGauge key={g.key} spec={g} />
        ))}
        {procSpec && <KpiGauge spec={procSpec} />}
      </div>

      {data && (
        <div className="card p-4">
          <h2 className="mb-3 font-semibold">상세 집계 ({data.period})</h2>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-4">
            <Stat k="총 검사수" v={fmtNum(data.total_inspected, 0)} />
            <Stat k="불량수" v={fmtNum(data.defect_count, 0)} />
            <Stat k="자동검사 완료" v={fmtNum(data.auto_inspected, 0)} />
            <Stat k="오검수" v={fmtNum(data.misjudge_count, 0)} />
            <Stat k="미검수" v={fmtNum(data.miss_count, 0)} />
            <Stat k="저장건수" v={fmtNum(data.stored_count, 0)} />
            <Stat k="MES 연계" v={fmtNum(data.mes_synced_count, 0)} />
            <Stat k="평균 처리(ms)" v={fmtNum(data.avg_proc_time_ms, 1)} />
            <Stat k="Claim" v={fmtNum(data.claim_count, 0)} />
            <Stat k="작업공수지수" v={fmtNum(data.workload_index, 2)} />
            <Stat k="리드타임(일)" v={fmtNum(data.lead_time_days, 1)} />
          </dl>
        </div>
      )}
    </div>
  );
}

function Stat({ k, v }: { k: string; v: React.ReactNode }): JSX.Element {
  return (
    <div>
      <dt className="text-xs text-slate-400">{k}</dt>
      <dd className="text-lg font-semibold tabular-nums">{v}</dd>
    </div>
  );
}
