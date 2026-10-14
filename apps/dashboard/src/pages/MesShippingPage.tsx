/**
 * MES 출하·클레임 — 월 출하 실적, 리드타임·납기 준수, 클레임(docs/MES.md §6.2).
 *
 * AIVIS KPI 의 수기 항목(Claim·리드타임·출하수량·출하유출 부적합)은 원래 사람이 KPI
 * 화면에 옮겨 적어야 했다. MES 가 그 원장이 되었으므로 "AIVIS KPI 에 반영" 한 번으로
 * 이 달 값을 넘긴다. 작업공수·비고는 MES 가 모르는 값이라 보내지 않는다 — 서버가
 * 부분 갱신이라 보내지 않은 칸은 그대로 남는다.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ItemMaster } from "@aivis/shared-types";
import { upsertKpiManual } from "@/api/endpoints";
import {
  createClaim,
  createShipment,
  fetchClaims,
  fetchMonthlyKpi,
  fetchSalesOrders,
  fetchShipments,
  patchClaim,
  type ClaimOut,
  type ClaimStatus,
  type MonthlyKpi,
} from "@/api/mes";
import {
  EmptyRow,
  Field,
  ItemSelect,
  MesError,
  OnTimeMark,
  ResultLine,
  StatusBadge,
  THEAD_CLASS,
  Td,
  Th,
  useAivisItems,
} from "@/components/MesParts";
import { currentPeriod, fmtNum } from "@/lib/format";
import {
  CLAIM_DEFECT_OPTIONS,
  claimStatusView,
  defectLabel,
  orNull,
  parseCount,
  todayIso,
} from "@/lib/mes";
import { canEdit, useAuthStore } from "@/store/auth";

interface Msg {
  ok: boolean;
  text: string;
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function MesShippingPage(): JSX.Element {
  const role = useAuthStore((s) => s.role);
  const editable = canEdit(role);
  const { items } = useAivisItems();
  const [period, setPeriod] = useState(currentPeriod());

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">출하·클레임</h1>
        <input
          type="month"
          className="input"
          value={period}
          aria-label="조회 월"
          // 지우기(빈 값)는 무시 — 빈 period 는 서버에서 '이번 달' 로 바뀌어 화면 표기와 어긋난다.
          onChange={(e) => e.target.value && setPeriod(e.target.value)}
          data-testid="ship-period"
        />
        {!editable && (
          <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500">
            읽기 전용 (등록·종결은 품질관리자 이상)
          </span>
        )}
      </div>

      <MonthlyKpiCard period={period} editable={editable} />
      <ShipmentSection period={period} editable={editable} items={items} />
      <ClaimSection editable={editable} items={items} />
    </div>
  );
}

/* ---------------- 월 KPI ---------------- */

function MonthlyKpiCard({ period, editable }: { period: string; editable: boolean }): JSX.Element {
  const qc = useQueryClient();
  const kpi = useQuery({
    queryKey: ["mes", "kpi-monthly", period],
    queryFn: () => fetchMonthlyKpi(period),
  });
  // 반영 결과는 그 달에만 붙인다 — 월을 바꿨는데 "반영했습니다" 가 남아 있으면 착각한다.
  const [result, setResult] = useState<(Msg & { period: string }) | null>(null);

  const reflect = useMutation({
    mutationFn: (k: MonthlyKpi) =>
      // 4개 칸 + 월만 보낸다. workload_index·note 를 실으면(null 이라도) 사람이 KPI
      // 화면에 적어 둔 작업공수·비고를 지운다.
      upsertKpiManual({
        period: `${k.period}-01`,
        claim_count: k.claim_count,
        lead_time_days: k.lead_time_days,
        shipped_qty: k.shipped_qty,
        leak_defect_qty: k.leak_defect_qty,
      }),
    onSuccess: (_d, k) => {
      setResult({ ok: true, text: "KPI 에 반영했습니다(작업공수·비고는 그대로)", period: k.period });
      qc.invalidateQueries({ queryKey: ["kpi-summary", k.period] });
    },
    onError: (e, k) => setResult({ ok: false, text: `KPI 반영 실패: ${errText(e)}`, period: k.period }),
  });

  const k = kpi.data;

  return (
    <section className="card p-4" data-testid="mes-kpi">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h2 className="font-semibold">월 출하 KPI ({period})</h2>
        {kpi.isFetching && <span className="text-sm text-slate-400">불러오는 중…</span>}
        {editable && (
          <button
            type="button"
            className="btn-primary ml-auto"
            disabled={!k || reflect.isPending}
            onClick={() => k && reflect.mutate(k)}
            data-testid="kpi-reflect"
          >
            {reflect.isPending ? "반영 중…" : "AIVIS KPI 에 반영"}
          </button>
        )}
      </div>

      {kpi.isError ? (
        <MesError what="월 KPI 조회" error={kpi.error} testid="mes-kpi-error" />
      ) : k ? (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm md:grid-cols-4 xl:grid-cols-7">
          <Stat k="출하 건수" v={fmtNum(k.shipment_count, 0)} testid="kpi-shipment-count" />
          <Stat k="출하 수량" v={fmtNum(k.shipped_qty, 0)} testid="kpi-shipped-qty" />
          <Stat
            k="평균 리드타임(일)"
            v={fmtNum(k.lead_time_days, 1)}
            sub={`표본 ${fmtNum(k.lead_time_samples, 0)}건`}
          />
          <Stat
            k="납기 준수율"
            v={k.on_time_rate_pct === null ? "-" : `${fmtNum(k.on_time_rate_pct, 1)}%`}
            sub={`표본 ${fmtNum(k.on_time_samples, 0)}건`}
          />
          <Stat k="Claim 건수" v={fmtNum(k.claim_count, 0)} testid="kpi-claim-count" />
          <Stat k="출하유출 부적합 수량" v={fmtNum(k.leak_defect_qty, 0)} />
          <Stat k="출하유출불량률(ppm)" v={fmtNum(k.leak_ppm, 1)} />
        </dl>
      ) : null}

      {editable && (
        <p className="mt-3 text-xs text-slate-400">
          “AIVIS KPI 에 반영” 은 이 달 AIVIS KPI 수기 입력의 Claim·리드타임·출하수량·출하유출
          부적합 네 칸을 위 값으로 바꿉니다. 작업공수·비고는 건드리지 않습니다.
          {k && k.lead_time_days === null && " 이 달은 리드타임 표본이 없어 리드타임 칸이 비워집니다."}
        </p>
      )}
      {result && result.period === period && (
        <div className="mt-2">
          <ResultLine ok={result.ok} testid="kpi-reflect-msg">
            {result.text}
          </ResultLine>
        </div>
      )}
    </section>
  );
}

function Stat({
  k,
  v,
  sub,
  testid,
}: {
  k: string;
  v: string;
  sub?: string;
  testid?: string;
}): JSX.Element {
  return (
    <div>
      <dt className="text-xs text-slate-400">{k}</dt>
      <dd className="text-lg font-semibold tabular-nums" data-testid={testid}>
        {v}
      </dd>
      {sub && <dd className="text-xs text-slate-400">{sub}</dd>}
    </div>
  );
}

/* ---------------- 출하 ---------------- */

function ShipmentSection({
  period,
  editable,
  items,
}: {
  period: string;
  editable: boolean;
  items: ItemMaster[];
}): JSX.Element {
  const q = useQuery({
    queryKey: ["mes", "shipments", period],
    queryFn: () => fetchShipments(period),
  });
  const rows = q.data ?? [];
  return (
    <section className="card">
      <h2 className="border-b border-slate-100 px-4 py-3 font-semibold">출하 ({period})</h2>
      {q.isError ? (
        <div className="p-4">
          <MesError what="출하 조회" error={q.error} testid="ship-error" />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="ship-table">
            <thead className={THEAD_CLASS}>
              <tr>
                <Th>출하번호</Th>
                <Th>출하일</Th>
                <Th>수주</Th>
                <Th>고객</Th>
                <Th>품목</Th>
                <Th>LOT</Th>
                <Th right>수량</Th>
                <Th right>리드타임(일)</Th>
                <Th>납기</Th>
                <Th>비고</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} className="border-t border-slate-100" data-testid={`ship-row-${s.id}`}>
                  <Td>
                    <span className="whitespace-nowrap font-medium">{s.ship_no}</span>
                  </Td>
                  <Td>
                    <span className="whitespace-nowrap tabular-nums">{s.ship_date}</span>
                  </Td>
                  <Td>{s.so_no ?? <span className="text-slate-400">-</span>}</Td>
                  <Td>{s.customer}</Td>
                  <Td>{s.item_code}</Td>
                  <Td>{s.lot ?? "-"}</Td>
                  <Td right>{fmtNum(s.qty, 0)}</Td>
                  <Td right>{fmtNum(s.lead_time_days, 0)}</Td>
                  <Td testid={`ship-ontime-${s.id}`}>
                    <OnTimeMark onTime={s.on_time} />
                  </Td>
                  <Td>{s.note ?? ""}</Td>
                </tr>
              ))}
              {rows.length === 0 && !q.isFetching && (
                <EmptyRow colSpan={10} testid="ship-empty">
                  이 달 출하가 없습니다.
                </EmptyRow>
              )}
            </tbody>
          </table>
        </div>
      )}
      {editable && <ShipmentForm items={items} />}
    </section>
  );
}

function ShipmentForm({ items }: { items: ItemMaster[] }): JSX.Element {
  const qc = useQueryClient();
  const openOrders = useQuery({
    queryKey: ["mes", "orders", "OPEN"],
    queryFn: () => fetchSalesOrders("OPEN"),
  });
  const [soId, setSoId] = useState("");
  const [customer, setCustomer] = useState("");
  const [item, setItem] = useState("");
  const [lot, setLot] = useState("");
  const [qty, setQty] = useState("");
  const [shipDate, setShipDate] = useState(todayIso());
  const [note, setNote] = useState("");
  const [result, setResult] = useState<Msg | null>(null);

  const qtyN = parseCount(qty);
  // 수주를 고르면 고객·품목은 서버가 수주에서 채운다. 수주 없이 출하하면 둘 다 필요.
  const valid = qtyN !== null && shipDate !== "" && (soId !== "" || (customer.trim() !== "" && item !== ""));

  function pickOrder(id: string): void {
    setSoId(id);
    const so = (openOrders.data ?? []).find((o) => String(o.id) === id);
    if (so) {
      setCustomer(so.customer);
      setItem(so.item_code);
      const remaining = so.qty - so.shipped_qty;
      if (qty.trim() === "" && remaining > 0) setQty(String(remaining));
    }
  }

  const m = useMutation({
    mutationFn: () =>
      createShipment({
        so_id: soId ? Number(soId) : null,
        customer: orNull(customer),
        item_code: item || null,
        lot: orNull(lot),
        qty: qtyN as number,
        ship_date: shipDate,
        note: orNull(note),
      }),
    onSuccess: (s) => {
      const lead = s.lead_time_days === null ? "" : ` · 리드타임 ${s.lead_time_days}일`;
      const due = s.on_time === null ? "" : s.on_time ? " · 납기 내" : " · 납기 초과";
      setResult({ ok: true, text: `출하 ${s.ship_no} 를 등록했습니다${lead}${due}.` });
      setSoId("");
      setLot("");
      setQty("");
      setNote("");
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) => setResult({ ok: false, text: `출하 등록 실패: ${errText(e)}` }),
  });

  return (
    <form
      className="space-y-3 border-t border-slate-100 p-4"
      data-testid="ship-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && !m.isPending) m.mutate();
      }}
    >
      <h3 className="font-semibold">출하 등록</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="수주(선택)">
          <select
            className="input w-full"
            value={soId}
            onChange={(e) => pickOrder(e.target.value)}
            data-testid="ship-so"
          >
            <option value="">수주 없이</option>
            {(openOrders.data ?? []).map((so) => (
              <option key={so.id} value={String(so.id)}>
                {so.so_no} · {so.customer} · {so.item_code} ({fmtNum(so.shipped_qty, 0)}/{fmtNum(so.qty, 0)})
              </option>
            ))}
          </select>
        </Field>
        <Field label="고객">
          <input className="input w-full" value={customer} onChange={(e) => setCustomer(e.target.value)} />
        </Field>
        <ItemSelect label="품목" items={items} value={item} onChange={setItem} testid="ship-item" />
        <Field label="LOT(선택)">
          <input className="input w-full" value={lot} onChange={(e) => setLot(e.target.value)} />
        </Field>
        <Field label="출하 수량">
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
        <Field label="출하일">
          <input className="input w-full" type="date" value={shipDate} onChange={(e) => setShipDate(e.target.value)} />
        </Field>
        <Field label="비고(선택)">
          <input className="input w-full" value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary" disabled={!valid || m.isPending} data-testid="ship-submit">
          {m.isPending ? "등록 중…" : "출하 등록"}
        </button>
        <span className="text-xs text-slate-400">
          수주를 고르면 리드타임(수주일→출하일)과 납기 준수가 계산됩니다.
        </span>
      </div>
      {result && <ResultLine ok={result.ok}>{result.text}</ResultLine>}
    </form>
  );
}

/* ---------------- 클레임 ---------------- */

const CLAIM_FILTERS: Array<{ value: "" | ClaimStatus; label: string }> = [
  { value: "", label: "전체" },
  { value: "OPEN", label: "처리 중" },
  { value: "CLOSED", label: "종결" },
];

function ClaimSection({ editable, items }: { editable: boolean; items: ItemMaster[] }): JSX.Element {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<"" | ClaimStatus>("");
  const q = useQuery({
    queryKey: ["mes", "claims", filter],
    queryFn: () => fetchClaims(filter || undefined),
  });
  const [result, setResult] = useState<Msg | null>(null);

  const close = useMutation({
    mutationFn: (c: ClaimOut) => patchClaim(c.id, { status: "CLOSED" }),
    onSuccess: (c) => {
      setResult({ ok: true, text: `클레임 ${c.claim_no} 를 종결했습니다.` });
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) => setResult({ ok: false, text: `종결 실패: ${errText(e)}` }),
  });

  const rows = q.data ?? [];
  return (
    <section className="card">
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-3">
        <h2 className="font-semibold">클레임</h2>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          상태
          <select
            className="input"
            value={filter}
            onChange={(e) => setFilter(e.target.value as "" | ClaimStatus)}
            data-testid="claim-filter"
          >
            {CLAIM_FILTERS.map((f) => (
              <option key={f.label} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {result && (
        <div className="px-4 pt-3">
          <ResultLine ok={result.ok}>{result.text}</ResultLine>
        </div>
      )}
      {q.isError ? (
        <div className="p-4">
          <MesError what="클레임 조회" error={q.error} testid="claim-error" />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="claim-table">
            <thead className={THEAD_CLASS}>
              <tr>
                <Th>클레임번호</Th>
                <Th>발생일</Th>
                <Th>고객</Th>
                <Th>LOT</Th>
                <Th>품목</Th>
                <Th right>부적합 수량</Th>
                <Th>유형</Th>
                <Th>내용</Th>
                <Th>상태</Th>
                {editable && <Th>작업</Th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="border-t border-slate-100" data-testid={`claim-row-${c.id}`}>
                  <Td>
                    <span className="whitespace-nowrap font-medium">{c.claim_no}</span>
                  </Td>
                  <Td>
                    <span className="whitespace-nowrap tabular-nums">{c.occurred_date}</span>
                  </Td>
                  <Td>{c.customer}</Td>
                  <Td>{c.lot ?? "-"}</Td>
                  <Td>{c.item_code ?? "-"}</Td>
                  <Td right>{fmtNum(c.defect_qty, 0)}</Td>
                  <Td>{defectLabel(c.defect_type)}</Td>
                  <Td>
                    <span className="block max-w-xs break-words">{c.description ?? ""}</span>
                  </Td>
                  <Td>
                    <StatusBadge view={claimStatusView(c.status)} />
                  </Td>
                  {editable && (
                    <Td>
                      {c.status === "OPEN" ? (
                        <button
                          type="button"
                          className="btn-ghost whitespace-nowrap px-2 py-1 text-xs"
                          disabled={close.isPending}
                          onClick={() => close.mutate(c)}
                          data-testid={`claim-close-${c.id}`}
                        >
                          종결
                        </button>
                      ) : (
                        <span className="text-slate-400">-</span>
                      )}
                    </Td>
                  )}
                </tr>
              ))}
              {rows.length === 0 && !q.isFetching && (
                <EmptyRow colSpan={editable ? 10 : 9} testid="claim-empty">
                  클레임이 없습니다.
                </EmptyRow>
              )}
            </tbody>
          </table>
        </div>
      )}
      {editable && <ClaimForm items={items} />}
    </section>
  );
}

function ClaimForm({ items }: { items: ItemMaster[] }): JSX.Element {
  const qc = useQueryClient();
  const [customer, setCustomer] = useState("");
  const [lot, setLot] = useState("");
  const [item, setItem] = useState("");
  const [defectQty, setDefectQty] = useState("0");
  const [defectType, setDefectType] = useState("");
  const [occurred, setOccurred] = useState(todayIso());
  const [description, setDescription] = useState("");
  const [result, setResult] = useState<Msg | null>(null);

  const qtyN = parseCount(defectQty, 0);
  const valid = customer.trim() !== "" && qtyN !== null && occurred !== "";

  const m = useMutation({
    mutationFn: () =>
      createClaim({
        customer: customer.trim(),
        lot: orNull(lot),
        item_code: item || null,
        defect_qty: qtyN as number,
        defect_type: defectType || null,
        occurred_date: occurred,
        description: orNull(description),
      }),
    onSuccess: (c) => {
      setResult({ ok: true, text: `클레임 ${c.claim_no} 를 등록했습니다.` });
      setCustomer("");
      setLot("");
      setItem("");
      setDefectQty("0");
      setDefectType("");
      setDescription("");
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) => setResult({ ok: false, text: `클레임 등록 실패: ${errText(e)}` }),
  });

  return (
    <form
      className="space-y-3 border-t border-slate-100 p-4"
      data-testid="claim-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && !m.isPending) m.mutate();
      }}
    >
      <h3 className="font-semibold">클레임 등록</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="고객">
          <input className="input w-full" value={customer} onChange={(e) => setCustomer(e.target.value)} />
        </Field>
        <Field label="LOT(선택)">
          <input className="input w-full" value={lot} onChange={(e) => setLot(e.target.value)} />
        </Field>
        <ItemSelect label="품목(선택)" items={items} value={item} onChange={setItem} optional testid="claim-item" />
        <Field label="부적합 수량">
          <input
            className="input w-full"
            type="number"
            min="0"
            step="1"
            inputMode="numeric"
            value={defectQty}
            onChange={(e) => setDefectQty(e.target.value)}
          />
        </Field>
        <Field label="불량유형">
          <select
            className="input w-full"
            value={defectType}
            onChange={(e) => setDefectType(e.target.value)}
            data-testid="claim-type"
          >
            <option value="">선택 안 함</option>
            {CLAIM_DEFECT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label} ({o.value})
              </option>
            ))}
          </select>
        </Field>
        <Field label="발생일">
          <input className="input w-full" type="date" value={occurred} onChange={(e) => setOccurred(e.target.value)} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="내용(선택)">
            <input className="input w-full" value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary" disabled={!valid || m.isPending} data-testid="claim-submit">
          {m.isPending ? "등록 중…" : "클레임 등록"}
        </button>
        <span className="text-xs text-slate-400">
          부적합 수량은 발생일이 속한 달의 출하유출불량률(ppm) 분자가 됩니다.
        </span>
      </div>
      {result && <ResultLine ok={result.ok}>{result.text}</ResultLine>}
    </form>
  );
}
