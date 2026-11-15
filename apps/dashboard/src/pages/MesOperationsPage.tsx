/**
 * MES 공정진행 — 작업지시의 공정별 시작·완료·건너뛰기, 자재 소요·투입
 * (docs/MES.md §11.1, §11.3, §11.5).
 *
 * 공정 목록은 작업지시를 만들 때 라우팅에서 복사된 것이다(그 뒤 라우팅을 바꿔도 그대로).
 * 검사 공정(auto)은 양품·불량을 손으로 넣지 않는다 — 검사기(AIVIS)가 보낸 결과에서 온다.
 *
 * 시작·완료 때 서버가 함께 하는 일(§11.3): 작업지시 PLANNED → RUNNING, 고른 설비 가동(RUN),
 * 완료하면 그 설비 대기(IDLE). 그래서 저장 뒤에는 MES 조회를 전부 다시 읽는다.
 *
 * 권한: 시작·완료·자재 투입은 작업자도, 건너뛰기는 품질관리자 이상.
 */
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { fetchWorkOrders, type WorkOrderOut } from "@/api/mes";
import {
  completeOperation,
  fetchEquipment,
  fetchMaterials,
  fetchOperations,
  issueMaterials,
  skipOperation,
  startOperation,
  type EquipmentOut,
  type MaterialsOut,
  type OperationOut,
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
import { fmtDateTime, fmtNum } from "@/lib/format";
import { orNull, parseCount, woStatusView } from "@/lib/mes";
import { errText, fmtQty, opStatusView, type Msg } from "@/lib/mesPlant";
import { canEdit, useAuthStore } from "@/store/auth";

/** 진행 중인 지시만 고르게 한다(끝난 지시는 URL ?wo= 로만 열린다). */
const WO_OPEN = "PLANNED,RUNNING";
/** 검사 공정의 집계·현재고는 계속 바뀐다. */
const REFRESH_MS = 15_000;

export function MesOperationsPage(): JSX.Element {
  const role = useAuthStore((s) => s.role);
  const username = useAuthStore((s) => s.username);
  const editable = canEdit(role);
  const [params, setParams] = useSearchParams();
  const woId = parseCount(params.get("wo") ?? "");

  const wos = useQuery({
    queryKey: ["mes", "work-orders", WO_OPEN],
    queryFn: () => fetchWorkOrders(WO_OPEN),
  });
  const rows = wos.data ?? [];
  const wo = rows.find((w) => w.id === woId) ?? null;

  function pick(id: string): void {
    setParams(id ? { wo: id } : {}, { replace: true });
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">공정 진행</h1>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          작업지시
          <select
            className="input"
            value={woId === null ? "" : String(woId)}
            onChange={(e) => pick(e.target.value)}
            data-testid="ops-wo"
          >
            <option value="">선택…</option>
            {/* URL 로 들어온 지시가 진행 중 목록에 없어도(완료·취소) 선택은 보이게 한다. */}
            {woId !== null && !wo && <option value={woId}>작업지시 #{woId}</option>}
            {rows.map((w) => (
              <option key={w.id} value={w.id}>
                {w.wo_no} — {w.item_code} · LOT {w.lot} ({woStatusView(w.status).text})
              </option>
            ))}
          </select>
        </label>
        <span className="text-xs text-slate-400">15초마다 갱신</span>
        {wos.isFetching && <span className="text-sm text-slate-400">불러오는 중…</span>}
      </div>

      {wos.isError && <MesError what="작업지시 조회" error={wos.error} testid="ops-wo-error" />}

      {woId === null ? (
        <p className="card p-6 text-center text-slate-400" data-testid="ops-hint">
          {rows.length === 0 && wos.isSuccess
            ? "진행 중인 작업지시가 없습니다 — MES 작업지시 화면에서 먼저 등록하세요."
            : "작업지시를 고르면 공정 목록과 자재 소요가 나옵니다."}
        </p>
      ) : (
        <>
          {wo && <WoSummary wo={wo} />}
          {/* 작업지시가 바뀌면 열려 있던 입력·메시지를 버린다. */}
          <Operations key={`ops-${woId}`} woId={woId} editable={editable} username={username} />
          <Materials key={`mat-${woId}`} woId={woId} />
        </>
      )}
    </div>
  );
}

function WoSummary({ wo }: { wo: WorkOrderOut }): JSX.Element {
  return (
    <div className="card flex flex-wrap items-center gap-x-6 gap-y-1 px-4 py-3 text-sm" data-testid="ops-wo-summary">
      <span className="font-semibold">{wo.wo_no}</span>
      <span>품목 {wo.item_code}</span>
      <span>LOT {wo.lot}</span>
      <span>지시수량 {fmtNum(wo.plan_qty, 0)}</span>
      <StatusBadge view={woStatusView(wo.status)} />
    </div>
  );
}

/* ================= 공정 카드 ================= */

type OpenForm = { opId: number; kind: "start" | "complete" | "skip" } | null;

function Operations({
  woId,
  editable,
  username,
}: {
  woId: number;
  editable: boolean;
  username: string | null;
}): JSX.Element {
  const qc = useQueryClient();
  const ops = useQuery({
    queryKey: ["mes", "operations", woId],
    queryFn: () => fetchOperations(woId),
    refetchInterval: REFRESH_MS,
  });
  const equipment = useQuery({ queryKey: ["mes", "equipment"], queryFn: fetchEquipment });
  const [open, setOpen] = useState<OpenForm>(null);
  const [msg, setMsg] = useState<Msg | null>(null);

  const done = (text: string) => {
    setMsg({ ok: true, text });
    setOpen(null);
    qc.invalidateQueries({ queryKey: ["mes"] });
  };

  const startMut = useMutation({
    mutationFn: (p: { op: OperationOut; equipment_code: string | null; worker: string | null }) =>
      startOperation(woId, p.op.id, { equipment_code: p.equipment_code, worker: p.worker }),
    // 서버는 그 작업지시의 공정 목록 전체를 돌려준다 — 안내 문구는 보낸 값으로 만든다.
    onSuccess: (_ops, p) => {
      const eq = p.equipment_code ?? p.op.equipment_code;
      done(
        `${p.op.process_name} 공정을 시작했습니다.${eq ? ` 설비 ${eq} 은(는) 가동으로 바뀝니다.` : ""}`,
      );
    },
    onError: (e) => setMsg({ ok: false, text: `공정 시작 실패: ${errText(e)}` }),
  });

  const completeMut = useMutation({
    mutationFn: (p: { op: OperationOut; good_qty: number | null; defect_qty: number; note: string | null }) =>
      completeOperation(
        woId,
        p.op.id,
        // 검사 공정은 수량을 보내지 않는다 — 서버가 검사기 집계를 쓴다.
        p.op.auto ? { note: p.note } : { good_qty: p.good_qty, defect_qty: p.defect_qty, note: p.note },
      ),
    onSuccess: (_ops, p) => done(`${p.op.process_name} 공정을 완료했습니다.`),
    onError: (e) => setMsg({ ok: false, text: `공정 완료 실패: ${errText(e)}` }),
  });

  const skipMut = useMutation({
    mutationFn: (op: OperationOut) => skipOperation(woId, op.id),
    onSuccess: (_ops, op) => done(`${op.process_name} 공정을 건너뛰었습니다.`),
    onError: (e) => {
      setMsg({
        ok: false,
        text:
          e instanceof ApiError && e.status === 403
            ? "공정 건너뛰기 실패: 품질관리자 이상 권한이 필요합니다."
            : `공정 건너뛰기 실패: ${errText(e)}`,
      });
      setOpen(null);
    },
  });

  const busy = startMut.isPending || completeMut.isPending || skipMut.isPending;
  const list = ops.data ?? [];
  const eqList = (equipment.data ?? []).filter((e) => e.active);

  return (
    <section className="space-y-3" data-testid="ops-section">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-semibold">공정</h2>
        {ops.isFetching && <span className="text-sm text-slate-400">불러오는 중…</span>}
      </div>
      {msg && (
        <ResultLine ok={msg.ok} testid="ops-msg">
          {msg.text}
        </ResultLine>
      )}
      {ops.isError ? (
        <MesError what="공정 조회" error={ops.error} testid="ops-error" />
      ) : list.length === 0 && !ops.isFetching ? (
        <p className="card p-6 text-center text-slate-400" data-testid="ops-empty">
          이 작업지시에는 공정이 없습니다 — MES 기준정보에서 공정·라우팅을 확인하세요.
        </p>
      ) : (
        <ol className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {list.map((op) => (
            <OpCard
              key={op.id}
              op={op}
              equipment={eqList}
              editable={editable}
              username={username}
              open={open?.opId === op.id ? open.kind : null}
              busy={busy}
              onOpen={(kind) => setOpen(kind ? { opId: op.id, kind } : null)}
              onStart={(equipment_code, worker) => startMut.mutate({ op, equipment_code, worker })}
              onComplete={(good_qty, defect_qty, note) =>
                completeMut.mutate({ op, good_qty, defect_qty, note })
              }
              onSkip={() => skipMut.mutate(op)}
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function OpCard({
  op,
  equipment,
  editable,
  username,
  open,
  busy,
  onOpen,
  onStart,
  onComplete,
  onSkip,
}: {
  op: OperationOut;
  equipment: EquipmentOut[];
  editable: boolean;
  username: string | null;
  open: "start" | "complete" | "skip" | null;
  busy: boolean;
  onOpen: (kind: "start" | "complete" | "skip" | null) => void;
  onStart: (equipmentCode: string | null, worker: string | null) => void;
  onComplete: (good: number | null, defect: number, note: string | null) => void;
  onSkip: () => void;
}): JSX.Element {
  const pending = op.status === "PENDING";
  const running = op.status === "RUNNING";
  const finished = op.status === "DONE";

  return (
    <li className="card space-y-2 p-4" data-testid={`op-card-${op.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs tabular-nums text-slate-600">
          {op.seq}
        </span>
        <span className="font-semibold">{op.process_name}</span>
        <StatusBadge view={opStatusView(op.status)} testid={`op-status-${op.id}`} />
        {op.auto && (
          <span className="whitespace-nowrap rounded bg-sky-100 px-1 text-xs font-semibold text-sky-900">
            ◆ 검사 공정
          </span>
        )}
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
        <dt className="text-slate-500">설비</dt>
        <dd>{op.equipment_code ?? "-"}</dd>
        <dt className="text-slate-500">시작</dt>
        <dd className="tabular-nums">{fmtDateTime(op.started_at)}</dd>
        <dt className="text-slate-500">끝</dt>
        <dd className="tabular-nums">{fmtDateTime(op.ended_at)}</dd>
        {op.worker && (
          <>
            <dt className="text-slate-500">작업자</dt>
            <dd>{op.worker}</dd>
          </>
        )}
        {(finished || !op.auto) && (
          <>
            <dt className="text-slate-500">양품·불량</dt>
            <dd className="tabular-nums" data-testid={`op-qty-${op.id}`}>
              {op.good_qty === null && op.defect_qty === null
                ? "-"
                : `양품 ${fmtNum(op.good_qty, 0)} · 불량 ${fmtNum(op.defect_qty, 0)}`}
            </dd>
          </>
        )}
        {op.auto && (
          <>
            <dt className="text-slate-500">검사기 집계</dt>
            <dd className="tabular-nums" data-testid={`op-auto-${op.id}`}>
              {op.auto_good_qty === null && op.auto_defect_qty === null
                ? "아직 검사 결과 없음"
                : `양품 ${fmtNum(op.auto_good_qty ?? 0, 0)} · 불량 ${fmtNum(op.auto_defect_qty ?? 0, 0)}`}
            </dd>
          </>
        )}
        {op.note && (
          <>
            <dt className="text-slate-500">비고</dt>
            <dd>{op.note}</dd>
          </>
        )}
      </dl>

      {(pending || running) && open === null && (
        <div className="flex flex-wrap gap-1">
          {pending && (
            <RowButton primary disabled={busy} onClick={() => onOpen("start")} testid={`op-start-${op.id}`}>
              시작
            </RowButton>
          )}
          {running && (
            <RowButton primary disabled={busy} onClick={() => onOpen("complete")} testid={`op-complete-${op.id}`}>
              완료
            </RowButton>
          )}
          {editable && (
            <RowButton disabled={busy} onClick={() => onOpen("skip")} testid={`op-skip-${op.id}`}>
              건너뛰기
            </RowButton>
          )}
        </div>
      )}

      {open === "start" && (
        <StartForm
          op={op}
          equipment={equipment}
          username={username}
          busy={busy}
          onCancel={() => onOpen(null)}
          onSubmit={onStart}
        />
      )}
      {open === "complete" && (
        <CompleteForm op={op} busy={busy} onCancel={() => onOpen(null)} onSubmit={onComplete} />
      )}
      {open === "skip" && (
        // 건너뛰기는 되돌리는 버튼이 없어 한 번 더 확인한다.
        <div className="space-y-2 rounded-md bg-amber-50 p-2 text-sm">
          <p className="text-amber-900">
            <span aria-hidden="true">⚠ </span>이 공정을 실적 없이 건너뜁니다. 되돌릴 수 없습니다.
          </p>
          <div className="flex flex-wrap gap-1">
            <RowButton danger disabled={busy} onClick={onSkip} testid={`op-skip-confirm-${op.id}`}>
              건너뛰기 확정
            </RowButton>
            <RowButton onClick={() => onOpen(null)}>그대로 둠</RowButton>
          </div>
        </div>
      )}
    </li>
  );
}

function StartForm({
  op,
  equipment,
  username,
  busy,
  onCancel,
  onSubmit,
}: {
  op: OperationOut;
  equipment: EquipmentOut[];
  username: string | null;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (equipmentCode: string | null, worker: string | null) => void;
}): JSX.Element {
  // 라우팅이 정해 둔 설비가 기본값. 그 공정의 설비를 위에 둔다.
  const [eq, setEq] = useState(op.equipment_code ?? "");
  const [worker, setWorker] = useState(username ?? "");
  const sorted = [...equipment].sort(
    (a, b) =>
      Number(b.process_code === op.process_code) - Number(a.process_code === op.process_code) ||
      a.code.localeCompare(b.code),
  );
  const known = sorted.some((e) => e.code === eq);

  return (
    <form
      className="space-y-2 rounded-md bg-slate-50 p-2"
      data-testid={`op-start-form-${op.id}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (!busy) onSubmit(orNull(eq), orNull(worker));
      }}
    >
      <Field label="설비(선택)">
        <select className="input w-full" value={eq} onChange={(e) => setEq(e.target.value)}>
          <option value="">설비 없이 시작</option>
          {eq && !known && <option value={eq}>{eq}</option>}
          {sorted.map((e) => (
            <option key={e.code} value={e.code}>
              {e.name} ({e.code})
            </option>
          ))}
        </select>
      </Field>
      <Field label="작업자(선택)">
        <input className="input w-full" value={worker} onChange={(e) => setWorker(e.target.value)} />
      </Field>
      <div className="flex flex-wrap gap-1">
        <button type="submit" className="btn-primary px-2 py-1 text-xs" disabled={busy} data-testid={`op-start-submit-${op.id}`}>
          {busy ? "처리 중…" : "시작 확정"}
        </button>
        <RowButton onClick={onCancel}>닫기</RowButton>
      </div>
      <p className="text-xs text-slate-400">설비를 고르면 그 설비가 가동으로 바뀝니다.</p>
    </form>
  );
}

function CompleteForm({
  op,
  busy,
  onCancel,
  onSubmit,
}: {
  op: OperationOut;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (good: number | null, defect: number, note: string | null) => void;
}): JSX.Element {
  const [good, setGood] = useState("");
  const [defect, setDefect] = useState("0");
  const [note, setNote] = useState("");

  const goodN = parseCount(good, 0);
  const defectN = parseCount(defect === "" ? "0" : defect, 0);
  const valid = op.auto || (goodN !== null && defectN !== null);

  return (
    <form
      className="space-y-2 rounded-md bg-slate-50 p-2"
      data-testid={`op-complete-form-${op.id}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && !busy) onSubmit(op.auto ? null : goodN, op.auto ? 0 : (defectN as number), orNull(note));
      }}
    >
      {op.auto ? (
        <p className="text-sm text-slate-700" data-testid={`op-complete-auto-${op.id}`}>
          검사기 집계 — 양품 {fmtNum(op.auto_good_qty ?? 0, 0)} · 불량 {fmtNum(op.auto_defect_qty ?? 0, 0)}
          <span className="block text-xs text-slate-400">
            검사 공정은 수량을 넣지 않습니다. 검사기(AIVIS)가 보낸 결과로 기록합니다.
          </span>
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <Field label="양품">
            <input
              className="input w-full"
              type="number"
              min="0"
              step="1"
              inputMode="numeric"
              value={good}
              onChange={(e) => setGood(e.target.value)}
            />
          </Field>
          <Field label="불량">
            <input
              className="input w-full"
              type="number"
              min="0"
              step="1"
              inputMode="numeric"
              value={defect}
              onChange={(e) => setDefect(e.target.value)}
            />
          </Field>
        </div>
      )}
      <Field label="비고(선택)">
        <input className="input w-full" value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      <div className="flex flex-wrap gap-1">
        <button
          type="submit"
          className="btn-primary px-2 py-1 text-xs"
          disabled={!valid || busy}
          data-testid={`op-complete-submit-${op.id}`}
        >
          {busy ? "처리 중…" : "완료 확정"}
        </button>
        <RowButton onClick={onCancel}>닫기</RowButton>
      </div>
    </form>
  );
}

/* ================= 자재 소요·투입 ================= */

const EPS = 1e-9;

function Materials({ woId }: { woId: number }): JSX.Element {
  const qc = useQueryClient();
  const mats = useQuery({
    queryKey: ["mes", "materials", woId],
    queryFn: () => fetchMaterials(woId),
    refetchInterval: REFRESH_MS,
  });
  const [msg, setMsg] = useState<Msg | null>(null);
  // 409(재고 부족) 뒤에만 "재고가 모자라도 투입" 을 보여 준다.
  const [shortBlocked, setShortBlocked] = useState(false);

  const issue = useMutation({
    mutationFn: (allowNegative: boolean) => issueMaterials(woId, { allow_negative: allowNegative }),
    onSuccess: (out: MaterialsOut, allowNegative) => {
      setShortBlocked(false);
      setMsg({
        ok: true,
        text: allowNegative
          ? "재고가 모자란 채로 남은 필요량을 투입했습니다 — 재고가 음수인 품목은 입고·조정으로 맞추세요."
          : "남은 필요량을 모두 투입했습니다.",
      });
      qc.setQueryData(["mes", "materials", woId], out);
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) {
        setShortBlocked(true);
        setMsg({ ok: false, text: `재고가 모자라 투입하지 않았습니다: ${errText(e)}` });
        // 부족 수량을 최신으로 다시 보여 준다.
        qc.invalidateQueries({ queryKey: ["mes", "materials", woId] });
      } else {
        setMsg({ ok: false, text: `자재 투입 실패: ${errText(e)}` });
      }
    },
  });

  const lines = mats.data?.lines ?? [];
  const remaining = lines.some((l) => l.required - l.issued > EPS);
  const short = lines.filter((l) => l.shortage > EPS);

  return (
    <section className="card" data-testid="mat-section">
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-3">
        <h2 className="font-semibold">자재 소요</h2>
        {mats.data && (
          <span className="text-xs text-slate-400">지시수량 {fmtNum(mats.data.plan_qty, 0)} 기준</span>
        )}
        {short.length > 0 && (
          <span
            className="rounded bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900"
            data-testid="mat-short-count"
          >
            <span aria-hidden="true">⚠ </span>재고 부족 {short.length}품목
          </span>
        )}
      </div>
      {mats.isError ? (
        <div className="p-4">
          <MesError what="자재 소요 조회" error={mats.error} testid="mat-error" />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="mat-table">
            <thead className={THEAD_CLASS}>
              <tr>
                <Th>자재</Th>
                <Th right>소요량(1개당)</Th>
                <Th right>손실률 %</Th>
                <Th right>필요</Th>
                <Th right>투입</Th>
                <Th right>현재고</Th>
                <Th right>부족</Th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l) => {
                const isShort = l.shortage > EPS;
                return (
                  <tr
                    key={l.item_code}
                    className={`border-t border-slate-100 ${isShort ? "bg-amber-50" : ""}`}
                    data-testid={`mat-row-${l.item_code}`}
                  >
                    <Td>
                      <span className="font-medium">{l.item_code}</span>
                      {l.name && <span className="text-slate-500"> — {l.name}</span>}
                      {l.unit && <span className="text-xs text-slate-400"> ({l.unit})</span>}
                    </Td>
                    <Td right>{fmtQty(l.qty_per)}</Td>
                    <Td right>{fmtQty(l.scrap_pct)}</Td>
                    <Td right>{fmtQty(l.required)}</Td>
                    <Td right>{fmtQty(l.issued)}</Td>
                    <Td right>{fmtQty(l.on_hand)}</Td>
                    <Td right testid={`mat-short-${l.item_code}`}>
                      {isShort ? (
                        <span className="whitespace-nowrap font-semibold text-amber-900">
                          <span aria-hidden="true">⚠ </span>부족 {fmtQty(l.shortage)}
                        </span>
                      ) : (
                        <span className="text-slate-400">-</span>
                      )}
                    </Td>
                  </tr>
                );
              })}
              {lines.length === 0 && !mats.isFetching && (
                <EmptyRow colSpan={7} testid="mat-empty">
                  이 품목에는 BOM 이 없어 자재 소요가 없습니다 — MES 기준정보에서 BOM 을 등록하세요.
                </EmptyRow>
              )}
            </tbody>
          </table>
        </div>
      )}
      {lines.length > 0 && (
        <div className="space-y-2 border-t border-slate-100 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="btn-primary"
              disabled={!remaining || issue.isPending}
              onClick={() => issue.mutate(false)}
              data-testid="mat-issue"
            >
              {issue.isPending ? "투입 중…" : "자재 투입"}
            </button>
            <span className="text-xs text-slate-400">
              {remaining
                ? "BOM 기준 남은 필요량(필요 − 투입)을 한 번에 재고에서 뺍니다."
                : "필요량을 모두 투입했습니다."}
            </span>
          </div>
          {msg && (
            <ResultLine ok={msg.ok} testid="mat-msg">
              {msg.text}
            </ResultLine>
          )}
          {shortBlocked && (
            <div className="space-y-2 rounded-md bg-amber-50 p-3 text-sm text-amber-900" data-testid="mat-short-box">
              <p>
                <span aria-hidden="true">⚠ </span>
                위 표에서 "부족" 으로 표시된 자재의 재고가 모자랍니다. 먼저 MES 자재·재고 화면에서 입고를
                등록하는 것이 맞습니다. 실물은 이미 썼고 입고 등록만 늦은 경우라면 아래 버튼으로 투입을
                기록할 수 있습니다(재고가 음수가 됩니다).
              </p>
              <RowButton
                danger
                disabled={issue.isPending}
                onClick={() => issue.mutate(true)}
                testid="mat-issue-force"
              >
                재고가 모자라도 투입
              </RowButton>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
