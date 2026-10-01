import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchKpiSummary, fetchKpiTargets } from "@/api/endpoints";
import { KpiGauge } from "@/components/KpiGauge";
import { KpiManualForm } from "@/components/KpiManualForm";
import { buildKpiGauges, procTimeSpec } from "@/lib/kpi";
import { fmtNum, currentPeriod } from "@/lib/format";

/** M12 — KPI 카드(§1.1 목표 대비 현재값 게이지). */
export function KpiPage(): JSX.Element {
  const [period, setPeriod] = useState(currentPeriod());

  const { data, isFetching, isError, error } = useQuery({
    queryKey: ["kpi-summary", period],
    queryFn: () => fetchKpiSummary(period),
  });

  // 목표치는 서버가 단일 출처(GET /kpi/targets). 화면이 자체 상수를 들고 있으면
  // 리포트(PDF)와 다른 기준으로 합격을 찍게 된다.
  const { data: targets } = useQuery({
    queryKey: ["kpi-targets"],
    queryFn: fetchKpiTargets,
    staleTime: 5 * 60_000,
  });

  const gauges = buildKpiGauges(data, targets);
  const procSpec = procTimeSpec(data, targets);

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

      {/* 4열 고정이면 지표 5개 중 마지막 하나가 다음 줄에 외톨이로 남는다.
          넓은 화면에서는 5개를 한 줄에 놓고, 좁아지면 3→2→1 로 접는다. */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {gauges.map((g) => (
          <KpiGauge key={g.key} spec={g} />
        ))}
        {procSpec && <KpiGauge spec={procSpec} />}
      </div>

      {data && (
        <div className="card p-4">
          <h2 className="mb-3 font-semibold">상세 집계 ({data.period})</h2>
          {/* 12개를 한 줄로 늘어놓으면 눈이 어디서 끊어야 할지 모른다. 성격이
              다른 묶음(물량 / 판정품질 / 연계·속도 / 수기)으로 나눈다. */}
          <StatGroup title="검사 물량">
            <Stat k="총 검사수" v={fmtNum(data.total_inspected, 0)} />
            <Stat k="불량수" v={fmtNum(data.defect_count, 0)} />
            <Stat k="자동검사 완료" v={fmtNum(data.auto_inspected, 0)} />
          </StatGroup>
          <StatGroup title="판정 품질">
            <Stat k="오검수" v={fmtNum(data.misjudge_count, 0)} />
            <Stat k="미검수" v={fmtNum(data.miss_count, 0)} />
          </StatGroup>
          <StatGroup title="저장·연계·속도">
            <Stat k="저장건수" v={fmtNum(data.stored_count, 0)} />
            <Stat k="MES 연계" v={fmtNum(data.mes_synced_count, 0)} />
            <Stat k="평균 처리(ms)" v={fmtNum(data.avg_proc_time_ms, 1)} />
          </StatGroup>
          <StatGroup title="수기 입력 항목">
            <Stat k="Claim" v={fmtNum(data.claim_count, 0)} />
            <Stat k="작업공수지수" v={fmtNum(data.workload_index, 2)} />
            <Stat k="리드타임(일)" v={fmtNum(data.lead_time_days, 1)} />
            <Stat k="총 출하수량" v={fmtNum(data.shipped_qty, 0)} />
            <Stat k="출하유출 부적합" v={fmtNum(data.leak_defect_qty, 0)} />
            <Stat k="출하유출불량률(ppm)" v={fmtNum(data.shipment_leak_ppm, 1)} />
          </StatGroup>
        </div>
      )}

      <KpiManualForm period={period} summary={data} />
    </div>
  );
}

function StatGroup({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <section className="mb-3 last:mb-0">
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
        {title}
      </h3>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-4">
        {children}
      </dl>
    </section>
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
