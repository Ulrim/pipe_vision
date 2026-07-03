import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchKpiSummary, fetchKpiReport, type ReportFormat } from "@/api/endpoints";
import { buildKpiGauges } from "@/lib/kpi";
import { triggerBlobDownload } from "@/lib/download";
import { fmtNum, currentPeriod } from "@/lib/format";
import { ApiError } from "@/api/client";

/** M12 — 월간 품질 리포트 미리보기 → PDF/엑셀 내보내기. */
export function ReportPage(): JSX.Element {
  const [period, setPeriod] = useState(currentPeriod());
  const [busy, setBusy] = useState<ReportFormat | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ["report-preview", period],
    queryFn: () => fetchKpiSummary(period),
  });

  const gauges = data ? buildKpiGauges(data) : [];

  async function download(fmt: ReportFormat): Promise<void> {
    setBusy(fmt);
    setMsg(null);
    try {
      const { blob, filename } = await fetchKpiReport(period, fmt);
      const ext = fmt === "pdf" ? "pdf" : "xlsx";
      triggerBlobDownload(blob, filename ?? `AIVIS_품질리포트_${period}.${ext}`);
      setMsg(`${fmt.toUpperCase()} 리포트 다운로드 완료`);
    } catch (e) {
      const detail = e instanceof ApiError ? e.message : (e as Error).message;
      setMsg(`다운로드 실패: ${detail}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">월간 품질 리포트</h1>

      <div className="card flex flex-wrap items-end gap-3 p-4">
        <div>
          <span className="label">대상 월</span>
          <input type="month" className="input" value={period}
            onChange={(e) => setPeriod(e.target.value)} data-testid="report-period" />
        </div>
        <button type="button" className="btn-primary" disabled={busy !== null}
          onClick={() => download("pdf")} data-testid="download-pdf">
          {busy === "pdf" ? "생성 중…" : "PDF 내보내기"}
        </button>
        <button type="button" className="btn-ghost" disabled={busy !== null}
          onClick={() => download("xlsx")} data-testid="download-xlsx">
          {busy === "xlsx" ? "생성 중…" : "엑셀 내보내기"}
        </button>
        {msg && <span className="text-sm text-slate-500" data-testid="report-msg">{msg}</span>}
      </div>

      {/* 미리보기 */}
      <div className="card p-5" data-testid="report-preview">
        <div className="mb-4 border-b border-slate-200 pb-3">
          <div className="text-sm text-slate-400">AIVIS 월간 품질 리포트</div>
          <div className="text-2xl font-bold">{period}</div>
        </div>
        {data ? (
          <>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              {gauges.map((g) => (
                <div key={g.key} className="rounded border border-slate-100 p-3">
                  <div className="text-xs text-slate-400">{g.label}</div>
                  <div className="text-xl font-bold tabular-nums"
                    style={{ color: g.status === "pass" ? "#16a34a" : g.status === "warn" ? "#d97706" : "#dc2626" }}>
                    {fmtNum(g.value, 2)}{g.unit}
                  </div>
                  <div className="text-xs text-slate-400">
                    목표 {g.direction === "lower" ? "≤" : "="} {fmtNum(g.target, 0)}{g.unit} · {g.status === "pass" ? "달성" : g.status === "warn" ? "근접" : "미달"}
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-4 text-sm text-slate-500">
              총 검사 {fmtNum(data.total_inspected, 0)}건 중 불량 {fmtNum(data.defect_count, 0)}건,
              평균 처리속도 {fmtNum(data.avg_proc_time_ms, 1)}ms.
            </p>
          </>
        ) : (
          <div className="py-8 text-center text-sm text-slate-400">미리보기 로딩 중…</div>
        )}
      </div>
    </div>
  );
}
