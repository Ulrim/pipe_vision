/**
 * MES 자재·재고 — 품목별 현재고, 입고, 조정, 수불 이력 (docs/MES.md §11.1, §11.2, §11.5).
 *
 * 재고는 저장된 숫자가 아니라 **수불 수량의 합**이다. 그래서 숫자를 고칠 때도 덮어쓰지 않고
 * 조정(ADJUST) 한 건을 더한다 — 사유를 꼭 남긴다. 투입·생산·출하는 공정진행·작업지시·출하
 * 화면에서 자동으로 기록된다.
 *
 * 권한: 조회·입고는 작업자도, 조정은 품질관리자 이상.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createAdjustment,
  createReceipt,
  fetchMesItems,
  fetchStock,
  fetchStockTxns,
  type ItemOut,
  type ItemType,
} from "@/api/mesPlant";
import {
  EmptyRow,
  Field,
  MesError,
  ResultLine,
  THEAD_CLASS,
  Td,
  Th,
} from "@/components/MesParts";
import { fmtDateTime } from "@/lib/format";
import { orNull } from "@/lib/mes";
import {
  ITEM_TYPE_OPTIONS,
  errText,
  fmtQty,
  fmtSignedQty,
  itemTypeLabel,
  parseQty,
  txnTypeLabel,
  type Msg,
} from "@/lib/mesPlant";
import { canEdit, useAuthStore } from "@/store/auth";

/** 수불 이력은 최근 것만(서버 페이지네이션 없음 — limit 만). */
const TXN_LIMIT = 200;

export function MesStockPage(): JSX.Element {
  const editable = canEdit(useAuthStore((s) => s.role));
  const [type, setType] = useState("");
  const [txnItem, setTxnItem] = useState("");

  const stock = useQuery({
    queryKey: ["mes", "stock", type],
    queryFn: () => fetchStock((type || undefined) as ItemType | undefined),
  });
  // 입고·조정 품목 선택지(사용 중인 것만) + 이력 필터.
  const items = useQuery({
    queryKey: ["mes", "items", "active"],
    queryFn: () => fetchMesItems({ active: true }),
  });
  const txns = useQuery({
    queryKey: ["mes", "stock-txns", txnItem],
    queryFn: () => fetchStockTxns({ item_code: txnItem || undefined, limit: TXN_LIMIT }),
  });

  const rows = stock.data ?? [];
  const itemList = items.data ?? [];
  const below = rows.filter((r) => r.below_safety).length;
  const txnRows = txns.data ?? [];
  // 이력 필터에는 기준정보에 없는데 수불만 있는 품목도 넣는다(현재고 표에 나오는 것과 같게).
  const filterCodes = Array.from(
    new Set([...itemList.map((i) => i.item_code), ...rows.map((r) => r.item_code), ...(txnItem ? [txnItem] : [])]),
  ).sort();

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">자재·재고</h1>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          유형
          <select
            className="input"
            value={type}
            onChange={(e) => setType(e.target.value)}
            data-testid="stock-type"
          >
            <option value="">전체</option>
            {ITEM_TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        {below > 0 && (
          <span
            className="rounded bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900"
            data-testid="stock-below-count"
          >
            <span aria-hidden="true">⚠ </span>안전재고 미달 {below}품목
          </span>
        )}
        {stock.isFetching && <span className="text-sm text-slate-400">불러오는 중…</span>}
      </div>

      <section className="card">
        <h2 className="border-b border-slate-100 px-4 py-3 font-semibold">품목별 현재고</h2>
        {stock.isError ? (
          <div className="p-4">
            <MesError what="재고 조회" error={stock.error} testid="stock-error" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="stock-table">
              <thead className={THEAD_CLASS}>
                <tr>
                  <Th>품목</Th>
                  <Th>이름</Th>
                  <Th>유형</Th>
                  <Th right>현재고</Th>
                  <Th right>안전재고</Th>
                  <Th>상태</Th>
                  <Th>마지막 수불</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.item_code}
                    className={`border-t border-slate-100 ${r.below_safety ? "bg-amber-50" : ""}`}
                    data-testid={`stock-row-${r.item_code}`}
                  >
                    <Td>
                      <span className="whitespace-nowrap font-medium">{r.item_code}</span>
                    </Td>
                    <Td>
                      {r.name ?? <span className="text-slate-400">기준정보에 없는 품목</span>}
                    </Td>
                    <Td>{itemTypeLabel(r.item_type)}</Td>
                    <Td right>
                      <span className={r.on_hand < 0 ? "font-semibold text-ng-fg" : ""}>
                        {r.on_hand < 0 ? fmtSignedQty(r.on_hand) : fmtQty(r.on_hand)}
                      </span>
                      {r.unit && <span className="text-xs text-slate-400"> {r.unit}</span>}
                    </Td>
                    <Td right>{fmtQty(r.safety_stock)}</Td>
                    <Td testid={`stock-state-${r.item_code}`}>
                      {r.below_safety ? (
                        <span className="whitespace-nowrap font-semibold text-amber-900">
                          <span aria-hidden="true">⚠ </span>안전재고 미달
                        </span>
                      ) : (
                        <span className="whitespace-nowrap text-ok-fg">
                          <span aria-hidden="true">✓ </span>정상
                        </span>
                      )}
                    </Td>
                    <Td>
                      <span className="whitespace-nowrap tabular-nums">{fmtDateTime(r.last_txn_at)}</span>
                    </Td>
                  </tr>
                ))}
                {rows.length === 0 && !stock.isFetching && (
                  <EmptyRow colSpan={7} testid="stock-empty">
                    재고가 없습니다 — MES 기준정보에 품목을 등록하고 입고를 넣으세요.
                  </EmptyRow>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <StockForm kind="receipt" items={itemList} />
        {editable && <StockForm kind="adjust" items={itemList} />}
        {items.isError && (
          <div className="lg:col-span-2">
            <MesError what="품목 조회" error={items.error} testid="stock-items-error" />
          </div>
        )}
      </div>

      <section className="card">
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-3">
          <h2 className="font-semibold">수불 이력</h2>
          <label className="flex items-center gap-2 text-sm text-slate-600">
            품목
            <select
              className="input"
              value={txnItem}
              onChange={(e) => setTxnItem(e.target.value)}
              data-testid="txn-item"
            >
              <option value="">전체</option>
              {filterCodes.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          <span className="text-xs text-slate-400">최근 {TXN_LIMIT}건</span>
        </div>
        {txns.isError ? (
          <div className="p-4">
            <MesError what="수불 이력 조회" error={txns.error} testid="txn-error" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="txn-table">
              <thead className={THEAD_CLASS}>
                <tr>
                  <Th>시각</Th>
                  <Th>품목</Th>
                  <Th>유형</Th>
                  <Th right>수량</Th>
                  <Th>LOT</Th>
                  <Th>작업지시</Th>
                  <Th>비고</Th>
                  <Th>등록자</Th>
                </tr>
              </thead>
              <tbody>
                {txnRows.map((t) => (
                  <tr key={t.id} className="border-t border-slate-100" data-testid={`txn-row-${t.id}`}>
                    <Td>
                      <span className="whitespace-nowrap tabular-nums">{fmtDateTime(t.ts)}</span>
                    </Td>
                    <Td>{t.item_code}</Td>
                    <Td>{txnTypeLabel(t.txn_type)}</Td>
                    <Td right testid={`txn-qty-${t.id}`}>
                      <span className={t.qty < 0 ? "text-ng-fg" : "text-ok-fg"}>{fmtSignedQty(t.qty)}</span>
                    </Td>
                    <Td>{t.lot ?? "-"}</Td>
                    <Td>{t.wo_no ?? (t.wo_id !== null ? `#${t.wo_id}` : "-")}</Td>
                    <Td>{t.note ?? "-"}</Td>
                    <Td>{t.created_by ?? "-"}</Td>
                  </tr>
                ))}
                {txnRows.length === 0 && !txns.isFetching && (
                  <EmptyRow colSpan={8} testid="txn-empty">
                    수불 이력이 없습니다.
                  </EmptyRow>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

/* ---------------- 입고 / 조정 ---------------- */

function StockForm({ kind, items }: { kind: "receipt" | "adjust"; items: ItemOut[] }): JSX.Element {
  const qc = useQueryClient();
  const adjust = kind === "adjust";
  const title = adjust ? "재고 조정" : "입고";
  const [item, setItem] = useState("");
  const [qty, setQty] = useState("");
  const [lot, setLot] = useState("");
  const [note, setNote] = useState("");
  const [result, setResult] = useState<Msg | null>(null);

  // 입고는 > 0, 조정은 부호 있는 수량(0 제외).
  const qtyN = adjust ? parseQty(qty) : parseQty(qty, { min: 0, exclusiveMin: true });
  const qtyOk = qtyN !== null && qtyN !== 0;
  const valid = item !== "" && qtyOk && (!adjust || note.trim() !== "");
  const unit = items.find((i) => i.item_code === item)?.unit;

  const m = useMutation({
    mutationFn: () =>
      adjust
        ? createAdjustment({ item_code: item, qty: qtyN as number, lot: orNull(lot), note: note.trim() })
        : createReceipt({ item_code: item, qty: qtyN as number, lot: orNull(lot), note: orNull(note) }),
    onSuccess: (t) => {
      setResult({
        ok: true,
        text: `${t.item_code} ${fmtSignedQty(t.qty)} — ${title} 을(를) 기록했습니다.`,
      });
      setQty("");
      setLot("");
      setNote("");
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) => setResult({ ok: false, text: `${title} 실패: ${errText(e)}` }),
  });

  return (
    <form
      className="card space-y-3 p-4"
      data-testid={`${kind}-form`}
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && !m.isPending) m.mutate();
      }}
    >
      <h2 className="font-semibold">{title}</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="품목">
          <select
            className="input w-full"
            value={item}
            onChange={(e) => setItem(e.target.value)}
            data-testid={`${kind}-item`}
          >
            <option value="">선택…</option>
            {items.map((it) => (
              <option key={it.item_code} value={it.item_code}>
                {it.item_code} — {it.name} ({itemTypeLabel(it.item_type)})
              </option>
            ))}
          </select>
        </Field>
        <Field label={adjust ? `조정 수량(+ 늘림 / − 줄임)${unit ? ` · ${unit}` : ""}` : `수량${unit ? ` · ${unit}` : ""}`}>
          <input
            className="input w-full"
            type="number"
            step="any"
            {...(adjust ? {} : { min: "0" })}
            inputMode="decimal"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            data-testid={`${kind}-qty`}
          />
        </Field>
        <Field label="LOT(선택)">
          <input className="input w-full" value={lot} onChange={(e) => setLot(e.target.value)} />
        </Field>
        <Field label={adjust ? "사유(필수)" : "비고(선택)"}>
          <input
            className="input w-full"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            data-testid={`${kind}-note`}
          />
        </Field>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary" disabled={!valid || m.isPending} data-testid={`${kind}-submit`}>
          {m.isPending ? "기록 중…" : adjust ? "조정 기록" : "입고 등록"}
        </button>
        <span className="text-xs text-slate-400">
          {adjust
            ? "실사 결과와 맞출 때 씁니다. 현재고를 덮어쓰지 않고 차이만큼 더하거나 뺍니다."
            : "MES 기준정보에 등록된 품목만 입고할 수 있습니다."}
        </span>
      </div>
      {result && (
        <ResultLine ok={result.ok} testid={`${kind}-result`}>
          {result.text}
        </ResultLine>
      )}
    </form>
  );
}
