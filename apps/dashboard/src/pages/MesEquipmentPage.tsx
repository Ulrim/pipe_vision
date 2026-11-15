/**
 * MES 설비가동 — 설비별 현재 상태 전환, 기간 가동률, 상태 이력
 * (docs/MES.md §11.1, §11.4, §11.5).
 *
 * 가동률(%) = 가동 ÷ (가동 + 대기 + 고장) × 100. 계획정지(점검)와 기록이 없는 시간은
 * 분모에서 뺀다. MTTR = 고장 시간 ÷ 고장 횟수, MTBF = 가동 시간 ÷ 고장 횟수.
 * 계산은 서버가 한다 — 화면은 받은 값을 그대로 보여 준다.
 *
 * 공정을 시작·완료하면 설비 상태가 자동으로 가동·대기로 바뀐다(§11.3). 여기서는 그 밖의
 * 전환(고장·점검·수동 가동/대기)을 한다. 권한: 작업자 이상(라인에서 하는 일).
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchEquipment,
  fetchEquipmentEvents,
  fetchUtilization,
  setEquipmentState,
  type EquipState,
  type EquipmentOut,
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
import {
  EQUIP_STATES,
  equipStateView,
  errText,
  fmtDuration,
  needsReason,
  secondsSince,
  type Msg,
  todayKst,
} from "@/lib/mesPlant";

const REFRESH_MS = 15_000;
const EVENT_LIMIT = 50;

/** 경과 시간 표시용 — 30초마다 다시 그린다. */
function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function MesEquipmentPage(): JSX.Element {
  const qc = useQueryClient();
  const now = useNow();
  const [selected, setSelected] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg | null>(null);
  // 고장·점검은 사유를 받아야 해서 먼저 입력칸을 연다.
  const [reasonFor, setReasonFor] = useState<{ code: string; state: EquipState } | null>(null);

  const equipment = useQuery({
    queryKey: ["mes", "equipment"],
    queryFn: fetchEquipment,
    refetchInterval: REFRESH_MS,
  });

  const stateMut = useMutation({
    mutationFn: (p: { code: string; state: EquipState; reason: string | null }) =>
      setEquipmentState(p.code, { state: p.state, reason: p.reason }),
    onSuccess: (e, p) => {
      setMsg({ ok: true, text: `${e.name}(${e.code}) — ${equipStateView(p.state).text}(으)로 바꿨습니다.` });
      setReasonFor(null);
      qc.invalidateQueries({ queryKey: ["mes"] });
    },
    onError: (e) => setMsg({ ok: false, text: `상태 전환 실패: ${errText(e)}` }),
  });

  // 사용 중지한 설비는 카드에서 뺀다(가동률 표에는 서버가 주는 대로 나온다).
  const cards = (equipment.data ?? []).filter((e) => e.active);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">설비 가동</h1>
        <span className="text-xs text-slate-400">15초마다 갱신</span>
        {equipment.isFetching && <span className="text-sm text-slate-400">불러오는 중…</span>}
      </div>

      {msg && (
        <ResultLine ok={msg.ok} testid="eq-msg">
          {msg.text}
        </ResultLine>
      )}

      {equipment.isError ? (
        <MesError what="설비 조회" error={equipment.error} testid="eq-error" />
      ) : cards.length === 0 && !equipment.isFetching ? (
        <p className="card p-6 text-center text-slate-400" data-testid="eq-empty">
          사용 중인 설비가 없습니다 — MES 기준정보의 설비 탭에서 등록하세요.
        </p>
      ) : (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {cards.map((e) => (
            <EquipCard
              key={e.code}
              eq={e}
              now={now}
              selected={selected === e.code}
              busy={stateMut.isPending}
              reasonState={reasonFor?.code === e.code ? reasonFor.state : null}
              onSelect={() => setSelected(selected === e.code ? null : e.code)}
              onAskReason={(state) => setReasonFor(state ? { code: e.code, state } : null)}
              onChange={(state, reason) => stateMut.mutate({ code: e.code, state, reason })}
            />
          ))}
        </ul>
      )}

      <Utilization />

      {selected ? (
        <Events code={selected} />
      ) : (
        <p className="text-sm text-slate-500" data-testid="ev-hint">
          설비 카드의 [이력] 을 누르면 그 설비의 상태 이력이 나옵니다.
        </p>
      )}
    </div>
  );
}

function EquipCard({
  eq,
  now,
  selected,
  busy,
  reasonState,
  onSelect,
  onAskReason,
  onChange,
}: {
  eq: EquipmentOut;
  now: number;
  selected: boolean;
  busy: boolean;
  reasonState: EquipState | null;
  onSelect: () => void;
  onAskReason: (state: EquipState | null) => void;
  onChange: (state: EquipState, reason: string | null) => void;
}): JSX.Element {
  const [reason, setReason] = useState("");
  const elapsed = secondsSince(eq.state_since, now);

  return (
    <li
      className={`card space-y-2 p-4 ${selected ? "ring-2 ring-brand" : ""}`}
      data-testid={`eq-card-${eq.code}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{eq.name}</span>
        <span className="text-xs text-slate-400">{eq.code}</span>
        <StatusBadge view={equipStateView(eq.state)} testid={`eq-state-${eq.code}`} />
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
        <dt className="text-slate-500">경과</dt>
        <dd className="tabular-nums" data-testid={`eq-elapsed-${eq.code}`}>
          {elapsed === null ? "-" : `${fmtDuration(elapsed)}째`}
          {eq.state_since && (
            <span className="text-xs text-slate-400"> ({fmtDateTime(eq.state_since)} 부터)</span>
          )}
        </dd>
        <dt className="text-slate-500">작업지시</dt>
        <dd data-testid={`eq-wo-${eq.code}`}>{eq.wo_no ?? (eq.wo_id !== null ? `#${eq.wo_id}` : "-")}</dd>
        {eq.state_reason && (
          <>
            <dt className="text-slate-500">사유</dt>
            <dd>{eq.state_reason}</dd>
          </>
        )}
      </dl>

      {reasonState === null ? (
        <div className="flex flex-wrap gap-1">
          {EQUIP_STATES.filter((s) => s !== eq.state).map((s) => (
            <RowButton
              key={s}
              danger={s === "DOWN"}
              disabled={busy}
              onClick={() => {
                if (needsReason(s)) {
                  setReason("");
                  onAskReason(s);
                } else {
                  onChange(s, null);
                }
              }}
              testid={`eq-set-${eq.code}-${s}`}
            >
              {equipStateView(s).symbol} {equipStateView(s).text}
            </RowButton>
          ))}
          <RowButton primary={selected} onClick={onSelect} testid={`eq-history-${eq.code}`}>
            이력
          </RowButton>
        </div>
      ) : (
        <form
          className="space-y-2 rounded-md bg-slate-50 p-2"
          data-testid={`eq-reason-form-${eq.code}`}
          onSubmit={(e) => {
            e.preventDefault();
            if (reason.trim() !== "" && !busy) onChange(reasonState, reason.trim());
          }}
        >
          <Field label={`${equipStateView(reasonState).text} 사유(필수)`}>
            <input
              className="input w-full"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              autoFocus
            />
          </Field>
          <div className="flex flex-wrap gap-1">
            <button
              type="submit"
              className="btn-primary px-2 py-1 text-xs"
              disabled={reason.trim() === "" || busy}
              data-testid={`eq-reason-submit-${eq.code}`}
            >
              {busy ? "처리 중…" : `${equipStateView(reasonState).text}(으)로 전환`}
            </button>
            <RowButton onClick={() => onAskReason(null)}>닫기</RowButton>
          </div>
        </form>
      )}
    </li>
  );
}

/* ---------------- 가동률 ---------------- */

function Utilization(): JSX.Element {
  const [from, setFrom] = useState(todayKst());
  const [to, setTo] = useState(todayKst());
  // 날짜 문자열(YYYY-MM-DD)은 사전순 = 시간순.
  const bad = from === "" || to === "" || to < from;

  const util = useQuery({
    queryKey: ["mes", "utilization", from, to],
    queryFn: () => fetchUtilization(from, to),
    enabled: !bad,
    refetchInterval: REFRESH_MS,
  });
  const rows = util.data?.rows ?? [];

  return (
    <section className="card" data-testid="util-section">
      <div className="flex flex-wrap items-end gap-3 border-b border-slate-100 px-4 py-3">
        <h2 className="pb-1.5 font-semibold">가동률</h2>
        <Field label="시작일">
          <input
            className="input"
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            data-testid="util-from"
          />
        </Field>
        <Field label="종료일">
          <input
            className="input"
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            data-testid="util-to"
          />
        </Field>
        <span className="pb-2 text-xs text-slate-400">
          가동률 = 가동 ÷ (가동 + 대기 + 고장). 점검과 기록이 없는 시간은 뺍니다.
        </span>
      </div>
      {bad ? (
        <p className="p-4 text-sm text-ng-fg" role="alert" data-testid="util-bad-range">
          <span aria-hidden="true">✕ </span>
          {from === "" || to === "" ? "시작일과 종료일을 넣으세요." : "종료일이 시작일보다 앞입니다."}
        </p>
      ) : util.isError ? (
        <div className="p-4">
          <MesError what="가동률 조회" error={util.error} testid="util-error" />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="util-table">
            <thead className={THEAD_CLASS}>
              <tr>
                <Th>설비</Th>
                <Th right>가동률</Th>
                <Th right>가동</Th>
                <Th right>대기</Th>
                <Th right>고장</Th>
                <Th right>점검</Th>
                <Th right>고장 횟수</Th>
                <Th right>MTTR(분)</Th>
                <Th right>MTBF(분)</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.equipment_code}
                  className="border-t border-slate-100"
                  data-testid={`util-row-${r.equipment_code}`}
                >
                  <Td>
                    <span className="font-medium">{r.name}</span>
                    <span className="text-xs text-slate-400"> {r.equipment_code}</span>
                  </Td>
                  <Td right testid={`util-pct-${r.equipment_code}`}>
                    {r.availability_pct === null ? (
                      <span className="text-slate-400" title="이 기간에 가동·대기·고장 기록이 없습니다">
                        -
                      </span>
                    ) : (
                      <span className="font-semibold">{fmtNum(r.availability_pct, 1)}%</span>
                    )}
                  </Td>
                  <Td right>{fmtDuration(r.run_s)}</Td>
                  <Td right>{fmtDuration(r.idle_s)}</Td>
                  <Td right>
                    <span className={r.down_s > 0 ? "text-ng-fg" : ""}>{fmtDuration(r.down_s)}</span>
                  </Td>
                  <Td right>{fmtDuration(r.pm_s)}</Td>
                  <Td right>{fmtNum(r.down_count, 0)}</Td>
                  <Td right>{fmtNum(r.mttr_min, 1)}</Td>
                  <Td right>{fmtNum(r.mtbf_min, 1)}</Td>
                </tr>
              ))}
              {rows.length === 0 && !util.isFetching && (
                <EmptyRow colSpan={9} testid="util-empty">
                  이 기간에 설비 기록이 없습니다.
                </EmptyRow>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/* ---------------- 상태 이력 ---------------- */

function Events({ code }: { code: string }): JSX.Element {
  const events = useQuery({
    queryKey: ["mes", "equipment-events", code],
    queryFn: () => fetchEquipmentEvents(code, EVENT_LIMIT),
    refetchInterval: REFRESH_MS,
  });
  const rows = events.data ?? [];

  return (
    <section className="card" data-testid="ev-section">
      <h2 className="border-b border-slate-100 px-4 py-3 font-semibold">
        상태 이력 — {code}
        <span className="ml-2 text-xs font-normal text-slate-400">최근 {EVENT_LIMIT}건</span>
      </h2>
      {events.isError ? (
        <div className="p-4">
          <MesError what="상태 이력 조회" error={events.error} testid="ev-error" />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="ev-table">
            <thead className={THEAD_CLASS}>
              <tr>
                <Th>상태</Th>
                <Th>사유</Th>
                <Th>시작</Th>
                <Th>끝</Th>
                <Th right>길이</Th>
                <Th>작업지시</Th>
                <Th>등록자</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((ev) => (
                <tr key={ev.id} className="border-t border-slate-100" data-testid={`ev-row-${ev.id}`}>
                  <Td>
                    <StatusBadge view={equipStateView(ev.state)} />
                  </Td>
                  <Td>{ev.reason ?? "-"}</Td>
                  <Td>
                    <span className="whitespace-nowrap tabular-nums">{fmtDateTime(ev.started_at)}</span>
                  </Td>
                  <Td>
                    {ev.ended_at ? (
                      <span className="whitespace-nowrap tabular-nums">{fmtDateTime(ev.ended_at)}</span>
                    ) : (
                      <span className="whitespace-nowrap text-sky-900">▶ 진행 중</span>
                    )}
                  </Td>
                  <Td right>{fmtDuration(secondsSince(ev.started_at, ev.ended_at))}</Td>
                  <Td>{ev.wo_id !== null ? `#${ev.wo_id}` : "-"}</Td>
                  <Td>{ev.created_by ?? "-"}</Td>
                </tr>
              ))}
              {rows.length === 0 && !events.isFetching && (
                <EmptyRow colSpan={7} testid="ev-empty">
                  상태 기록이 없습니다.
                </EmptyRow>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
