import { useQuery } from "@tanstack/react-query";
import { fetchLotSummary, type LotSummary } from "@/api/endpoints";
import { fmtNum } from "@/lib/format";

/**
 * LOT 종합 판정 — 길이·표면·개수 스테이션 결과를 LOT 단위로 합친 결론.
 *
 * 2026-10-10 점검: "모드를 나눈 뒤 길이+표면 통합판정이 안 일어난다". 다발 튜브는
 * 스테이션 사이에서 낱개 추적이 안 되므로 제품 단위가 아니라 LOT 단위로 합친다.
 * 판정은 색+기호+문자 3중 표기(색약 고려).
 */
const VIEW: Record<LotSummary["final_verdict"], { mark: string; text: string; cls: string }> = {
  NG: { mark: "✕", text: "LOT NG", cls: "bg-ng-bg text-ng-fg" },
  OK: { mark: "✓", text: "LOT OK", cls: "bg-ok-bg text-ok-fg" },
  INCOMPLETE: { mark: "…", text: "미완 — 거쳐야 할 검사가 남음", cls: "bg-amber-100 text-amber-900" },
  NONE: { mark: "?", text: "이 LOT 의 결과 없음", cls: "border border-slate-300 text-slate-600" },
};

export function LotSummaryView({ s }: { s: LotSummary }): JSX.Element {
  const v = VIEW[s.final_verdict];
  return (
    <div className="card space-y-3 p-4" data-testid="lot-summary" data-verdict={s.final_verdict}>
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-semibold">LOT {s.lot} 종합 판정</h2>
        <span className={`inline-flex items-center gap-1 rounded px-2 py-0.5 font-bold ${v.cls}`}>
          <span aria-hidden="true">{v.mark}</span>
          {v.text}
        </span>
        {s.item_codes.length > 0 && (
          <span className="text-sm text-slate-500">품목 {s.item_codes.join(", ")}</span>
        )}
      </div>
      {s.reasons.length > 0 && (
        <ul className="list-disc pl-5 text-sm text-slate-700" data-testid="lot-reasons">
          {s.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
      {s.stages.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500">
            <tr>
              <th className="px-2 py-1 font-medium">검사 모드</th>
              <th className="px-2 py-1 font-medium">스테이션</th>
              <th className="px-2 py-1 font-medium">검사 / NG</th>
              <th className="px-2 py-1 font-medium">불량 유형</th>
              <th className="px-2 py-1 font-medium">재확인 대기</th>
            </tr>
          </thead>
          <tbody>
            {s.stages.map((st) => (
              <tr key={st.stage} className="border-t border-slate-100" data-testid={`lot-stage-${st.stage}`}>
                <td className="px-2 py-1">{st.label}</td>
                <td className="px-2 py-1">{st.cam_ids.join(", ")}</td>
                <td className="px-2 py-1 tabular-nums">
                  {fmtNum(st.total, 0)} / <b className={st.ng ? "text-ng-fg" : undefined}>{fmtNum(st.ng, 0)}</b>
                  <span className="text-slate-500"> ({fmtNum(st.ng_rate_pct, 1)}%)</span>
                </td>
                <td className="px-2 py-1">
                  {Object.entries(st.by_code).map(([k, n]) => `${k} ${n}`).join(", ") || "-"}
                </td>
                <td className="px-2 py-1 tabular-nums">{fmtNum(st.pending_review, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function LotSummaryCard({ lot }: { lot: string }): JSX.Element | null {
  const { data, isError, error } = useQuery({
    queryKey: ["lot-summary", lot],
    queryFn: () => fetchLotSummary(lot),
    enabled: !!lot,
  });
  if (!lot) return null;
  if (isError) {
    return (
      <div className="card bg-ng-bg p-3 text-sm text-ng-fg">LOT 종합 판정 조회 실패: {(error as Error)?.message}</div>
    );
  }
  return data ? <LotSummaryView s={data} /> : null;
}
