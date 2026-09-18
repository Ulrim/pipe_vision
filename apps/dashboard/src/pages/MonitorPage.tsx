import { useQuery } from "@tanstack/react-query";
import type { SystemStatus } from "@/api/endpoints";
import { fetchSystemStatus } from "@/api/endpoints";
import { fmtDateTime, fmtNum } from "@/lib/format";

/** 자동 갱신 주기(ms) — 현장 장비 감시용 짧은 폴링. */
export const REFETCH_MS = 5000;

/** §1.2 3번 — 검사 처리속도 목표(ms/ea). 초과 시 경고 표기. */
export const PROC_TIME_TARGET_MS = 300;

/** 라즈베리파이 발열 임계(℃). 70 이상 경고, 80 이상 위험. */
export const CPU_TEMP_WARN_C = 70;
export const CPU_TEMP_DANGER_C = 80;

/** 표시 심각도. 색 단독 사용 금지 — 항상 기호+한국어와 함께 쓴다(적녹색약 고려). */
export type Severity = "ok" | "warn" | "danger" | "unknown";

const SEV_BADGE: Record<Severity, string> = {
  ok: "bg-ok-bg text-ok-fg",
  warn: "bg-amber-100 text-amber-900",
  danger: "bg-ng-bg text-ng-fg",
  unknown: "bg-slate-100 text-slate-600",
};

const SEV_SYMBOL: Record<Severity, string> = {
  ok: "O",
  warn: "△",
  danger: "X",
  unknown: "?",
};

export interface StatusLabel {
  sev: Severity;
  /** ASCII/기호 마커 — 색을 못 구분해도 상태를 읽을 수 있게 한다. */
  symbol: string;
  text: string;
}

/** 워커 상태 → 기호+한국어 라벨. */
export function workerStatusLabel(s: SystemStatus["services"]["worker"]): StatusLabel {
  if (s === "up") return { sev: "ok", symbol: SEV_SYMBOL.ok, text: "정상" };
  if (s === "stale") return { sev: "warn", symbol: SEV_SYMBOL.warn, text: "응답 지연" };
  return { sev: "danger", symbol: SEV_SYMBOL.danger, text: "정지" };
}

/** DB 상태 → 기호+한국어 라벨(워커와 동일 규칙). */
export function dbStatusLabel(s: SystemStatus["services"]["db"]): StatusLabel {
  return s === "up"
    ? { sev: "ok", symbol: SEV_SYMBOL.ok, text: "정상" }
    : { sev: "danger", symbol: SEV_SYMBOL.danger, text: "정지" };
}

/** CPU 온도 심각도(파이 발열). null = 측정 불가. */
export function cpuTempSeverity(t: number | null): Severity {
  if (t === null || !Number.isFinite(t)) return "unknown";
  if (t >= CPU_TEMP_DANGER_C) return "danger";
  if (t >= CPU_TEMP_WARN_C) return "warn";
  return "ok";
}

/** 사용률(%) 심각도 — 90% 이상 위험, 80% 이상 경고. */
export function percentSeverity(p: number | null): Severity {
  if (p === null || !Number.isFinite(p)) return "unknown";
  if (p >= 90) return "danger";
  if (p >= 80) return "warn";
  return "ok";
}

/** 상대시간 한국어 표기. now 를 주입받아 결정적으로 동작한다. */
export function relativeTimeKo(
  iso: string | null,
  now: number = Date.now(),
): string {
  if (!iso) return "기록 없음";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const sec = Math.floor((now - t) / 1000);
  if (sec < 0) return "방금 전";
  if (sec < 60) return `${sec}초 전`;
  if (sec < 3600) return `${Math.floor(sec / 60)}분 전`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}시간 전`;
  return `${Math.floor(sec / 86400)}일 전`;
}

/** null 은 반드시 "측정 불가" — 0 으로 표시하면 정상 수치로 오해된다. */
export function fmtMetric(
  v: number | null | undefined,
  unit = "",
  digits = 1,
): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "측정 불가";
  return `${fmtNum(v, digits)}${unit}`;
}

/** "사용 / 전체" 병기. 둘 다 없으면 측정 불가. */
function fmtUsage(
  used: number | null,
  total: number | null,
  unit: string,
  digits = 0,
): string {
  if (used === null && total === null) return "측정 불가";
  const u = used === null ? "측정 불가" : fmtNum(used, digits);
  const t = total === null ? "측정 불가" : fmtNum(total, digits);
  return `${u} / ${t} ${unit}`;
}

/**
 * 시스템 모니터링 (M11 운영 보조).
 *
 * 현장 라즈베리파이를 사무실 PC 웹에서 원격 감시한다. GET /system/status 를
 * 5초 주기로 폴링해 서비스 상태(워커/DB/오더), 하드웨어 자원(온도·부하·
 * 메모리·디스크), 검사 현황, 최근 오류를 한 화면에 모아 보여준다.
 * 상태는 색 단독이 아니라 기호(O/△/X)+한국어 문자로 표기한다(색약 고려).
 */
export function MonitorPage(): JSX.Element {
  const { data, isLoading, isError, error, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ["system-status"],
    queryFn: fetchSystemStatus,
    refetchInterval: REFETCH_MS,
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">시스템 모니터링</h1>
        <span className="text-xs text-slate-500" data-testid="monitor-updated">
          갱신: {dataUpdatedAt ? fmtDateTime(new Date(dataUpdatedAt).toISOString()) : "-"} (5초마다 자동 갱신)
        </span>
        {isFetching && <span className="text-sm text-slate-400">불러오는 중…</span>}
      </div>

      {isError && (
        <div className="card bg-ng-bg p-3 text-sm text-ng-fg" data-testid="monitor-error">
          <span aria-hidden="true">X </span>
          API 연결 실패 — 파이 전원/네트워크 확인
          <div className="mt-1 text-xs opacity-80">{(error as Error)?.message}</div>
        </div>
      )}

      {isLoading && !data && (
        <div className="card p-6 text-center text-slate-400" data-testid="monitor-loading">
          상태를 불러오는 중…
        </div>
      )}

      {data && <MonitorBody status={data} />}
    </div>
  );
}

function MonitorBody({ status }: { status: SystemStatus }): JSX.Element {
  const { system: sys, services: svc, inspection: insp, active_order: order } = status;
  const worker = workerStatusLabel(svc.worker);
  const db = dbStatusLabel(svc.db);
  const tempSev = cpuTempSeverity(sys.cpu_temp_c);

  return (
    <>
      {sys.throttled === true && (
        <div
          className="card bg-ng-bg p-3 text-sm font-semibold text-ng-fg"
          data-testid="throttle-banner"
        >
          <span aria-hidden="true">! </span>
          전원 부족/스로틀 감지 — 어댑터·케이블과 냉각 상태를 확인하세요.
        </div>
      )}

      {/* 서비스 상태 배지 3종 */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatusCard
          testid="svc-worker"
          title="검사 워커"
          label={worker}
          sub={
            svc.worker_last_seen_s === null
              ? "마지막 응답 기록 없음"
              : `마지막 응답 ${fmtNum(svc.worker_last_seen_s, 0)}초 전`
          }
        />
        <StatusCard
          testid="svc-db"
          title="데이터베이스"
          label={db}
          sub={db.sev === "ok" ? "검사결과 저장 가능" : "저장 불가 — 즉시 확인 필요"}
        />
        <StatusCard
          testid="svc-order"
          title="활성 오더"
          label={
            order
              ? { sev: "ok", symbol: SEV_SYMBOL.ok, text: `${order.item_code} · ${order.lot ?? "LOT 미지정"}` }
              : { sev: "unknown", symbol: SEV_SYMBOL.unknown, text: "오더 미설정" }
          }
          sub={
            order
              ? `작업지시 ${order.work_order ?? "미지정"}`
              : "품목/LOT 미설정 — 기준정보 화면에서 지정"
          }
        />
      </div>

      {/* 시스템 자원 */}
      <div className="card p-4">
        <h2 className="mb-3 font-semibold">시스템 자원</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <MetricCard
            testid="res-cpu-temp"
            title="CPU 온도"
            value={fmtMetric(sys.cpu_temp_c, "℃")}
            sev={tempSev}
            note={
              tempSev === "danger"
                ? `위험 — ${CPU_TEMP_DANGER_C}℃ 이상 과열`
                : tempSev === "warn"
                  ? `경고 — ${CPU_TEMP_WARN_C}℃ 이상 발열`
                  : tempSev === "ok"
                    ? `정상 (${CPU_TEMP_WARN_C}℃ 미만)`
                    : "센서 값을 읽지 못했습니다"
            }
          />
          <MetricCard
            testid="res-cpu"
            title="CPU 사용률"
            value={fmtMetric(sys.cpu_percent, "%")}
            sev={percentSeverity(sys.cpu_percent)}
            note={`부하(1분) ${fmtMetric(sys.load_1m, "", 2)}`}
          />
          <MetricCard
            testid="res-mem"
            title="메모리 사용률"
            value={fmtMetric(sys.mem_percent, "%")}
            sev={percentSeverity(sys.mem_percent)}
            note={fmtUsage(sys.mem_used_mb, sys.mem_total_mb, "MB")}
          />
          <MetricCard
            testid="res-disk"
            title="디스크 사용률"
            value={fmtMetric(sys.disk_percent, "%")}
            sev={percentSeverity(sys.disk_percent)}
            note={fmtUsage(sys.disk_used_gb, sys.disk_total_gb, "GB", 1)}
          />
        </div>
      </div>

      {/* 검사 현황 */}
      <div className="card p-4">
        <h2 className="mb-3 font-semibold">검사 현황</h2>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm md:grid-cols-4">
          <Stat
            testid="insp-last-hour"
            k="최근 1시간 (총/NG)"
            v={`${fmtNum(insp.last_hour.total, 0)} / ${fmtNum(insp.last_hour.ng, 0)}`}
            note={`NG율 ${fmtNum(insp.last_hour.ng_rate_pct, 2)}%`}
          />
          <Stat
            testid="insp-today"
            k="오늘 (총/NG)"
            v={`${fmtNum(insp.today.total, 0)} / ${fmtNum(insp.today.ng, 0)}`}
            note={`NG율 ${fmtNum(insp.today.ng_rate_pct, 2)}%`}
          />
          <Stat
            testid="insp-avg"
            k="평균 처리속도"
            v={fmtMetric(insp.avg_proc_time_ms, "ms")}
            note={procNote(insp.avg_proc_time_ms)}
            alert={isProcOver(insp.avg_proc_time_ms)}
          />
          <Stat
            testid="insp-p95"
            k="p95 처리속도"
            v={fmtMetric(insp.p95_proc_time_ms, "ms")}
            note={procNote(insp.p95_proc_time_ms)}
            alert={isProcOver(insp.p95_proc_time_ms)}
          />
          <Stat
            testid="insp-last-at"
            k="마지막 검사"
            v={relativeTimeKo(insp.last_inspected_at)}
            note={insp.last_inspected_at ? fmtDateTime(insp.last_inspected_at) : "검사 이력 없음"}
          />
          <Stat
            testid="insp-mes-pending"
            k="MES 미전송"
            v={`${fmtNum(insp.mes_pending, 0)}건`}
            note={
              insp.mes_pending > 0
                ? "△ 미전송 대기 — 연계 상태 확인 필요"
                : "O 전량 연계 완료"
            }
            alert={insp.mes_pending > 0}
          />
        </dl>
      </div>

      {/* 최근 오류 */}
      <div className="card p-4">
        <h2 className="mb-3 font-semibold">최근 오류</h2>
        {status.recent_errors.length === 0 ? (
          <p className="text-sm text-slate-400" data-testid="errors-empty">
            최근 오류 없음
          </p>
        ) : (
          <ul className="space-y-1 text-sm" data-testid="error-list">
            {status.recent_errors.map((e, i) => (
              <li
                key={`${e.ts}-${i}`}
                className="flex gap-3 border-b border-slate-100 py-1 last:border-0"
                data-testid={`error-row-${i}`}
              >
                <span className="shrink-0 tabular-nums text-slate-400">
                  {fmtDateTime(e.ts)}
                </span>
                <span className="text-ng-fg">{e.message}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

function isProcOver(v: number | null): boolean {
  return v !== null && Number.isFinite(v) && v > PROC_TIME_TARGET_MS;
}

function procNote(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "측정값 없음";
  return isProcOver(v)
    ? `△ ${PROC_TIME_TARGET_MS}ms 목표 초과`
    : `O ${PROC_TIME_TARGET_MS}ms 목표 이내`;
}

function StatusCard({
  testid, title, label, sub,
}: {
  testid: string;
  title: string;
  label: StatusLabel;
  sub: string;
}): JSX.Element {
  return (
    <div className="card p-4" data-testid={testid}>
      <div className="label">{title}</div>
      <div
        className={`inline-flex items-center gap-1.5 rounded px-2 py-1 text-base font-bold ${SEV_BADGE[label.sev]}`}
      >
        <span aria-hidden="true">[{label.symbol}]</span>
        <span>{label.text}</span>
      </div>
      <div className="mt-1 text-xs tabular-nums text-slate-500">{sub}</div>
    </div>
  );
}

function MetricCard({
  testid, title, value, sev, note,
}: {
  testid: string;
  title: string;
  value: string;
  sev: Severity;
  note: string;
}): JSX.Element {
  return (
    <div className={`rounded-md p-3 ${SEV_BADGE[sev]}`} data-testid={testid}>
      <div className="text-xs font-medium opacity-80">
        <span aria-hidden="true">[{SEV_SYMBOL[sev]}] </span>
        {title}
      </div>
      <div className="text-2xl font-bold tabular-nums">{value}</div>
      <div className="mt-0.5 text-xs tabular-nums opacity-90">{note}</div>
    </div>
  );
}

function Stat({
  k, v, note, testid, alert,
}: {
  k: string;
  v: string;
  note: string;
  testid: string;
  alert?: boolean;
}): JSX.Element {
  return (
    <div data-testid={testid}>
      <dt className="text-xs text-slate-400">{k}</dt>
      <dd className="text-lg font-semibold tabular-nums">{v}</dd>
      <div className={`text-xs tabular-nums ${alert ? "font-semibold text-ng" : "text-slate-500"}`}>
        {note}
      </div>
    </div>
  );
}
