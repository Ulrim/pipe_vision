import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";
import { fetchInspections, type InspectionQuery } from "@/api/endpoints";
import { Pagination } from "@/components/Pagination";
import { VerdictBadge } from "@/components/VerdictBadge";
import { InspectionDetail } from "@/components/InspectionDetail";
import { fmtNum, fmtDateTime } from "@/lib/format";
import { rowsToCsv, triggerBlobDownload } from "@/lib/download";

const PAGE_SIZE = 25;

interface Filters {
  lot: string;
  item: string;
  from: string;
  to: string;
  verdict: string;
}
const EMPTY: Filters = { lot: "", item: "", from: "", to: "", verdict: "" };

const CSV_COLUMNS = [
  { key: "id", header: "id" },
  { key: "lot", header: "lot" },
  { key: "item_code", header: "item_code" },
  { key: "inspected_at", header: "inspected_at" },
  { key: "meas_length_mm", header: "meas_length_mm" },
  { key: "deviation_mm", header: "deviation_mm" },
  { key: "final_verdict", header: "final_verdict" },
  { key: "defect_codes", header: "defect_codes" },
  { key: "proc_time_ms", header: "proc_time_ms" },
];

/** M11 — LOT별 검사이력 조회(필터 조합 + 서버 페이지네이션). */
export function InspectionsPage(): JSX.Element {
  // 입력 중인 필터(폼)와 적용된 필터(조회)를 분리.
  const [form, setForm] = useState<Filters>(EMPTY);
  const [applied, setApplied] = useState<Filters>(EMPTY);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<InspectionResult | null>(null);

  const query: InspectionQuery = useMemo(
    () => ({
      lot: applied.lot,
      item: applied.item,
      from: applied.from,
      to: applied.to,
      verdict: applied.verdict,
      limit: PAGE_SIZE,
      offset,
    }),
    [applied, offset],
  );

  const { data, isFetching, isError, error } = useQuery({
    queryKey: ["inspections", query],
    queryFn: () => fetchInspections(query),
  });

  const rows = data ?? [];

  function applyFilters(): void {
    setOffset(0);
    setApplied(form);
  }
  function reset(): void {
    setForm(EMPTY);
    setApplied(EMPTY);
    setOffset(0);
  }
  function downloadCsv(): void {
    const csv = rowsToCsv(rows as unknown as Record<string, unknown>[], CSV_COLUMNS);
    triggerBlobDownload(
      new Blob([csv], { type: "text/csv;charset=utf-8" }),
      `inspections_${applied.lot || "all"}.csv`,
    );
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">검사이력 조회</h1>

      {/* 필터 조합 검색 */}
      <div className="card grid grid-cols-2 gap-3 p-4 md:grid-cols-6">
        <Field label="LOT">
          <input className="input w-full" value={form.lot} data-testid="filter-lot"
            onChange={(e) => setForm({ ...form, lot: e.target.value })} />
        </Field>
        <Field label="품목">
          <input className="input w-full" value={form.item} data-testid="filter-item"
            onChange={(e) => setForm({ ...form, item: e.target.value })} />
        </Field>
        <Field label="시작일">
          <input type="date" className="input w-full" value={form.from} data-testid="filter-from"
            onChange={(e) => setForm({ ...form, from: e.target.value })} />
        </Field>
        <Field label="종료일">
          <input type="date" className="input w-full" value={form.to} data-testid="filter-to"
            onChange={(e) => setForm({ ...form, to: e.target.value })} />
        </Field>
        <Field label="판정">
          <select className="input w-full" value={form.verdict} data-testid="filter-verdict"
            onChange={(e) => setForm({ ...form, verdict: e.target.value })}>
            <option value="">전체</option>
            <option value={Verdict.OK}>OK</option>
            <option value={Verdict.NG}>NG</option>
          </select>
        </Field>
        <div className="flex items-end gap-2">
          <button type="button" className="btn-primary" onClick={applyFilters} data-testid="apply-filters">
            검색
          </button>
          <button type="button" className="btn-ghost" onClick={reset}>
            초기화
          </button>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <div className="text-sm text-slate-500">
          {isFetching ? "조회 중…" : `${rows.length}건`}
        </div>
        <button type="button" className="btn-ghost" onClick={downloadCsv}
          disabled={rows.length === 0} data-testid="download-csv">
          CSV 다운로드
        </button>
      </div>

      {isError && (
        <div className="card bg-ng-bg p-3 text-sm text-ng-fg">
          조회 실패: {(error as Error)?.message}
        </div>
      )}

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-slate-500">
            <tr>
              <Th>ID</Th><Th>LOT</Th><Th>품목</Th><Th>검사시각</Th>
              <Th>측정길이(mm)</Th><Th>편차(mm)</Th><Th>판정</Th>
              <Th>불량유형</Th><Th>처리(ms)</Th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !isFetching && (
              <tr><td colSpan={9} className="p-6 text-center text-slate-400">결과 없음</td></tr>
            )}
            {rows.map((r) => (
              <tr key={r.id ?? `${r.lot}-${r.inspected_at}`}
                className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                onClick={() => setSelected(r)} data-testid="insp-row">
                <Td>{r.id}</Td>
                <Td>{r.lot}</Td>
                <Td>{r.item_code}</Td>
                <Td>{fmtDateTime(r.inspected_at)}</Td>
                <Td>{fmtNum(r.meas_length_mm, 3)}</Td>
                <Td>{fmtNum(r.deviation_mm, 3)}</Td>
                <Td><VerdictBadge verdict={r.final_verdict} /></Td>
                <Td>{(r.defect_codes ?? []).join(", ") || "-"}</Td>
                <Td>{fmtNum(r.proc_time_ms, 0)}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Pagination offset={offset} limit={PAGE_SIZE} pageCount={rows.length}
        onChange={setOffset} />

      {selected && (
        <InspectionDetail row={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <div>
      <span className="label">{label}</span>
      {children}
    </div>
  );
}
function Th({ children }: { children: React.ReactNode }): JSX.Element {
  return <th className="px-3 py-2 font-medium">{children}</th>;
}
function Td({ children }: { children: React.ReactNode }): JSX.Element {
  return <td className="px-3 py-2 tabular-nums">{children}</td>;
}
