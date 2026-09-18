/**
 * 비자동 KPI 수기 입력 폼 (M12 — "작업공수/리드타임 비교자료 입력·관리").
 *
 * 이 화면이 없어서 그동안 Claim·작업공수·리드타임을 **표시만 하고 넣을 수는
 * 없었다**(POST /kpi/manual 은 있는데 부르는 UI 가 없었음). 여기에 계약
 * 성과지표인 출하유출불량률의 분자·분모까지 함께 받는다.
 *
 * 출하수량·유출 부적합수량은 시스템이 알 수 없는 값이다. 검사에서 걸러낸
 * 불량은 고객에게 가지 않으므로, "출하 후 발견된 부적합"은 반품·클레임을
 * 통해서만 알 수 있고 그래서 사람이 입력한다.
 *
 * 권한: quality 이상만 저장 가능(작업자는 읽기만).
 */
import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { KpiSummary } from "@aivis/shared-types";
import { upsertKpiManual } from "@/api/endpoints";
import { useAuthStore, canEdit } from "@/store/auth";

/** 빈 문자열 → null, 숫자 문자열 → number. 0 은 유효한 값이므로 보존한다. */
function num(v: string): number | null {
  const t = v.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

function str(v: number | null | undefined): string {
  return v === null || v === undefined ? "" : String(v);
}

export function KpiManualForm({
  period,
  summary,
}: {
  /** "YYYY-MM" */
  period: string;
  summary: KpiSummary | undefined;
}): JSX.Element {
  const role = useAuthStore((s) => s.role);
  const editable = canEdit(role);
  const qc = useQueryClient();

  const [claim, setClaim] = useState("");
  const [workload, setWorkload] = useState("");
  const [lead, setLead] = useState("");
  const [shipped, setShipped] = useState("");
  const [leak, setLeak] = useState("");

  // 월을 바꾸면 그 달의 저장값으로 폼을 다시 채운다.
  useEffect(() => {
    setClaim(str(summary?.claim_count));
    setWorkload(str(summary?.workload_index));
    setLead(str(summary?.lead_time_days));
    setShipped(str(summary?.shipped_qty));
    setLeak(str(summary?.leak_defect_qty));
  }, [summary]);

  const mutation = useMutation({
    mutationFn: () =>
      upsertKpiManual({
        period: `${period}-01`,
        claim_count: num(claim),
        workload_index: num(workload),
        lead_time_days: num(lead),
        shipped_qty: num(shipped),
        leak_defect_qty: num(leak),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kpi-summary", period] });
    },
  });

  const leakPreview = (() => {
    const s = num(shipped);
    const l = num(leak);
    if (!s || l === null) return null;
    return ((l / s) * 1_000_000).toFixed(1);
  })();

  return (
    <div className="card p-4" data-testid="kpi-manual-form">
      <h2 className="mb-1 font-semibold">수기 입력 ({period})</h2>
      <p className="mb-3 text-xs text-slate-400">
        시스템이 자동으로 알 수 없는 값입니다. 출하수량과 출하 후 부적합수량을
        넣으면 출하유출불량률(계약 성과지표)이 산출됩니다.
      </p>

      <form
        className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (editable && !mutation.isPending) mutation.mutate();
        }}
      >
        <Field label="Claim 건수" value={claim} onChange={setClaim}
               disabled={!editable} testId="kpi-claim" />
        <Field label="작업공수 지수" value={workload} onChange={setWorkload}
               disabled={!editable} testId="kpi-workload" step="0.01" />
        <Field label="리드타임(일)" value={lead} onChange={setLead}
               disabled={!editable} testId="kpi-lead" step="0.1" />
        <Field label="총 출하수량" value={shipped} onChange={setShipped}
               disabled={!editable} testId="kpi-shipped" />
        <Field label="출하 후 부적합수량" value={leak} onChange={setLeak}
               disabled={!editable} testId="kpi-leak" />

        <div className="sm:col-span-2 lg:col-span-5 flex flex-wrap items-center gap-3">
          <button
            type="submit"
            className="btn-primary"
            disabled={!editable || mutation.isPending}
            data-testid="kpi-manual-save"
          >
            {mutation.isPending ? "저장 중…" : "저장"}
          </button>
          {leakPreview && (
            <span className="text-sm text-slate-400" data-testid="kpi-leak-preview">
              출하유출불량률 = {leakPreview} ppm
            </span>
          )}
          {!editable && (
            <span className="text-sm text-slate-400">
              수정 권한이 없습니다(품질관리자 이상).
            </span>
          )}
          {mutation.isError && (
            <span className="text-sm text-ng-fg" role="alert">
              저장 실패: {(mutation.error as Error)?.message}
            </span>
          )}
          {mutation.isSuccess && !mutation.isPending && (
            <span className="text-sm text-ok-fg">저장했습니다.</span>
          )}
        </div>
      </form>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  disabled,
  testId,
  step,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
  testId: string;
  step?: string;
}): JSX.Element {
  return (
    <label className="block text-sm">
      <span className="text-xs text-slate-400">{label}</span>
      <input
        type="number"
        min="0"
        step={step ?? "1"}
        className="input mt-1 w-full"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
      />
    </label>
  );
}
