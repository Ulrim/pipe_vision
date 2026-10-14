/**
 * MES 작업지시 — 수주 → 작업지시 → 검사기 오더(docs/MES.md §6.1).
 *
 * 이 화면의 핵심 동작은 "검사기에 오더 내리기" 다. 작업지시의 품목·LOT·지시번호를
 * AIVIS 현재 검사 오더(PUT /master/active)로 그대로 넘기면 파이 워커가 15초 안에
 * 전환하고, 그 뒤 검사결과는 같은 LOT·지시번호로 MES 에 들어와 이 작업지시의 실적이
 * 된다. 사람이 LOT 을 두 번(MES·검사기) 치지 않게 하는 것이 목적이다 — 한 글자만
 * 달라도 실적이 '작업지시 없는 LOT' 으로 빠진다.
 *
 * 권한: 조회·시작/완료/취소는 작업자도(라인에서 바꾼다), 등록·마감·검사기 오더는
 * 품질관리자 이상(서버도 같은 규칙으로 막는다).
 */
import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ItemMaster } from "@aivis/shared-types";
import { ApiError } from "@/api/client";
import { fetchActiveOrder, putActiveOrder } from "@/api/endpoints";
import {
  createSalesOrder,
  createWorkOrder,
  fetchSalesOrders,
  fetchWorkOrders,
  patchSalesOrder,
  patchWorkOrder,
  type SalesOrderOut,
  type WoStatus,
  type WorkOrderOut,
} from "@/api/mes";
import {
  EmptyRow,
  Field,
  ItemSelect,
  MesError,
  ProgressBar,
  ResultLine,
  StatusBadge,
  THEAD_CLASS,
  Td,
  Th,
  useAivisItems,
} from "@/components/MesParts";
import { fmtDateTime, fmtNum } from "@/lib/format";
import {
  WO_FILTERS,
  orNull,
  parseCount,
  soStatusView,
  todayIso,
  woStatusView,
} from "@/lib/mes";
import { canEdit, useAuthStore } from "@/store/auth";

interface Msg {
  ok: boolean;
  text: string;
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** 검사기(AIVIS API) 오더 실패를 현장 말로. MES 오류와 구분되게 "검사기" 를 앞에 둔다. */
function dispatchErrText(e: unknown, itemCode: string): string {
  if (e instanceof ApiError && e.status === 403) return "품질관리자 이상 권한이 필요합니다.";
  if (e instanceof ApiError && e.status === 404) {
    return `AIVIS 기준정보에 품목 ${itemCode} 이(가) 없습니다 — 기준정보에 먼저 등록하세요.`;
  }
  return errText(e);
}

export function MesOrdersPage(): JSX.Element {
  const role = useAuthStore((s) => s.role);
  const editable = canEdit(role);
  const qc = useQueryClient();
  const { items, failed: itemsFailed } = useAivisItems();
  const [filter, setFilter] = useState(WO_FILTERS[0].value);
  const [msg, setMsg] = useState<Msg | null>(null);
  // 취소는 되돌리는 버튼이 없으므로 한 번 더 눌러야 실행한다(터치 오조작 방지).
  const [confirmCancel, setConfirmCancel] = useState<number | null>(null);

  const wos = useQuery({
    queryKey: ["mes", "work-orders", filter],
    queryFn: () => fetchWorkOrders(filter || undefined),
  });
  // 지금 검사기에 걸린 오더 — 어느 작업지시가 "실제로 찍히고 있는지" 표에 표시한다.
  const active = useQuery({ queryKey: ["active-order"], queryFn: fetchActiveOrder });

  const invalidateMes = () => qc.invalidateQueries({ queryKey: ["mes"] });

  const statusMut = useMutation({
    mutationFn: (p: { wo: WorkOrderOut; status: WoStatus }) =>
      patchWorkOrder(p.wo.id, { status: p.status }),
    onSuccess: (wo, p) => {
      setMsg({ ok: true, text: `${wo.wo_no} — ${woStatusView(p.status).text}(으)로 바꿨습니다.` });
      invalidateMes();
    },
    onError: (e) => setMsg({ ok: false, text: `상태 변경 실패: ${errText(e)}` }),
    onSettled: () => setConfirmCancel(null),
  });

  const dispatchMut = useMutation({
    mutationFn: async (wo: WorkOrderOut) => {
      try {
        await putActiveOrder({ item_code: wo.item_code, lot: wo.lot, work_order: wo.wo_no });
      } catch (e) {
        throw new Error(dispatchErrText(e, wo.item_code));
      }
      // 검사기는 이미 바뀌었다. 상태 변경만 실패하면 그 사실을 따로 알려야 한다
      // (전체를 실패로 보이면 사용자가 다시 눌러 같은 오더를 또 내린다).
      if (wo.status === "PLANNED") {
        try {
          await patchWorkOrder(wo.id, { status: "RUNNING" });
        } catch (e) {
          return { wo, started: false, startError: errText(e) };
        }
        return { wo, started: true, startError: null };
      }
      return { wo, started: false, startError: null };
    },
    onSuccess: ({ wo, started, startError }) => {
      const head = `검사기에 ${wo.wo_no}(품목 ${wo.item_code} · LOT ${wo.lot})을(를) 내렸습니다 — 검사기가 15초 안에 이 오더로 전환합니다.`;
      if (startError) {
        setMsg({
          ok: false,
          text: `${head} 다만 작업지시를 '진행 중'으로 바꾸지 못했습니다: ${startError}`,
        });
      } else {
        setMsg({ ok: true, text: started ? `${head} 작업지시를 '진행 중'으로 바꿨습니다.` : head });
      }
      qc.invalidateQueries({ queryKey: ["active-order"] });
      invalidateMes();
    },
    onError: (e) => setMsg({ ok: false, text: `검사기 오더 실패: ${errText(e)}` }),
  });

  const busy = statusMut.isPending || dispatchMut.isPending;
  const rows = wos.data ?? [];
  const activeWo = active.data?.work_order ?? null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">수주·작업지시</h1>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          상태
          <select
            className="input"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            data-testid="wo-filter"
          >
            {WO_FILTERS.map((f) => (
              <option key={f.label} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        {!editable && (
          <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500">
            작업자: 시작·완료·취소만 할 수 있습니다(등록은 품질관리자 이상)
          </span>
        )}
        {wos.isFetching && <span className="text-sm text-slate-400">불러오는 중…</span>}
      </div>

      {msg && (
        <ResultLine ok={msg.ok} testid="wo-msg">
          {msg.text}
        </ResultLine>
      )}

      <section className="card">
        <h2 className="border-b border-slate-100 px-4 py-3 font-semibold">작업지시</h2>
        {wos.isError ? (
          <div className="p-4">
            <MesError what="작업지시 조회" error={wos.error} testid="wo-error" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="wo-table">
              <thead className={THEAD_CLASS}>
                <tr>
                  <Th>작업지시</Th>
                  <Th>LOT</Th>
                  <Th>품목</Th>
                  <Th>수주/고객</Th>
                  <Th right>지시수량</Th>
                  <Th right>양품</Th>
                  <Th right>불량</Th>
                  <Th>진척률</Th>
                  <Th>상태</Th>
                  <Th>마지막 검사</Th>
                  <Th>작업</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((wo) => {
                  const open = wo.status === "PLANNED" || wo.status === "RUNNING";
                  const onInspector = activeWo !== null && activeWo === wo.wo_no;
                  return (
                    <tr
                      key={wo.id}
                      className="border-t border-slate-100"
                      data-testid={`wo-row-${wo.id}`}
                    >
                      <Td>
                        <span className="whitespace-nowrap font-medium">{wo.wo_no}</span>
                        {onInspector && (
                          <span
                            className="ml-1 whitespace-nowrap rounded bg-sky-100 px-1 text-xs font-semibold text-sky-900"
                            data-testid={`wo-on-inspector-${wo.id}`}
                          >
                            ◆ 검사기 적용 중
                          </span>
                        )}
                      </Td>
                      <Td>
                        <span className="whitespace-nowrap">{wo.lot}</span>
                      </Td>
                      <Td>{wo.item_code}</Td>
                      <Td>
                        {wo.so_no ? (
                          <span className="whitespace-nowrap">
                            {wo.so_no}
                            {wo.customer && <span className="text-slate-500"> / {wo.customer}</span>}
                          </span>
                        ) : (
                          <span className="text-slate-400">수주 없음</span>
                        )}
                      </Td>
                      <Td right>{fmtNum(wo.plan_qty, 0)}</Td>
                      <Td right>{fmtNum(wo.good_qty, 0)}</Td>
                      <Td right>{fmtNum(wo.defect_total, 0)}</Td>
                      <Td>
                        <ProgressBar pct={wo.progress_pct} />
                      </Td>
                      <Td>
                        <StatusBadge view={woStatusView(wo.status)} testid={`wo-status-${wo.id}`} />
                      </Td>
                      <Td>
                        <span className="whitespace-nowrap tabular-nums">{fmtDateTime(wo.last_at)}</span>
                      </Td>
                      <Td>
                        {open ? (
                          <div className="flex flex-wrap gap-1">
                            {wo.status === "PLANNED" && (
                              <RowButton
                                disabled={busy}
                                onClick={() => statusMut.mutate({ wo, status: "RUNNING" })}
                                testid={`wo-start-${wo.id}`}
                              >
                                시작
                              </RowButton>
                            )}
                            {wo.status === "RUNNING" && (
                              <RowButton
                                disabled={busy}
                                onClick={() => statusMut.mutate({ wo, status: "DONE" })}
                                testid={`wo-done-${wo.id}`}
                              >
                                완료
                              </RowButton>
                            )}
                            {confirmCancel === wo.id ? (
                              <>
                                <RowButton
                                  danger
                                  disabled={busy}
                                  onClick={() => statusMut.mutate({ wo, status: "CANCELLED" })}
                                  testid={`wo-cancel-confirm-${wo.id}`}
                                >
                                  취소 확정
                                </RowButton>
                                <RowButton onClick={() => setConfirmCancel(null)}>그대로 둠</RowButton>
                              </>
                            ) : (
                              <RowButton
                                disabled={busy}
                                onClick={() => setConfirmCancel(wo.id)}
                                testid={`wo-cancel-${wo.id}`}
                              >
                                취소
                              </RowButton>
                            )}
                            {editable && (
                              <RowButton
                                primary
                                disabled={busy}
                                onClick={() => dispatchMut.mutate(wo)}
                                testid={`wo-dispatch-${wo.id}`}
                              >
                                검사기에 오더 내리기
                              </RowButton>
                            )}
                          </div>
                        ) : (
                          <span className="text-slate-400">-</span>
                        )}
                      </Td>
                    </tr>
                  );
                })}
                {rows.length === 0 && !wos.isFetching && (
                  <EmptyRow colSpan={11} testid="wo-empty">
                    {filter === WO_FILTERS[0].value
                      ? "진행 중인 작업지시가 없습니다."
                      : "작업지시가 없습니다."}
                    {editable && " 아래에서 작업지시를 등록하세요."}
                  </EmptyRow>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {editable && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <SalesOrderForm items={items} />
          <WorkOrderForm items={items} />
          {itemsFailed && (
            <p className="text-xs text-amber-800 lg:col-span-2">
              <span aria-hidden="true">⚠ </span>
              AIVIS 기준정보 품목을 불러오지 못해 품목 선택지가 비어 있습니다.
            </p>
          )}
        </div>
      )}

      <SalesOrderList editable={editable} />
    </div>
  );
}

function RowButton({
  children,
  onClick,
  disabled,
  primary,
  danger,
  testid,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  primary?: boolean;
  danger?: boolean;
  testid?: string;
}): JSX.Element {
  const base = primary ? "btn-primary" : "btn-ghost";
  const tone = danger ? " border-ng text-ng-fg" : "";
  return (
    <button
      type="button"
      className={`${base}${tone} whitespace-nowrap px-2 py-1 text-xs`}
      disabled={disabled}
      onClick={onClick}
      data-testid={testid}
    >
      {children}
    </button>
  );
}

/* ---------------- 수주 등록 ---------------- */

function SalesOrderForm({ items }: { items: ItemMaster[] }): JSX.Element {
  const qc = useQueryClient();
  const [customer, setCustomer] = useState("");
  const [item, setItem] = useState("");
  const [qty, setQty] = useState("");
  const [orderDate, setOrderDate] = useState(todayIso());
  const [dueDate, setDueDate] = useState("");
  const [note, setNote] = useState("");
  const [result, setResult] = useState<Msg | null>(null);

  const qtyN = parseCount(qty);
  // 날짜 문자열(YYYY-MM-DD)은 사전순 = 시간순.
  const dueBeforeOrder = dueDate !== "" && orderDate !== "" && dueDate < orderDate;
  const valid = customer.trim() !== "" && item !== "" && qtyN !== null && orderDate !== "" && !dueBeforeOrder;

  const m = useMutation({
    mutationFn: () =>
      createSalesOrder({
        customer: customer.trim(),
        item_code: item,
        qty: qtyN as number,
        order_date: orderDate,
        due_date: orNull(dueDate),
        note: orNull(note),
      }),
    onSuccess: (so) => {
      setResult({ ok: true, text: `수주 ${so.so_no} 를 등록했습니다.` });
      setCustomer("");
      setQty("");
      setDueDate("");
      setNote("");
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) => setResult({ ok: false, text: `수주 등록 실패: ${errText(e)}` }),
  });

  return (
    <form
      className="card space-y-3 p-4"
      data-testid="so-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && !m.isPending) m.mutate();
      }}
    >
      <h2 className="font-semibold">수주 등록</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="고객">
          <input className="input w-full" value={customer} onChange={(e) => setCustomer(e.target.value)} />
        </Field>
        <ItemSelect label="품목" items={items} value={item} onChange={setItem} testid="so-item" />
        <Field label="수량">
          <input
            className="input w-full"
            type="number"
            min="1"
            step="1"
            inputMode="numeric"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
          />
        </Field>
        <Field label="수주일">
          <input className="input w-full" type="date" value={orderDate} onChange={(e) => setOrderDate(e.target.value)} />
        </Field>
        <Field label="납기(선택)">
          <input className="input w-full" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
        </Field>
        <Field label="비고(선택)">
          <input className="input w-full" value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
      {dueBeforeOrder && (
        <p className="text-xs text-ng-fg" role="alert">
          <span aria-hidden="true">✕ </span>납기가 수주일보다 앞입니다.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary" disabled={!valid || m.isPending} data-testid="so-submit">
          {m.isPending ? "등록 중…" : "수주 등록"}
        </button>
        <span className="text-xs text-slate-400">수주번호는 자동(SO-날짜-순번)으로 붙습니다.</span>
      </div>
      {result && <ResultLine ok={result.ok}>{result.text}</ResultLine>}
    </form>
  );
}

/* ---------------- 작업지시 등록 ---------------- */

function WorkOrderForm({ items }: { items: ItemMaster[] }): JSX.Element {
  const qc = useQueryClient();
  const openOrders = useQuery({
    queryKey: ["mes", "orders", "OPEN"],
    queryFn: () => fetchSalesOrders("OPEN"),
  });
  const [soId, setSoId] = useState("");
  const [item, setItem] = useState("");
  const [lot, setLot] = useState("");
  const [planQty, setPlanQty] = useState("");
  const [planDate, setPlanDate] = useState(todayIso());
  const [result, setResult] = useState<Msg | null>(null);

  const qtyN = parseCount(planQty);
  // 수주를 고르면 서버가 그 품목을 쓴다 — 그래도 화면에서는 채워서 보여 준다.
  const valid = (item !== "" || soId !== "") && qtyN !== null;

  function pickOrder(id: string): void {
    setSoId(id);
    const so = (openOrders.data ?? []).find((o) => String(o.id) === id);
    if (so) {
      setItem(so.item_code);
      // 남은 수량(수주 − 출하)을 지시수량 초안으로. 이미 적어 둔 값은 덮지 않는다.
      const remaining = so.qty - so.shipped_qty;
      if (planQty.trim() === "" && remaining > 0) setPlanQty(String(remaining));
    }
  }

  const m = useMutation({
    mutationFn: () =>
      createWorkOrder({
        so_id: soId ? Number(soId) : null,
        item_code: item || null,
        lot: orNull(lot),
        plan_qty: qtyN as number,
        plan_date: orNull(planDate),
      }),
    onSuccess: (wo) => {
      setResult({ ok: true, text: `작업지시 ${wo.wo_no} (LOT ${wo.lot}) 를 등록했습니다.` });
      setSoId("");
      setLot("");
      setPlanQty("");
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) => setResult({ ok: false, text: `작업지시 등록 실패: ${errText(e)}` }),
  });

  return (
    <form
      className="card space-y-3 p-4"
      data-testid="wo-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && !m.isPending) m.mutate();
      }}
    >
      <h2 className="font-semibold">작업지시 등록</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="수주(선택)">
          <select
            className="input w-full"
            value={soId}
            onChange={(e) => pickOrder(e.target.value)}
            data-testid="wo-so"
          >
            <option value="">수주 없이</option>
            {(openOrders.data ?? []).map((so) => (
              <option key={so.id} value={String(so.id)}>
                {so.so_no} · {so.customer} · {so.item_code} {fmtNum(so.qty, 0)}개
              </option>
            ))}
          </select>
        </Field>
        <ItemSelect label="품목" items={items} value={item} onChange={setItem} testid="wo-item" />
        <Field label="LOT(선택)">
          <input
            className="input w-full"
            value={lot}
            placeholder="비우면 자동"
            onChange={(e) => setLot(e.target.value)}
            title="학습 데이터 수집용 촬영은 TRAIN- 으로 시작(KPI·MES 제외)"
          />
        </Field>
        <Field label="지시수량">
          <input
            className="input w-full"
            type="number"
            min="1"
            step="1"
            inputMode="numeric"
            value={planQty}
            onChange={(e) => setPlanQty(e.target.value)}
          />
        </Field>
        <Field label="계획일">
          <input className="input w-full" type="date" value={planDate} onChange={(e) => setPlanDate(e.target.value)} />
        </Field>
      </div>
      {openOrders.isError && (
        <p className="text-xs text-ng-fg">
          <span aria-hidden="true">✕ </span>수주 목록을 불러오지 못했습니다: {errText(openOrders.error)}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary" disabled={!valid || m.isPending} data-testid="wo-submit">
          {m.isPending ? "등록 중…" : "작업지시 등록"}
        </button>
        <span className="text-xs text-slate-400">
          LOT 은 작업지시마다 하나입니다. 검사기에는 위 표의 “검사기에 오더 내리기” 로 넘기세요.
        </span>
      </div>
      {result && <ResultLine ok={result.ok}>{result.text}</ResultLine>}
    </form>
  );
}

/* ---------------- 수주 목록 ---------------- */

function SalesOrderList({ editable }: { editable: boolean }): JSX.Element {
  const qc = useQueryClient();
  const orders = useQuery({ queryKey: ["mes", "orders", "all"], queryFn: () => fetchSalesOrders() });
  const [result, setResult] = useState<Msg | null>(null);
  const today = todayIso();

  const close = useMutation({
    mutationFn: (so: SalesOrderOut) => patchSalesOrder(so.id, { status: "CLOSED" }),
    onSuccess: (so) => {
      setResult({ ok: true, text: `수주 ${so.so_no} 를 마감했습니다.` });
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) => setResult({ ok: false, text: `마감 실패: ${errText(e)}` }),
  });

  const rows = orders.data ?? [];

  return (
    <section className="card">
      <h2 className="border-b border-slate-100 px-4 py-3 font-semibold">수주</h2>
      {result && (
        <div className="px-4 pt-3">
          <ResultLine ok={result.ok}>{result.text}</ResultLine>
        </div>
      )}
      {orders.isError ? (
        <div className="p-4">
          <MesError what="수주 조회" error={orders.error} testid="so-error" />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="so-table">
            <thead className={THEAD_CLASS}>
              <tr>
                <Th>수주번호</Th>
                <Th>고객</Th>
                <Th>품목</Th>
                <Th right>수량</Th>
                <Th>수주일</Th>
                <Th>납기</Th>
                <Th right>출하/수량</Th>
                <Th>상태</Th>
                {editable && <Th>작업</Th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((so) => {
                const open = so.status === "OPEN";
                const overdue = open && so.due_date !== null && so.due_date < today && so.shipped_qty < so.qty;
                return (
                  <tr key={so.id} className="border-t border-slate-100" data-testid={`so-row-${so.id}`}>
                    <Td>
                      <span className="whitespace-nowrap font-medium">{so.so_no}</span>
                    </Td>
                    <Td>{so.customer}</Td>
                    <Td>{so.item_code}</Td>
                    <Td right>{fmtNum(so.qty, 0)}</Td>
                    <Td>
                      <span className="whitespace-nowrap tabular-nums">{so.order_date}</span>
                    </Td>
                    <Td>
                      <span className="whitespace-nowrap tabular-nums">{so.due_date ?? "-"}</span>
                      {overdue && (
                        <span className="ml-1 whitespace-nowrap text-xs font-semibold text-amber-800">
                          <span aria-hidden="true">⚠ </span>납기 지남
                        </span>
                      )}
                    </Td>
                    <Td right>
                      {fmtNum(so.shipped_qty, 0)} / {fmtNum(so.qty, 0)}
                    </Td>
                    <Td>
                      <StatusBadge view={soStatusView(so.status)} />
                    </Td>
                    {editable && (
                      <Td>
                        {open ? (
                          <RowButton
                            disabled={close.isPending}
                            onClick={() => close.mutate(so)}
                            testid={`so-close-${so.id}`}
                          >
                            마감
                          </RowButton>
                        ) : (
                          <span className="text-slate-400">-</span>
                        )}
                      </Td>
                    )}
                  </tr>
                );
              })}
              {rows.length === 0 && !orders.isFetching && (
                <EmptyRow colSpan={editable ? 9 : 8} testid="so-empty">
                  수주가 없습니다.
                </EmptyRow>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
