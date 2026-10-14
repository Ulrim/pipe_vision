/**
 * MES 연계현황 — 검사기(AIVIS) → MES 품질이력 수신 상태(docs/MES.md §6.3,
 * docs/MES_INTERFACE.md).
 *
 * 연계율 100%(§1.2-4)는 "보냈다" 가 아니라 "MES 가 받았다" 로 증명해야 한다. 이 화면은
 * MES 쪽에서 본 수신 상태다: 테이블 연계(MES 가 AIVIS 의 mes_quality_if 를 폴링)가 켜져
 * 있는지, 아직 안 가져간 건이 쌓이는지, 최근 수신이 언제였는지.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { fetchIntakeStatus, fetchQuality, pollIntake, type IntakeStatus, type PollOut, type QualityOut } from "@/api/mes";
import { EmptyRow, MesError, ResultLine, StatusBadge, THEAD_CLASS, Td, Th } from "@/components/MesParts";
import { VerdictBadge } from "@/components/VerdictBadge";
import { fmtDateTime, fmtNum } from "@/lib/format";
import { defectLabel, intakeSourceLabel, isPendingStale, TONE_CLASS, type MesTone } from "@/lib/mes";
import { isCountStage, stageLabel } from "@/lib/stage";
import { canEdit, useAuthStore } from "@/store/auth";

/** 연계 상태 갱신 주기(ms). MES 폴링 주기(기본 10초)보다 짧아야 변화가 보인다. */
export const INTAKE_REFETCH_MS = 5000;
const QUALITY_LIMIT = 50;

/** PollOut 한 줄 요약 — "지금 가져오기" 결과와 마지막 주기 결과가 같은 말을 쓴다. */
export function pollSummary(r: PollOut): string {
  return (
    `조회 ${fmtNum(r.scanned, 0)} · 신규 ${fmtNum(r.stored, 0)} · 갱신 ${fmtNum(r.updated, 0)}` +
    ` · 중복 ${fmtNum(r.duplicate, 0)} · 수신 표시 ${fmtNum(r.marked, 0)}`
  );
}

export function MesLinkPage(): JSX.Element {
  const role = useAuthStore((s) => s.role);
  const editable = canEdit(role);
  const qc = useQueryClient();

  const status = useQuery({
    queryKey: ["mes", "intake-status"],
    queryFn: fetchIntakeStatus,
    refetchInterval: INTAKE_REFETCH_MS,
  });

  const poll = useMutation({
    mutationFn: pollIntake,
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["mes", "intake-status"] });
      qc.invalidateQueries({ queryKey: ["mes", "quality"] });
    },
  });

  const s = status.data;
  const tableOff = s !== undefined && !s.table_enabled;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">검사기 → MES 수신 상태</h1>
        <span className="text-xs text-slate-400">5초마다 갱신</span>
        {editable && (
          <button
            type="button"
            className="btn-primary ml-auto"
            disabled={poll.isPending || tableOff}
            title={tableOff ? "테이블 연계가 꺼져 있어 가져올 곳이 없습니다" : undefined}
            onClick={() => poll.mutate()}
            data-testid="poll-now"
          >
            {poll.isPending ? "가져오는 중…" : "지금 가져오기"}
          </button>
        )}
      </div>

      <p className="text-sm text-slate-600">
        MES 가 검사 결과를 가져가면 검사기(AIVIS) KPI 의 ‘MES 수신 확인’ 건수가 올라갑니다.
      </p>

      {poll.isSuccess && poll.data && (
        <ResultLine ok={!poll.data.error} testid="poll-result">
          가져오기 {poll.data.error ? "중 오류" : "완료"} — {pollSummary(poll.data)}
          {poll.data.error && <span className="block text-xs">{poll.data.error}</span>}
        </ResultLine>
      )}
      {poll.isError && (
        <ResultLine ok={false} testid="poll-result">
          가져오기 실패: {(poll.error as Error)?.message}
        </ResultLine>
      )}

      {status.isError ? (
        <MesError what="연계 상태 조회" error={status.error} testid="link-error" />
      ) : s ? (
        <StatusPanel s={s} />
      ) : (
        <p className="text-sm text-slate-400">불러오는 중…</p>
      )}

      {s && <RunsTable runs={s.runs} />}

      <QualityTable />
    </div>
  );
}

function StatusPanel({ s }: { s: IntakeStatus }): JSX.Element {
  const stale = isPendingStale(s);
  return (
    <section className="space-y-3" data-testid="link-status">
      {s.interface_error && (
        <div className="rounded-md bg-ng-bg p-3 text-sm text-ng-fg" role="alert" data-testid="interface-error">
          <span aria-hidden="true">✕ </span>
          검사기 인터페이스 테이블을 읽지 못했습니다
          <div className="mt-1 break-all text-xs opacity-80">{s.interface_error}</div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Tile label="연계 방식" testid="link-mode">
          {s.table_enabled ? (
            <StatusBadge view={{ symbol: "●", text: "테이블 연계 켜짐", tone: "ok" }} />
          ) : (
            <>
              <StatusBadge view={{ symbol: "○", text: "테이블 연계 꺼짐", tone: "neutral" }} />
              <p className="mt-1 text-xs text-slate-500">REST 수신만 받는다(MES_AIVIS_DB_URL 미설정)</p>
            </>
          )}
          <p className="mt-1 text-xs text-slate-500">
            REST 수신 토큰: {s.rest_token_required ? "필요" : "불필요(내부 호출)"}
          </p>
        </Tile>

        <Tile label="폴러" testid="link-poller">
          {!s.table_enabled ? (
            <span className="text-slate-400">사용 안 함</span>
          ) : s.poller_running ? (
            <StatusBadge view={{ symbol: "▶", text: "동작 중", tone: "ok" }} />
          ) : (
            <StatusBadge view={{ symbol: "✕", text: "멈춤", tone: "danger" }} />
          )}
          <p className="mt-1 text-xs tabular-nums text-slate-500">
            주기 {fmtNum(s.poll_interval_s, 1)}초 · 마지막 {fmtDateTime(s.last_poll_at)}
          </p>
          {s.last_result && (
            <p className="mt-1 text-xs text-slate-500" data-testid="last-result">
              {pollSummary(s.last_result)}
              {s.last_result.error && (
                <span className="block text-ng-fg">
                  <span aria-hidden="true">✕ </span>
                  {s.last_result.error}
                </span>
              )}
            </p>
          )}
        </Tile>

        <Tile label="검사기에서 아직 안 가져간 건수" testid="pending" tone={stale ? "warn" : undefined}>
          <span className="text-2xl font-bold tabular-nums" data-testid="pending-count">
            {stale && <span aria-hidden="true">⚠ </span>}
            {s.interface_pending === null ? "-" : fmtNum(s.interface_pending, 0)}
          </span>
          {stale ? (
            <p className="mt-1 text-xs font-semibold" data-testid="pending-warn">
              쌓여 있는데 마지막 가져오기가 {fmtNum(s.poll_interval_s * 2, 0)}초보다 오래됐습니다 —
              MES 폴러가 멈췄는지 확인하세요.
            </p>
          ) : (
            <p className="mt-1 text-xs text-slate-500">
              {s.interface_pending === null
                ? "테이블 연계가 꺼져 있으면 알 수 없습니다."
                : "다음 주기에 가져갑니다."}
            </p>
          )}
        </Tile>

        <Tile label="받은 기록" testid="link-records">
          <div className="flex items-baseline gap-3 tabular-nums">
            <span>
              <span className="text-2xl font-bold">{fmtNum(s.records_today, 0)}</span>
              <span className="ml-1 text-xs text-slate-500">오늘</span>
            </span>
            <span>
              <span className="text-lg font-semibold">{fmtNum(s.records_total, 0)}</span>
              <span className="ml-1 text-xs text-slate-500">전체</span>
            </span>
          </div>
          <p className="mt-1 text-xs tabular-nums text-slate-500">
            마지막 수신 {fmtDateTime(s.last_received_at)}
          </p>
        </Tile>
      </div>
    </section>
  );
}

function Tile({
  label,
  children,
  tone,
  testid,
}: {
  label: string;
  children: ReactNode;
  tone?: MesTone;
  testid?: string;
}): JSX.Element {
  // 정상은 테두리만, 주의가 필요할 때만 색으로 채운다(모니터 화면 규칙).
  return (
    <div
      className={`rounded-lg border p-3 ${tone ? `${TONE_CLASS[tone]} border-amber-300` : "border-slate-200 bg-white"}`}
      data-testid={testid}
    >
      <div className="mb-1 text-xs font-medium text-slate-500">{label}</div>
      {children}
    </div>
  );
}

function RunsTable({ runs }: { runs: IntakeStatus["runs"] }): JSX.Element {
  return (
    <section className="card">
      <h2 className="border-b border-slate-100 px-4 py-3 font-semibold">최근 수신</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm" data-testid="runs-table">
          <thead className={THEAD_CLASS}>
            <tr>
              <Th>시각</Th>
              <Th>방식</Th>
              <Th right>신규</Th>
              <Th right>갱신</Th>
              <Th>오류</Th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r, i) => (
              <tr key={`${r.ts}-${i}`} className="border-t border-slate-100">
                <Td>
                  <span className="whitespace-nowrap tabular-nums">{fmtDateTime(r.ts)}</span>
                </Td>
                <Td>{intakeSourceLabel(r.source)}</Td>
                <Td right>{fmtNum(r.received, 0)}</Td>
                <Td right>{fmtNum(r.updated, 0)}</Td>
                <Td>
                  {r.error ? (
                    <span className="break-all text-ng-fg">
                      <span aria-hidden="true">✕ </span>
                      {r.error}
                    </span>
                  ) : (
                    <span className="text-slate-400">-</span>
                  )}
                </Td>
              </tr>
            ))}
            {runs.length === 0 && (
              <EmptyRow colSpan={5} testid="runs-empty">
                아직 수신 기록이 없습니다.
              </EmptyRow>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function tubeText(r: QualityOut): string {
  // 개수 확인 행은 튜브 번호 대신 "검출/기준" 이 의미 있다.
  if (isCountStage(r.inspection_stage)) {
    return r.count_detected === null
      ? "-"
      : `${fmtNum(r.count_detected, 0)}/${fmtNum(r.count_expected, 0)}개`;
  }
  return r.tube_index === null ? "-" : `#${r.tube_index + 1}`;
}

function QualityTable(): JSX.Element {
  const q = useQuery({
    queryKey: ["mes", "quality", QUALITY_LIMIT],
    queryFn: () => fetchQuality({ limit: QUALITY_LIMIT }),
    refetchInterval: INTAKE_REFETCH_MS * 2,
  });
  const rows = q.data ?? [];
  return (
    <section className="card">
      <h2 className="border-b border-slate-100 px-4 py-3 font-semibold">
        최근 받은 품질이력 <span className="text-xs font-normal text-slate-400">(최근 {QUALITY_LIMIT}건)</span>
      </h2>
      {q.isError ? (
        <div className="p-4">
          <MesError what="품질이력 조회" error={q.error} testid="quality-error" />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="quality-table">
            <thead className={THEAD_CLASS}>
              <tr>
                <Th>시각</Th>
                <Th>LOT</Th>
                <Th>작업지시</Th>
                <Th>단계</Th>
                <Th>튜브</Th>
                <Th>AI 판정</Th>
                <Th>재확인</Th>
                <Th>불량유형</Th>
                <Th>방식</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-slate-100" data-testid={`quality-row-${r.id}`}>
                  <Td>
                    <span className="whitespace-nowrap tabular-nums">{fmtDateTime(r.inspected_at)}</span>
                  </Td>
                  <Td>
                    <span className="whitespace-nowrap">{r.lot}</span>
                  </Td>
                  <Td>{r.work_order ?? <span className="text-slate-400">-</span>}</Td>
                  <Td>
                    <span className="whitespace-nowrap">{stageLabel(r.inspection_stage)}</span>
                  </Td>
                  <Td>
                    <span className="whitespace-nowrap tabular-nums">{tubeText(r)}</span>
                  </Td>
                  <Td>
                    <VerdictBadge verdict={r.final_verdict} />
                  </Td>
                  <Td>
                    {r.manual_verdict ? (
                      <VerdictBadge verdict={r.manual_verdict} />
                    ) : (
                      <span className="text-slate-400">-</span>
                    )}
                  </Td>
                  <Td>
                    {(r.defect_codes ?? []).length === 0
                      ? "-"
                      : (r.defect_codes ?? []).map(defectLabel).join(", ")}
                  </Td>
                  <Td>{intakeSourceLabel(r.source)}</Td>
                </tr>
              ))}
              {rows.length === 0 && !q.isFetching && (
                <EmptyRow colSpan={9} testid="quality-empty">
                  아직 받은 품질이력이 없습니다.
                </EmptyRow>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
