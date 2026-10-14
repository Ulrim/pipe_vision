/**
 * MES 생산실적 — 작업지시별로 검사기(AIVIS)에서 들어온 결과를 단계별로 본다
 * (docs/MES.md §4, services/mes/mes_app/production.py).
 *
 * 다발 튜브는 스테이션 사이에서 낱개로 추적되지 않아 같은 튜브가 길이 검사와 표면
 * 검사에서 각각 한 행이 된다. 그래서 단계 숫자를 **더하지 않고** 나란히 보여 주고,
 * 양품이 어느 숫자에서 왔는지(good_source)를 함께 적는다.
 */
import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchUnassignedLots,
  fetchWorkOrder,
  fetchWorkOrders,
  type ProductionOut,
  type WorkOrderOut,
} from "@/api/mes";
import {
  EmptyRow,
  MesError,
  ProgressBar,
  StatusBadge,
  THEAD_CLASS,
  Td,
  Th,
} from "@/components/MesParts";
import { fmtDateTime, fmtNum } from "@/lib/format";
import { defectLabel, goodSourceLabel, sortedDefects, woStatusView } from "@/lib/mes";

/** 실적은 검사가 도는 동안 계속 늘어난다 — 손으로 새로고침하지 않아도 되게. */
const REFRESH_MS = 15_000;

export function MesProductionPage(): JSX.Element {
  const [selected, setSelected] = useState<number | null>(null);

  const wos = useQuery({
    queryKey: ["mes", "work-orders", ""],
    queryFn: () => fetchWorkOrders(),
    refetchInterval: REFRESH_MS,
  });
  const detail = useQuery({
    queryKey: ["mes", "work-order", selected],
    queryFn: () => fetchWorkOrder(selected as number),
    enabled: selected !== null,
    refetchInterval: REFRESH_MS,
  });

  const rows = wos.data ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">작업지시별 생산실적</h1>
        <span className="text-xs text-slate-400">15초마다 갱신</span>
        {wos.isFetching && <span className="text-sm text-slate-400">불러오는 중…</span>}
      </div>

      <section className="card">
        <h2 className="border-b border-slate-100 px-4 py-3 font-semibold">작업지시 (최신순)</h2>
        {wos.isError ? (
          <div className="p-4">
            <MesError what="작업지시 조회" error={wos.error} testid="prod-error" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="prod-wo-table">
              <thead className={THEAD_CLASS}>
                <tr>
                  <Th>작업지시</Th>
                  <Th>LOT</Th>
                  <Th>품목</Th>
                  <Th right>지시</Th>
                  <Th right>양품</Th>
                  <Th right>불량</Th>
                  <Th>진척률</Th>
                  <Th>상태</Th>
                  <Th>마지막 검사</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((wo) => (
                  <WoRow
                    key={wo.id}
                    wo={wo}
                    selected={wo.id === selected}
                    onSelect={() => setSelected(wo.id)}
                  />
                ))}
                {rows.length === 0 && !wos.isFetching && (
                  <EmptyRow colSpan={9} testid="prod-empty">
                    작업지시가 없습니다. MES 작업지시 화면에서 먼저 등록하세요.
                  </EmptyRow>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected === null ? (
        rows.length > 0 && (
          <p className="text-sm text-slate-500" data-testid="prod-hint">
            작업지시를 고르면 단계별 실적과 불량유형이 나옵니다.
          </p>
        )
      ) : detail.isError ? (
        <MesError what="생산실적 조회" error={detail.error} testid="prod-detail-error" />
      ) : detail.data ? (
        <ProductionDetail wo={detail.data.work_order} p={detail.data.production} />
      ) : (
        <p className="text-sm text-slate-400">불러오는 중…</p>
      )}

      <UnassignedSection />
    </div>
  );
}

function WoRow({
  wo,
  selected,
  onSelect,
}: {
  wo: WorkOrderOut;
  selected: boolean;
  onSelect: () => void;
}): JSX.Element {
  return (
    <tr
      className={`cursor-pointer border-t border-slate-100 ${selected ? "bg-brand-fg" : "hover:bg-slate-50"}`}
      onClick={onSelect}
      data-testid={`prod-row-${wo.id}`}
    >
      <Td>
        {/* 행 클릭과 별개로 키보드로도 고를 수 있게 버튼을 둔다. */}
        <button
          type="button"
          className="whitespace-nowrap font-medium text-brand underline-offset-2 hover:underline"
          aria-pressed={selected}
          onClick={(e) => {
            e.stopPropagation();
            onSelect();
          }}
          data-testid={`prod-select-${wo.id}`}
        >
          {selected && <span aria-hidden="true">▸ </span>}
          {wo.wo_no}
        </button>
      </Td>
      <Td>
        <span className="whitespace-nowrap">{wo.lot}</span>
      </Td>
      <Td>{wo.item_code}</Td>
      <Td right>{fmtNum(wo.plan_qty, 0)}</Td>
      <Td right>{fmtNum(wo.good_qty, 0)}</Td>
      <Td right>{fmtNum(wo.defect_total, 0)}</Td>
      <Td>
        <ProgressBar pct={wo.progress_pct} />
      </Td>
      <Td>
        <StatusBadge view={woStatusView(wo.status)} />
      </Td>
      <Td>
        <span className="whitespace-nowrap tabular-nums">{fmtDateTime(wo.last_at)}</span>
      </Td>
    </tr>
  );
}

function ProductionDetail({ wo, p }: { wo: WorkOrderOut; p: ProductionOut }): JSX.Element {
  const defects = sortedDefects(p.by_code);
  return (
    <section className="card space-y-4 p-4" data-testid="prod-detail">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">
          {wo.wo_no} <span className="font-normal text-slate-500">· LOT {wo.lot} · {wo.item_code}</span>
        </h2>
        <StatusBadge view={woStatusView(wo.status)} />
        {wo.customer && <span className="text-sm text-slate-500">{wo.customer}</span>}
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile label="지시수량" value={fmtNum(wo.plan_qty, 0)} />
        <Tile
          label="양품"
          value={fmtNum(p.good_qty, 0)}
          note={<span data-testid="good-source">{goodSourceLabel(p.good_source)}</span>}
          testid="tile-good"
        />
        <Tile label="불량 합계" value={fmtNum(p.defect_total, 0)} note="단계별 최종 불량의 합" />
        <Tile
          label="진척률"
          value={wo.progress_pct === null ? "-" : `${fmtNum(wo.progress_pct, 1)}%`}
          note={<ProgressBar pct={wo.progress_pct} />}
        />
      </div>

      <div>
        <h3 className="mb-1 text-sm font-semibold">단계별</h3>
        <p className="mb-2 text-xs text-slate-500">
          단계는 더하지 않습니다 — 같은 튜브가 길이 검사와 표면 검사에서 각각 한 번씩 셉니다.
          최종 불량은 재확인 결과가 있으면 그것을, 없으면 AI 판정을 씁니다.
        </p>
        <div className="overflow-x-auto rounded border border-slate-200">
          <table className="w-full text-sm" data-testid="stage-table">
            <thead className={THEAD_CLASS}>
              <tr>
                <Th>단계</Th>
                <Th right>검사</Th>
                <Th right>AI 불량</Th>
                <Th right>최종 불량(재확인 반영)</Th>
                <Th right>재확인 수</Th>
                <Th right>양품</Th>
              </tr>
            </thead>
            <tbody>
              {p.stages.map((s) => (
                <tr key={s.stage} className="border-t border-slate-100" data-testid={`stage-${s.stage}`}>
                  <Td>{s.label}</Td>
                  <Td right>{fmtNum(s.inspected, 0)}</Td>
                  <Td right>{fmtNum(s.ai_ng, 0)}</Td>
                  <Td right>{fmtNum(s.final_ng, 0)}</Td>
                  <Td right>{fmtNum(s.reviewed, 0)}</Td>
                  <Td right>{fmtNum(s.good, 0)}</Td>
                </tr>
              ))}
              {p.stages.length === 0 && (
                <EmptyRow colSpan={6} testid="stage-empty">
                  {p.crate_count > 0
                    ? "길이·표면 검사 결과는 아직 없습니다(개수 확인만 들어왔습니다)."
                    : "아직 이 작업지시로 들어온 검사 결과가 없습니다."}
                </EmptyRow>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div>
          <h3 className="mb-2 text-sm font-semibold">불량유형</h3>
          {defects.length === 0 ? (
            <p className="text-sm text-slate-400" data-testid="defects-empty">
              불량 없음
            </p>
          ) : (
            <ul className="flex flex-wrap gap-2" data-testid="defects">
              {defects.map(([code, n]) => (
                <li
                  key={code}
                  className="rounded-md border border-slate-200 bg-white px-2 py-1 text-sm"
                  data-testid={`defect-${code}`}
                >
                  <span className="text-slate-500">{defectLabel(code)}</span>{" "}
                  <span className="font-semibold tabular-nums">{fmtNum(n, 0)}</span>
                </li>
              ))}
            </ul>
          )}
          {p.by_code.MANUAL ? (
            <p className="mt-1 text-xs text-slate-400">
              재확인 NG = AI 는 양품이라 했지만 작업자 재확인에서 불량이 된 것(유형 없음).
            </p>
          ) : null}
        </div>

        <div className="space-y-2 text-sm">
          {p.crate_count > 0 && (
            <div data-testid="crate-info">
              <h3 className="mb-1 font-semibold">크레이트(개수 확인)</h3>
              <dl className="grid grid-cols-3 gap-2">
                <Mini k="판 수" v={fmtNum(p.crate_count, 0)} />
                <Mini k="담긴 개수" v={fmtNum(p.crate_tubes, 0)} />
                <Mini
                  k="개수 불일치 판"
                  v={
                    p.crate_mismatch > 0 ? (
                      <span className="text-amber-800">
                        <span aria-hidden="true">⚠ </span>
                        {fmtNum(p.crate_mismatch, 0)}
                      </span>
                    ) : (
                      "0"
                    )
                  }
                />
              </dl>
            </div>
          )}
          <dl className="grid grid-cols-2 gap-2">
            <Mini k="첫 검사" v={fmtDateTime(p.first_at)} />
            <Mini k="마지막 검사" v={fmtDateTime(p.last_at)} />
          </dl>
          <p className="text-xs text-slate-400">검사 행 {fmtNum(p.inspected_rows, 0)}건 기준</p>
        </div>
      </div>
    </section>
  );
}

function UnassignedSection(): JSX.Element {
  const q = useQuery({
    queryKey: ["mes", "unassigned"],
    queryFn: fetchUnassignedLots,
    refetchInterval: REFRESH_MS,
  });
  const rows = q.data ?? [];
  return (
    <section className="card" data-testid="unassigned">
      <div className="border-b border-slate-100 px-4 py-3">
        <h2 className="font-semibold">작업지시 없는 LOT</h2>
        <p className="mt-1 text-xs text-slate-500">
          검사 결과는 들어왔는데 어느 작업지시와도 이어지지 않은 LOT 입니다. 검사기에 LOT 을
          잘못 넣었거나 작업지시를 늦게 만든 경우입니다 — 그 LOT 으로 작업지시를 만들면
          자동으로 이어집니다.
        </p>
      </div>
      {q.isError ? (
        <div className="p-4">
          <MesError what="미지정 LOT 조회" error={q.error} testid="unassigned-error" />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className={THEAD_CLASS}>
              <tr>
                <Th>LOT</Th>
                <Th>검사기 작업지시</Th>
                <Th>품목</Th>
                <Th right>검사</Th>
                <Th right>양품</Th>
                <Th right>불량</Th>
                <Th>마지막 검사</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.lot} className="border-t border-slate-100" data-testid={`unassigned-${u.lot}`}>
                  <Td>
                    <span className="whitespace-nowrap font-medium">{u.lot}</span>
                  </Td>
                  <Td>{u.work_order ?? <span className="text-slate-400">-</span>}</Td>
                  <Td>{u.item_code ?? "-"}</Td>
                  <Td right>{fmtNum(u.production.inspected_rows, 0)}</Td>
                  <Td right>
                    <span title={goodSourceLabel(u.production.good_source)}>
                      {fmtNum(u.production.good_qty, 0)}
                    </span>
                  </Td>
                  <Td right>{fmtNum(u.production.defect_total, 0)}</Td>
                  <Td>
                    <span className="whitespace-nowrap tabular-nums">
                      {fmtDateTime(u.production.last_at)}
                    </span>
                  </Td>
                </tr>
              ))}
              {rows.length === 0 && !q.isFetching && (
                <EmptyRow colSpan={7} testid="unassigned-empty">
                  <span aria-hidden="true">✓ </span>모든 검사 결과가 작업지시와 이어져 있습니다.
                </EmptyRow>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Tile({
  label,
  value,
  note,
  testid,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  testid?: string;
}): JSX.Element {
  return (
    <div className="rounded-md border border-slate-200 p-3" data-testid={testid}>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-2xl font-bold tabular-nums">{value}</div>
      {note && <div className="mt-1 text-xs text-slate-500">{note}</div>}
    </div>
  );
}

function Mini({ k, v }: { k: string; v: ReactNode }): JSX.Element {
  return (
    <div>
      <dt className="text-xs text-slate-400">{k}</dt>
      <dd className="font-semibold tabular-nums">{v}</dd>
    </div>
  );
}
