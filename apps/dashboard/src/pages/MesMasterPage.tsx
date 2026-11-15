/**
 * MES 기준정보 — 품목·BOM / 공정·라우팅 / 설비 (docs/MES.md §11.1, §11.5).
 *
 * AIVIS 기준정보(/master — 검사 기준길이·공차·임계값)와는 다른 것이다. 여기 품목은 MES 가
 * 재고·BOM·라우팅을 세는 단위(원자재·제품·부자재)다.
 *
 * BOM 과 라우팅은 서버가 **통째로 교체**(PUT)한다 — 그래서 화면에서 줄을 다 고친 뒤
 * [저장] 한 번으로 보낸다. 빈 목록으로 저장하면 삭제다.
 *
 * 권한: 조회는 작업자도, 등록·수정은 품질관리자 이상(서버도 같은 규칙).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ROUTING_DEFAULT,
  createEquipment,
  createMesItem,
  createProcess,
  fetchBom,
  fetchEquipment,
  fetchMesItems,
  fetchProcesses,
  fetchRouting,
  patchEquipment,
  patchMesItem,
  patchProcess,
  putBom,
  putRouting,
  type BomLineOut,
  type EquipmentOut,
  type ItemOut,
  type ItemType,
  type ProcessOut,
  type RoutingOut,
} from "@/api/mesPlant";
import {
  EmptyRow,
  Field,
  MesError,
  ResultLine,
  RowButton,
  StatusBadge,
  THEAD_CLASS,
  Td,
  Th,
} from "@/components/MesParts";
import { orNull, parseCount } from "@/lib/mes";
import {
  ITEM_TYPE_OPTIONS,
  errText,
  fmtQty,
  itemTypeLabel,
  parseQty,
  routingSourceView,
  type Msg,
} from "@/lib/mesPlant";
import { canEdit, useAuthStore } from "@/store/auth";

type Tab = "items" | "routing" | "equipment";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "items", label: "품목·BOM" },
  { id: "routing", label: "공정·라우팅" },
  { id: "equipment", label: "설비" },
];

export function MesMasterPage(): JSX.Element {
  const editable = canEdit(useAuthStore((s) => s.role));
  const [tab, setTab] = useState<Tab>("items");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">품목·공정·설비</h1>
        <div className="flex gap-1" role="tablist" aria-label="기준정보 종류">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`${tab === t.id ? "btn-primary" : "btn-ghost"} px-3 py-1.5`}
              onClick={() => setTab(t.id)}
              data-testid={`tab-${t.id}`}
            >
              {t.label}
            </button>
          ))}
        </div>
        {!editable && (
          <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500">
            작업자: 조회만 할 수 있습니다(등록·수정은 품질관리자 이상)
          </span>
        )}
      </div>

      {tab === "items" && <ItemsTab editable={editable} />}
      {tab === "routing" && <RoutingTab editable={editable} />}
      {tab === "equipment" && <EquipmentTab editable={editable} />}
    </div>
  );
}

function ActiveMark({ active }: { active: boolean }): JSX.Element {
  return active ? (
    <span className="whitespace-nowrap text-ok-fg">
      <span aria-hidden="true">✓ </span>사용
    </span>
  ) : (
    <span className="whitespace-nowrap text-slate-400">
      <span aria-hidden="true">— </span>사용 중지
    </span>
  );
}

/* ================= 품목·BOM ================= */

function ItemsTab({ editable }: { editable: boolean }): JSX.Element {
  const items = useQuery({ queryKey: ["mes", "items"], queryFn: () => fetchMesItems() });
  const [editing, setEditing] = useState<ItemOut | null>(null);
  const [bomParent, setBomParent] = useState("");
  const rows = items.data ?? [];

  return (
    <>
      <section className="card">
        <h2 className="border-b border-slate-100 px-4 py-3 font-semibold">품목</h2>
        {items.isError ? (
          <div className="p-4">
            <MesError what="품목 조회" error={items.error} testid="item-error" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="item-table">
              <thead className={THEAD_CLASS}>
                <tr>
                  <Th>품목 코드</Th>
                  <Th>이름</Th>
                  <Th>유형</Th>
                  <Th>단위</Th>
                  <Th right>안전재고</Th>
                  <Th>규격</Th>
                  <Th>사용</Th>
                  <Th>작업</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((it) => (
                  <tr
                    key={it.item_code}
                    className={`border-t border-slate-100 ${it.active ? "" : "text-slate-400"}`}
                    data-testid={`item-row-${it.item_code}`}
                  >
                    <Td>
                      <span className="whitespace-nowrap font-medium">{it.item_code}</span>
                    </Td>
                    <Td>{it.name}</Td>
                    <Td>{itemTypeLabel(it.item_type)}</Td>
                    <Td>{it.unit}</Td>
                    <Td right>{fmtQty(it.safety_stock)}</Td>
                    <Td>{it.spec ?? "-"}</Td>
                    <Td>
                      <ActiveMark active={it.active} />
                    </Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {it.item_type === "FG" && (
                          <RowButton
                            primary={bomParent === it.item_code}
                            onClick={() => setBomParent(it.item_code)}
                            testid={`item-bom-${it.item_code}`}
                          >
                            BOM
                          </RowButton>
                        )}
                        {editable && (
                          <RowButton onClick={() => setEditing(it)} testid={`item-edit-${it.item_code}`}>
                            수정
                          </RowButton>
                        )}
                      </div>
                    </Td>
                  </tr>
                ))}
                {rows.length === 0 && !items.isFetching && (
                  <EmptyRow colSpan={8} testid="item-empty">
                    등록된 품목이 없습니다.{editable && " 아래에서 품목을 등록하세요."}
                  </EmptyRow>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {editable && (
        <ItemForm
          // 수정 대상이 바뀌면 폼을 새로 만든다(이전 입력이 남지 않게).
          key={editing?.item_code ?? "new"}
          editing={editing}
          onDone={() => setEditing(null)}
        />
      )}

      {bomParent ? (
        <BomSection key={bomParent} parent={bomParent} items={rows} editable={editable} />
      ) : (
        <p className="text-sm text-slate-500" data-testid="bom-hint">
          제품 줄의 [BOM] 을 누르면 그 제품 1개를 만드는 데 드는 자재(BOM)를 봅니다.
        </p>
      )}
    </>
  );
}

function ItemForm({
  editing,
  onDone,
}: {
  editing: ItemOut | null;
  onDone: () => void;
}): JSX.Element {
  const qc = useQueryClient();
  const [code, setCode] = useState(editing?.item_code ?? "");
  const [name, setName] = useState(editing?.name ?? "");
  const [type, setType] = useState<string>(editing?.item_type ?? "FG");
  const [unit, setUnit] = useState(editing?.unit ?? "EA");
  const [safety, setSafety] = useState(editing ? String(editing.safety_stock) : "0");
  const [spec, setSpec] = useState(editing?.spec ?? "");
  const [active, setActive] = useState(editing?.active ?? true);
  const [result, setResult] = useState<Msg | null>(null);

  const safetyN = parseQty(safety, { min: 0 });
  const valid = code.trim() !== "" && name.trim() !== "" && unit.trim() !== "" && safetyN !== null;

  const m = useMutation({
    mutationFn: () => {
      const common = {
        name: name.trim(),
        item_type: type as ItemType,
        unit: unit.trim(),
        safety_stock: safetyN as number,
        spec: orNull(spec),
      };
      return editing
        ? patchMesItem(editing.item_code, { ...common, active })
        : createMesItem({ item_code: code.trim(), ...common });
    },
    onSuccess: (it) => {
      setResult({
        ok: true,
        text: editing ? `품목 ${it.item_code} 을(를) 수정했습니다.` : `품목 ${it.item_code} 을(를) 등록했습니다.`,
      });
      if (!editing) {
        setCode("");
        setName("");
        setSpec("");
        setSafety("0");
      }
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) =>
      setResult({ ok: false, text: `품목 ${editing ? "수정" : "등록"} 실패: ${errText(e)}` }),
  });

  return (
    <form
      className="card space-y-3 p-4"
      data-testid="item-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && !m.isPending) m.mutate();
      }}
    >
      <h2 className="font-semibold">{editing ? `품목 수정 — ${editing.item_code}` : "품목 등록"}</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Field label="품목 코드">
          <input
            className="input w-full"
            value={code}
            disabled={editing !== null}
            onChange={(e) => setCode(e.target.value)}
          />
        </Field>
        <Field label="이름">
          <input className="input w-full" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="유형">
          <select className="input w-full" value={type} onChange={(e) => setType(e.target.value)}>
            {ITEM_TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="단위">
          <input className="input w-full" value={unit} onChange={(e) => setUnit(e.target.value)} />
        </Field>
        <Field label="안전재고">
          <input
            className="input w-full"
            type="number"
            min="0"
            step="any"
            inputMode="decimal"
            value={safety}
            onChange={(e) => setSafety(e.target.value)}
          />
        </Field>
        <Field label="규격(선택)">
          <input className="input w-full" value={spec} onChange={(e) => setSpec(e.target.value)} />
        </Field>
      </div>
      {editing && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          사용 (끄면 사용 중지 — 지난 수불·BOM 기록은 그대로 남습니다)
        </label>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary" disabled={!valid || m.isPending} data-testid="item-submit">
          {m.isPending ? "저장 중…" : editing ? "수정 저장" : "품목 등록"}
        </button>
        {editing && (
          <button type="button" className="btn-ghost" onClick={onDone} data-testid="item-edit-cancel">
            수정 닫기
          </button>
        )}
        <span className="text-xs text-slate-400">안전재고보다 적으면 재고 화면에 경고가 뜹니다.</span>
      </div>
      {result && (
        <ResultLine ok={result.ok} testid="item-result">
          {result.text}
        </ResultLine>
      )}
    </form>
  );
}

/* ---------------- BOM ---------------- */

interface BomDraft {
  component_code: string;
  qty_per: string;
  scrap_pct: string;
  note: string;
}

function BomSection({
  parent,
  items,
  editable,
}: {
  parent: string;
  items: ItemOut[];
  editable: boolean;
}): JSX.Element {
  // 편집 중에 창을 오갔다고 다시 불러와 입력을 덮지 않는다.
  const bom = useQuery({
    queryKey: ["mes", "bom", parent],
    queryFn: () => fetchBom(parent),
    refetchOnWindowFocus: false,
  });
  // 저장 결과는 편집기 밖에 둔다 — 저장 뒤 다시 불러오면 편집기가 새로 만들어진다.
  const [result, setResult] = useState<Msg | null>(null);
  return (
    <section className="card" data-testid="bom-section">
      <h2 className="border-b border-slate-100 px-4 py-3 font-semibold">
        BOM — {parent}
        <span className="ml-2 text-xs font-normal text-slate-400">제품 1개당 소요량</span>
      </h2>
      {bom.isError ? (
        <div className="p-4">
          <MesError what="BOM 조회" error={bom.error} testid="bom-error" />
        </div>
      ) : bom.data === undefined ? (
        <p className="p-4 text-sm text-slate-400">불러오는 중…</p>
      ) : (
        <BomEditor
          // 서버 값이 새로 오면(저장 뒤·제품 바꿈) 그 값으로 다시 시작한다.
          key={`${parent}-${bom.dataUpdatedAt}`}
          parent={parent}
          initial={bom.data}
          items={items}
          editable={editable}
          result={result}
          onResult={setResult}
        />
      )}
    </section>
  );
}

function BomEditor({
  parent,
  initial,
  items,
  editable,
  result,
  onResult: setResult,
}: {
  parent: string;
  initial: BomLineOut[];
  items: ItemOut[];
  editable: boolean;
  result: Msg | null;
  onResult: (m: Msg) => void;
}): JSX.Element {
  const qc = useQueryClient();
  const [lines, setLines] = useState<BomDraft[]>(() =>
    initial.map((l) => ({
      component_code: l.component_code,
      qty_per: String(l.qty_per),
      scrap_pct: String(l.scrap_pct),
      note: l.note ?? "",
    })),
  );

  const names = new Map<string, string>();
  for (const l of initial) if (l.component_name) names.set(l.component_code, l.component_name);
  for (const it of items) names.set(it.item_code, it.name);
  // 자재 선택지: 사용 중인 품목 중 이 제품이 아닌 것. 이미 BOM 에 있는(중지된) 것은 남긴다.
  const options = items.filter((it) => it.active && it.item_code !== parent);

  const parsed = lines.map((l) => ({
    component_code: l.component_code,
    qty_per: parseQty(l.qty_per, { min: 0, exclusiveMin: true }),
    scrap_pct: parseQty(l.scrap_pct === "" ? "0" : l.scrap_pct, { min: 0, max: 100, exclusiveMax: true }),
    note: orNull(l.note),
  }));
  const codes = parsed.map((p) => p.component_code);
  const duplicate = new Set(codes).size !== codes.length;
  const valid =
    !duplicate &&
    parsed.every((p) => p.component_code !== "" && p.qty_per !== null && p.scrap_pct !== null);

  const update = (i: number, patch: Partial<BomDraft>) =>
    setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));

  const m = useMutation({
    mutationFn: () =>
      putBom(
        parent,
        parsed.map((p) => ({
          component_code: p.component_code,
          qty_per: p.qty_per as number,
          scrap_pct: p.scrap_pct as number,
          note: p.note,
        })),
      ),
    onSuccess: () => {
      setResult({
        ok: true,
        text: lines.length ? `${parent} BOM ${lines.length}줄을 저장했습니다.` : `${parent} BOM 을 지웠습니다.`,
      });
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) => setResult({ ok: false, text: `BOM 저장 실패: ${errText(e)}` }),
  });

  return (
    <div className="space-y-3 p-4">
      <div className="overflow-x-auto">
        <table className="w-full text-sm" data-testid="bom-table">
          <thead className={THEAD_CLASS}>
            <tr>
              <Th>자재</Th>
              <Th right>소요량(1개당)</Th>
              <Th right>손실률 %</Th>
              <Th>비고</Th>
              {editable && <Th>작업</Th>}
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) =>
              editable ? (
                <tr key={i} className="border-t border-slate-100" data-testid={`bom-line-${i}`}>
                  <Td>
                    <select
                      className="input w-full min-w-[10rem]"
                      aria-label={`자재 ${i + 1}`}
                      value={l.component_code}
                      onChange={(e) => update(i, { component_code: e.target.value })}
                    >
                      <option value="">선택…</option>
                      {l.component_code && !options.some((o) => o.item_code === l.component_code) && (
                        <option value={l.component_code}>
                          {l.component_code}
                          {names.has(l.component_code) ? ` — ${names.get(l.component_code)}` : ""}
                        </option>
                      )}
                      {options.map((o) => (
                        <option key={o.item_code} value={o.item_code}>
                          {o.item_code} — {o.name} ({itemTypeLabel(o.item_type)}, {o.unit})
                        </option>
                      ))}
                    </select>
                  </Td>
                  <Td right>
                    <input
                      className="input w-28 text-right"
                      type="number"
                      min="0"
                      step="any"
                      inputMode="decimal"
                      aria-label={`소요량 ${i + 1}`}
                      value={l.qty_per}
                      onChange={(e) => update(i, { qty_per: e.target.value })}
                    />
                  </Td>
                  <Td right>
                    <input
                      className="input w-24 text-right"
                      type="number"
                      min="0"
                      max="99.99"
                      step="any"
                      inputMode="decimal"
                      aria-label={`손실률 ${i + 1}`}
                      value={l.scrap_pct}
                      onChange={(e) => update(i, { scrap_pct: e.target.value })}
                    />
                  </Td>
                  <Td>
                    <input
                      className="input w-full min-w-[8rem]"
                      aria-label={`비고 ${i + 1}`}
                      value={l.note}
                      onChange={(e) => update(i, { note: e.target.value })}
                    />
                  </Td>
                  <Td>
                    <RowButton
                      danger
                      onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))}
                      testid={`bom-del-${i}`}
                    >
                      삭제
                    </RowButton>
                  </Td>
                </tr>
              ) : (
                <tr key={i} className="border-t border-slate-100" data-testid={`bom-line-${i}`}>
                  <Td>
                    {l.component_code}
                    {names.has(l.component_code) && (
                      <span className="text-slate-500"> — {names.get(l.component_code)}</span>
                    )}
                  </Td>
                  <Td right>{l.qty_per}</Td>
                  <Td right>{l.scrap_pct}</Td>
                  <Td>{l.note || "-"}</Td>
                </tr>
              ),
            )}
            {lines.length === 0 && (
              <EmptyRow colSpan={editable ? 5 : 4} testid="bom-empty">
                BOM 이 없습니다 — 이 제품은 자재 소요·투입을 계산하지 않습니다.
              </EmptyRow>
            )}
          </tbody>
        </table>
      </div>
      {editable && (
        <>
          {duplicate && (
            <p className="text-xs text-ng-fg" role="alert">
              <span aria-hidden="true">✕ </span>같은 자재가 두 줄 이상 있습니다.
            </p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="btn-ghost"
              onClick={() =>
                setLines((ls) => [...ls, { component_code: "", qty_per: "", scrap_pct: "0", note: "" }])
              }
              data-testid="bom-add"
            >
              + 줄 추가
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!valid || m.isPending}
              onClick={() => m.mutate()}
              data-testid="bom-save"
            >
              {m.isPending ? "저장 중…" : "BOM 저장"}
            </button>
            <span className="text-xs text-slate-400">
              저장하면 이 제품의 BOM 전체가 지금 보이는 줄로 바뀝니다(줄이 없으면 삭제).
            </span>
          </div>
          {result && (
            <ResultLine ok={result.ok} testid="bom-result">
              {result.text}
            </ResultLine>
          )}
        </>
      )}
    </div>
  );
}

/* ================= 공정·라우팅 ================= */

function RoutingTab({ editable }: { editable: boolean }): JSX.Element {
  const qc = useQueryClient();
  const procs = useQuery({ queryKey: ["mes", "processes"], queryFn: fetchProcesses });
  const items = useQuery({ queryKey: ["mes", "items"], queryFn: () => fetchMesItems() });
  const equipment = useQuery({ queryKey: ["mes", "equipment"], queryFn: fetchEquipment });
  const [msg, setMsg] = useState<Msg | null>(null);
  const [routeItem, setRouteItem] = useState(ROUTING_DEFAULT);

  const toggle = useMutation({
    mutationFn: (p: ProcessOut) => patchProcess(p.code, { active: !p.active }),
    onSuccess: (p) => {
      setMsg({ ok: true, text: `공정 ${p.name} — ${p.active ? "사용" : "사용 중지"}(으)로 바꿨습니다.` });
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) => setMsg({ ok: false, text: `공정 수정 실패: ${errText(e)}` }),
  });

  const rows = procs.data ?? [];
  const products = (items.data ?? []).filter((it) => it.item_type === "FG");

  return (
    <>
      {msg && (
        <ResultLine ok={msg.ok} testid="proc-msg">
          {msg.text}
        </ResultLine>
      )}
      <section className="card">
        <h2 className="border-b border-slate-100 px-4 py-3 font-semibold">공정</h2>
        {procs.isError ? (
          <div className="p-4">
            <MesError what="공정 조회" error={procs.error} testid="proc-error" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="proc-table">
              <thead className={THEAD_CLASS}>
                <tr>
                  <Th right>순서</Th>
                  <Th>공정 코드</Th>
                  <Th>이름</Th>
                  <Th>실적</Th>
                  <Th>사용</Th>
                  {editable && <Th>작업</Th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr
                    key={p.code}
                    className={`border-t border-slate-100 ${p.active ? "" : "text-slate-400"}`}
                    data-testid={`proc-row-${p.code}`}
                  >
                    <Td right>{p.seq}</Td>
                    <Td>
                      <span className="font-medium">{p.code}</span>
                    </Td>
                    <Td>{p.name}</Td>
                    <Td>
                      {p.auto_from_inspection ? (
                        <span className="whitespace-nowrap rounded bg-sky-100 px-1 text-xs font-semibold text-sky-900">
                          ◆ 검사기 집계
                        </span>
                      ) : (
                        "손으로 입력"
                      )}
                    </Td>
                    <Td>
                      <ActiveMark active={p.active} />
                    </Td>
                    {editable && (
                      <Td>
                        <RowButton
                          disabled={toggle.isPending}
                          onClick={() => toggle.mutate(p)}
                          testid={`proc-toggle-${p.code}`}
                        >
                          {p.active ? "사용 중지" : "다시 사용"}
                        </RowButton>
                      </Td>
                    )}
                  </tr>
                ))}
                {rows.length === 0 && !procs.isFetching && (
                  <EmptyRow colSpan={editable ? 6 : 5} testid="proc-empty">
                    등록된 공정이 없습니다.
                  </EmptyRow>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {editable && <ProcessForm />}

      <section className="card" data-testid="routing-section">
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-3">
          <h2 className="font-semibold">라우팅</h2>
          <label className="flex items-center gap-2 text-sm text-slate-600">
            품목
            <select
              className="input"
              value={routeItem}
              onChange={(e) => setRouteItem(e.target.value)}
              data-testid="routing-item"
            >
              <option value={ROUTING_DEFAULT}>공통(*) — 전용 라우팅이 없는 모든 품목</option>
              {products.map((it) => (
                <option key={it.item_code} value={it.item_code}>
                  {it.item_code} — {it.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <RoutingSection
          key={routeItem}
          itemCode={routeItem}
          processes={rows}
          equipment={equipment.data ?? []}
          editable={editable}
        />
      </section>
    </>
  );
}

function ProcessForm(): JSX.Element {
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [seq, setSeq] = useState("");
  const [auto, setAuto] = useState(false);
  const [result, setResult] = useState<Msg | null>(null);

  const seqN = parseCount(seq, 0);
  const valid = code.trim() !== "" && name.trim() !== "" && seqN !== null;

  const m = useMutation({
    mutationFn: () =>
      createProcess({
        code: code.trim(),
        name: name.trim(),
        seq: seqN as number,
        auto_from_inspection: auto,
      }),
    onSuccess: (p) => {
      setResult({ ok: true, text: `공정 ${p.name}(${p.code}) 을(를) 등록했습니다.` });
      setCode("");
      setName("");
      setSeq("");
      setAuto(false);
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) => setResult({ ok: false, text: `공정 등록 실패: ${errText(e)}` }),
  });

  return (
    <form
      className="card space-y-3 p-4"
      data-testid="proc-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && !m.isPending) m.mutate();
      }}
    >
      <h2 className="font-semibold">공정 등록</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Field label="공정 코드">
          <input className="input w-full" value={code} onChange={(e) => setCode(e.target.value)} />
        </Field>
        <Field label="공정 이름">
          <input className="input w-full" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="순서">
          <input
            className="input w-full"
            type="number"
            min="0"
            step="1"
            inputMode="numeric"
            value={seq}
            onChange={(e) => setSeq(e.target.value)}
          />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
        검사 공정 — 양품·불량을 검사기(AIVIS) 결과에서 가져옵니다(손으로 넣지 않음)
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary" disabled={!valid || m.isPending} data-testid="proc-submit">
          {m.isPending ? "등록 중…" : "공정 등록"}
        </button>
      </div>
      {result && (
        <ResultLine ok={result.ok} testid="proc-result">
          {result.text}
        </ResultLine>
      )}
    </form>
  );
}

/* ---------------- 라우팅 ---------------- */

interface StepDraft {
  process_code: string;
  equipment_code: string;
  std_sec_per_ea: string;
}

function RoutingSection({
  itemCode,
  processes,
  equipment,
  editable,
}: {
  itemCode: string;
  processes: ProcessOut[];
  equipment: EquipmentOut[];
  editable: boolean;
}): JSX.Element {
  const routing = useQuery({
    queryKey: ["mes", "routing", itemCode],
    queryFn: () => fetchRouting(itemCode),
    refetchOnWindowFocus: false,
  });
  const [result, setResult] = useState<Msg | null>(null);
  if (routing.isError) {
    return (
      <div className="p-4">
        <MesError what="라우팅 조회" error={routing.error} testid="routing-error" />
      </div>
    );
  }
  if (routing.data === undefined) return <p className="p-4 text-sm text-slate-400">불러오는 중…</p>;
  return (
    <RoutingEditor
      key={`${itemCode}-${routing.dataUpdatedAt}`}
      itemCode={itemCode}
      routing={routing.data}
      processes={processes}
      equipment={equipment}
      editable={editable}
      result={result}
      onResult={setResult}
    />
  );
}

function RoutingEditor({
  itemCode,
  routing,
  processes,
  equipment,
  editable,
  result,
  onResult: setResult,
}: {
  itemCode: string;
  routing: RoutingOut;
  processes: ProcessOut[];
  equipment: EquipmentOut[];
  editable: boolean;
  result: Msg | null;
  onResult: (m: Msg) => void;
}): JSX.Element {
  const qc = useQueryClient();
  const [steps, setSteps] = useState<StepDraft[]>(() =>
    routing.steps.map((s) => ({
      process_code: s.process_code,
      equipment_code: s.equipment_code ?? "",
      std_sec_per_ea: s.std_sec_per_ea === null ? "" : String(s.std_sec_per_ea),
    })),
  );
  // 적용 중인 것이 이 품목 것이 아니면(공통·공정 전부) 저장이 곧 "전용 라우팅 만들기" 다.
  const own = routing.source === "item";
  const isDefault = itemCode === ROUTING_DEFAULT;

  const procName = new Map(processes.map((p) => [p.code, p.name]));
  for (const s of routing.steps) if (!procName.has(s.process_code)) procName.set(s.process_code, s.process_name);
  const procOptions = processes.filter((p) => p.active);
  const eqOptions = equipment.filter((e) => e.active);

  const parsed = steps.map((s) => ({
    process_code: s.process_code,
    equipment_code: orNull(s.equipment_code),
    std: s.std_sec_per_ea.trim() === "" ? null : parseQty(s.std_sec_per_ea, { min: 0 }),
    stdBad: s.std_sec_per_ea.trim() !== "" && parseQty(s.std_sec_per_ea, { min: 0 }) === null,
  }));
  const valid = parsed.every((p) => p.process_code !== "" && !p.stdBad);

  const update = (i: number, patch: Partial<StepDraft>) =>
    setSteps((ss) => ss.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  const move = (i: number, d: -1 | 1) =>
    setSteps((ss) => {
      const j = i + d;
      if (j < 0 || j >= ss.length) return ss;
      const next = [...ss];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });

  const m = useMutation({
    mutationFn: () =>
      putRouting(
        itemCode,
        parsed.map((p) => ({
          process_code: p.process_code,
          equipment_code: p.equipment_code,
          std_sec_per_ea: p.std,
        })),
      ),
    onSuccess: () => {
      const who = isDefault ? "공통 라우팅" : `${itemCode} 전용 라우팅`;
      setResult({
        ok: true,
        text: steps.length ? `${who} ${steps.length}공정을 저장했습니다.` : `${who}을 지웠습니다.`,
      });
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) => setResult({ ok: false, text: `라우팅 저장 실패: ${errText(e)}` }),
  });

  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-slate-500">적용 중:</span>
        <StatusBadge view={routingSourceView(routing.source, itemCode)} testid="routing-source" />
        {!own && editable && (
          <span className="text-xs text-slate-500">
            {isDefault
              ? "공통 라우팅이 없어 사용 중인 공정 전부가 순서대로 적용됩니다. 저장하면 공통 라우팅이 생깁니다."
              : "아래는 지금 적용되는 순서입니다. 고쳐서 저장하면 이 품목 전용 라우팅이 생깁니다."}
          </span>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm" data-testid="routing-table">
          <thead className={THEAD_CLASS}>
            <tr>
              <Th right>순서</Th>
              <Th>공정</Th>
              <Th>설비</Th>
              <Th right>표준시간(초/개)</Th>
              {editable && <Th>작업</Th>}
            </tr>
          </thead>
          <tbody>
            {steps.map((s, i) =>
              editable ? (
                <tr key={i} className="border-t border-slate-100" data-testid={`routing-step-${i}`}>
                  <Td right>{(i + 1) * 10}</Td>
                  <Td>
                    <select
                      className="input w-full min-w-[8rem]"
                      aria-label={`공정 ${i + 1}`}
                      value={s.process_code}
                      onChange={(e) => update(i, { process_code: e.target.value })}
                    >
                      <option value="">선택…</option>
                      {s.process_code && !procOptions.some((p) => p.code === s.process_code) && (
                        <option value={s.process_code}>
                          {procName.get(s.process_code) ?? s.process_code}
                        </option>
                      )}
                      {procOptions.map((p) => (
                        <option key={p.code} value={p.code}>
                          {p.name} ({p.code})
                        </option>
                      ))}
                    </select>
                  </Td>
                  <Td>
                    <select
                      className="input w-full min-w-[8rem]"
                      aria-label={`설비 ${i + 1}`}
                      value={s.equipment_code}
                      onChange={(e) => update(i, { equipment_code: e.target.value })}
                    >
                      <option value="">지정 안 함</option>
                      {s.equipment_code && !eqOptions.some((e) => e.code === s.equipment_code) && (
                        <option value={s.equipment_code}>{s.equipment_code}</option>
                      )}
                      {eqOptions.map((e) => (
                        <option key={e.code} value={e.code}>
                          {e.name} ({e.code})
                        </option>
                      ))}
                    </select>
                  </Td>
                  <Td right>
                    <input
                      className="input w-24 text-right"
                      type="number"
                      min="0"
                      step="any"
                      inputMode="decimal"
                      aria-label={`표준시간 ${i + 1}`}
                      value={s.std_sec_per_ea}
                      onChange={(e) => update(i, { std_sec_per_ea: e.target.value })}
                    />
                  </Td>
                  <Td>
                    <div className="flex flex-wrap gap-1">
                      <RowButton disabled={i === 0} onClick={() => move(i, -1)} testid={`routing-up-${i}`}>
                        ▲ 위로
                      </RowButton>
                      <RowButton
                        disabled={i === steps.length - 1}
                        onClick={() => move(i, 1)}
                        testid={`routing-down-${i}`}
                      >
                        ▼ 아래로
                      </RowButton>
                      <RowButton
                        danger
                        onClick={() => setSteps((ss) => ss.filter((_, j) => j !== i))}
                        testid={`routing-del-${i}`}
                      >
                        삭제
                      </RowButton>
                    </div>
                  </Td>
                </tr>
              ) : (
                <tr key={i} className="border-t border-slate-100" data-testid={`routing-step-${i}`}>
                  <Td right>{routing.steps[i]?.seq ?? (i + 1) * 10}</Td>
                  <Td>{procName.get(s.process_code) ?? s.process_code}</Td>
                  <Td>{s.equipment_code || "-"}</Td>
                  <Td right>{s.std_sec_per_ea || "-"}</Td>
                </tr>
              ),
            )}
            {steps.length === 0 && (
              <EmptyRow colSpan={editable ? 5 : 4} testid="routing-empty">
                공정이 없습니다.
              </EmptyRow>
            )}
          </tbody>
        </table>
      </div>
      {editable && (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="btn-ghost"
              onClick={() =>
                setSteps((ss) => [...ss, { process_code: "", equipment_code: "", std_sec_per_ea: "" }])
              }
              data-testid="routing-add"
            >
              + 공정 추가
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!valid || m.isPending}
              onClick={() => m.mutate()}
              data-testid="routing-save"
            >
              {m.isPending ? "저장 중…" : "라우팅 저장"}
            </button>
            <span className="text-xs text-slate-400">
              순서 번호는 저장할 때 10, 20, … 으로 다시 매깁니다. 공정을 모두 지우고 저장하면{" "}
              {isDefault ? "공통" : "전용"} 라우팅을 삭제합니다. 이미 만든 작업지시의 공정은 바뀌지 않습니다.
            </span>
          </div>
          {result && (
            <ResultLine ok={result.ok} testid="routing-result">
              {result.text}
            </ResultLine>
          )}
        </>
      )}
    </div>
  );
}

/* ================= 설비 ================= */

function EquipmentTab({ editable }: { editable: boolean }): JSX.Element {
  const equipment = useQuery({ queryKey: ["mes", "equipment"], queryFn: fetchEquipment });
  const procs = useQuery({ queryKey: ["mes", "processes"], queryFn: fetchProcesses });
  const [editing, setEditing] = useState<EquipmentOut | null>(null);
  const rows = equipment.data ?? [];
  const procName = new Map((procs.data ?? []).map((p) => [p.code, p.name]));

  return (
    <>
      <section className="card">
        <h2 className="border-b border-slate-100 px-4 py-3 font-semibold">설비</h2>
        {equipment.isError ? (
          <div className="p-4">
            <MesError what="설비 조회" error={equipment.error} testid="eq-error" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="eq-table">
              <thead className={THEAD_CLASS}>
                <tr>
                  <Th>설비 코드</Th>
                  <Th>이름</Th>
                  <Th>공정</Th>
                  <Th>비고</Th>
                  <Th>사용</Th>
                  {editable && <Th>작업</Th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((e) => (
                  <tr
                    key={e.code}
                    className={`border-t border-slate-100 ${e.active ? "" : "text-slate-400"}`}
                    data-testid={`eq-row-${e.code}`}
                  >
                    <Td>
                      <span className="whitespace-nowrap font-medium">{e.code}</span>
                    </Td>
                    <Td>{e.name}</Td>
                    <Td>{e.process_code ? (procName.get(e.process_code) ?? e.process_code) : "-"}</Td>
                    <Td>{e.note ?? "-"}</Td>
                    <Td>
                      <ActiveMark active={e.active} />
                    </Td>
                    {editable && (
                      <Td>
                        <RowButton onClick={() => setEditing(e)} testid={`eq-edit-${e.code}`}>
                          수정
                        </RowButton>
                      </Td>
                    )}
                  </tr>
                ))}
                {rows.length === 0 && !equipment.isFetching && (
                  <EmptyRow colSpan={editable ? 6 : 5} testid="eq-empty">
                    등록된 설비가 없습니다.{editable && " 아래에서 설비를 등록하세요."}
                  </EmptyRow>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {editable && (
        <EquipmentForm
          key={editing?.code ?? "new"}
          editing={editing}
          processes={procs.data ?? []}
          onDone={() => setEditing(null)}
        />
      )}
      <p className="text-xs text-slate-400">
        설비의 현재 상태(가동·대기·고장·점검)와 가동률은 메뉴의 MES 설비가동 화면에서 봅니다.
      </p>
    </>
  );
}

function EquipmentForm({
  editing,
  processes,
  onDone,
}: {
  editing: EquipmentOut | null;
  processes: ProcessOut[];
  onDone: () => void;
}): JSX.Element {
  const qc = useQueryClient();
  const [code, setCode] = useState(editing?.code ?? "");
  const [name, setName] = useState(editing?.name ?? "");
  const [proc, setProc] = useState(editing?.process_code ?? "");
  const [note, setNote] = useState(editing?.note ?? "");
  const [active, setActive] = useState(editing?.active ?? true);
  const [result, setResult] = useState<Msg | null>(null);

  const valid = code.trim() !== "" && name.trim() !== "";
  const procKnown = processes.some((p) => p.code === proc);

  const m = useMutation({
    mutationFn: () => {
      const common = { name: name.trim(), process_code: orNull(proc), note: orNull(note) };
      return editing
        ? patchEquipment(editing.code, { ...common, active })
        : createEquipment({ code: code.trim(), ...common });
    },
    onSuccess: (e) => {
      setResult({
        ok: true,
        text: editing ? `설비 ${e.code} 을(를) 수정했습니다.` : `설비 ${e.code} 을(를) 등록했습니다.`,
      });
      if (!editing) {
        setCode("");
        setName("");
        setNote("");
      }
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) =>
      setResult({ ok: false, text: `설비 ${editing ? "수정" : "등록"} 실패: ${errText(e)}` }),
  });

  return (
    <form
      className="card space-y-3 p-4"
      data-testid="eq-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && !m.isPending) m.mutate();
      }}
    >
      <h2 className="font-semibold">{editing ? `설비 수정 — ${editing.code}` : "설비 등록"}</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="설비 코드">
          <input
            className="input w-full"
            value={code}
            disabled={editing !== null}
            onChange={(e) => setCode(e.target.value)}
          />
        </Field>
        <Field label="설비 이름">
          <input className="input w-full" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="공정(선택)">
          <select className="input w-full" value={proc} onChange={(e) => setProc(e.target.value)}>
            <option value="">선택 안 함</option>
            {proc && !procKnown && <option value={proc}>{proc}</option>}
            {processes.map((p) => (
              <option key={p.code} value={p.code}>
                {p.name} ({p.code})
              </option>
            ))}
          </select>
        </Field>
        <Field label="비고(선택)">
          <input className="input w-full" value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
      {editing && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          사용 (끄면 사용 중지 — 설비가동 화면과 선택지에서 빠집니다)
        </label>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary" disabled={!valid || m.isPending} data-testid="eq-submit">
          {m.isPending ? "저장 중…" : editing ? "수정 저장" : "설비 등록"}
        </button>
        {editing && (
          <button type="button" className="btn-ghost" onClick={onDone} data-testid="eq-edit-cancel">
            수정 닫기
          </button>
        )}
      </div>
      {result && (
        <ResultLine ok={result.ok} testid="eq-result">
          {result.text}
        </ResultLine>
      )}
    </form>
  );
}
