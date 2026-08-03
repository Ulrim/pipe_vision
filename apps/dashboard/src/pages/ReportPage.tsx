import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchKpiReport,
  fetchKpiReportPreview,
  type ReportFormat,
  type ReportTarget,
} from "@/api/endpoints";
import { buildKpiGauges } from "@/lib/kpi";
import { triggerBlobDownload } from "@/lib/download";
import { fmtNum, currentPeriod } from "@/lib/format";
import { ApiError } from "@/api/client";
import { DailyPpmTrend, DefectBar } from "@/components/ReportCharts";

const STATUS_GOOD = "#0ca30c";
const STATUS_CRITICAL = "#d03b3b";
const INK = "#52514e";

/**
 * 달성 여부 표기. 색만으로 구분하면 적녹색약 사용자가 판별할 수 없으므로
 * **기호 + 문자 + 색** 3중으로 표기한다(PDF 리포트와 동일 규칙).
 */
function AchievedCell({ achieved }: { achieved: boolean | null }): JSX.Element {
  if (achieved === null) {
    return <span style={{ color: INK }}>- 판정보류</span>;
  }
  return (
    <span
      style={{ color: achieved ? STATUS_GOOD : STATUS_CRITICAL }}
      className="font-semibold"
    >
      {achieved ? "O 달성" : "X 미달"}
    </span>
  );
}

/** M12 — 월간 품질 리포트 미리보기 → PDF/엑셀 내보내기. */
export function ReportPage(): JSX.Element {
  const [period, setPeriod] = useState(currentPeriod());
  const [busy, setBusy] = useState<ReportFormat | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  // 미리보기는 PDF/XLSX 와 같은 서버 집계를 그대로 받는다(숫자 불일치 방지).
  const { data: preview, isLoading } = useQuery({
    queryKey: ["report-preview", period],
    queryFn: () => fetchKpiReportPreview(period),
  });

  const summary = preview?.summary;
  const gauges = summary ? buildKpiGauges(summary) : [];

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

      {/* 미리보기 — 내려받을 PDF 와 같은 구성/숫자를 화면에서 먼저 확인한다. */}
      <div className="card p-5" data-testid="report-preview">
        <div className="mb-4 border-b border-slate-200 pb-3">
          <div className="text-sm text-slate-400">AIVIS 월간 품질 리포트</div>
          <div className="text-2xl font-bold">{period}</div>
        </div>

        {isLoading || !preview || !summary ? (
          <div className="py-8 text-center text-sm text-slate-400">미리보기 로딩 중…</div>
        ) : (
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
              총 검사 {fmtNum(summary.total_inspected, 0)}건 중 불량 {fmtNum(summary.defect_count, 0)}건,
              평균 처리속도 {fmtNum(summary.avg_proc_time_ms, 1)}ms.
            </p>

            {/* 처리속도 분포 — FAT 기준 300ms/ea 는 평균이 아니라 꼬리를 봐야 한다. */}
            <h2 className="mt-6 mb-2 text-base font-bold">처리속도 분포 (ms)</h2>
            <div className="grid grid-cols-3 gap-3" data-testid="proc-percentiles">
              {(["p50", "p95", "p99"] as const).map((k) => (
                <div key={k} className="rounded border border-slate-100 p-3 text-center">
                  <div className="text-xs text-slate-400">
                    {k === "p50" ? "p50 (중앙값)" : k}
                  </div>
                  <div className="text-lg font-bold tabular-nums">
                    {preview.proc_time[k] === null ? "-" : fmtNum(preview.proc_time[k], 0)}
                  </div>
                </div>
              ))}
            </div>

            {/* KPI 목표 대비 달성(인수 기준) — PDF 와 같은 판정 결과. */}
            <h2 className="mt-6 mb-2 text-base font-bold">KPI 목표 대비 달성 (인수 기준)</h2>
            <table className="w-full text-sm" data-testid="target-table">
              <thead>
                <tr className="bg-slate-50 text-left text-slate-500">
                  <th className="p-2">항목</th>
                  <th className="p-2 text-center">목표</th>
                  <th className="p-2 text-center">실적</th>
                  <th className="p-2 text-center">달성 여부</th>
                </tr>
              </thead>
              <tbody>
                {preview.targets.map((t: ReportTarget) => (
                  <tr key={t.key} className="border-t border-slate-100">
                    <td className="p-2">{t.label}</td>
                    <td className="p-2 text-center tabular-nums">{t.target}</td>
                    <td className="p-2 text-center tabular-nums">{t.actual}</td>
                    <td className="p-2 text-center">
                      <AchievedCell achieved={t.achieved} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h2 className="mt-6 mb-2 text-base font-bold">일자별 공정불량률 추세 (ppm)</h2>
            <DailyPpmTrend data={preview.daily} />

            <h2 className="mt-6 mb-2 text-base font-bold">불량유형별 건수</h2>
            <DefectBar data={preview.defects} />
          </>
        )}
      </div>
    </div>
  );
}
