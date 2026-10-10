import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchInspectionImageBlob,
  fetchStations,
  type StationHost,
  type StationLive,
} from "@/api/endpoints";
import {
  SEV_BADGE,
  cpuTempSeverity,
  percentSeverity,
  workerStatusLabel,
  type Severity,
} from "@/pages/MonitorPage";
import { fmtNum } from "@/lib/format";
import { hmiUrlFor } from "@/lib/env";
import { isCountStage, reasonLines, stageLabel } from "@/lib/stage";

/** 갱신 주기(ms). 작업자 화면만큼 빠를 필요는 없지만 "지금" 이어야 한다. */
export const LIVE_REFETCH_MS = 2000;

/**
 * 실시간 현황 — 파이(카메라) 여러 대를 한 화면에 (2026-10-09).
 *
 * 도입기업: "동시에 여러 개의 라즈베리파이 + 카메라가 작동할 것이다. 이것을
 * 웹페이지에서 확인하고 싶다." 작업자 화면(HMI)은 한 라인을 크게 보는 화면이고,
 * 이 화면은 **모든 라인을 나란히** 본다. 스테이션마다 카드 한 장:
 *
 *   [카메라 · 모드 · 상태]
 *   [마지막 판정(크게) + NG 사유 수치]  [판정 사진]
 *   [최근 1시간 / 오늘 / 처리 ms]
 *   [그 파이의 온도 · CPU · 메모리 · 디스크 · 전원]
 *
 * 표기 규칙(모니터 화면과 같다, ISA-101): 정상은 무채색, 색은 이상에만. 색만으로
 * 말하지 않는다 — 항상 기호와 한국어를 함께.
 *
 * **멈춘 라인의 마지막 판정은 흐리게.** 정지한 파이의 카드가 30분 전 "OK" 를
 * 선명하게 띄우고 있으면 지금도 잘 돌고 있는 것처럼 읽힌다.
 */
export function LivePage(): JSX.Element {
  const { data, isError, error, dataUpdatedAt } = useQuery({
    queryKey: ["system-stations"],
    queryFn: fetchStations,
    refetchInterval: LIVE_REFETCH_MS,
  });
  const stations = data?.stations ?? [];
  const up = stations.filter((s) => s.state === "up").length;
  const stale = stations.filter((s) => s.state === "stale").length;
  const down = stations.filter((s) => s.state === "down").length;
  const hourNg = stations.reduce((a, s) => a + s.last_hour.ng, 0);
  const hourTotal = stations.reduce((a, s) => a + s.last_hour.total, 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2" data-testid="live-summary">
        <Chip testid="sum-stations" text={`스테이션 ${stations.length}대`} />
        <Chip testid="sum-up" text={`가동 ${up}`} />
        {stale > 0 && <Chip testid="sum-stale" sev="warn" text={`⚠ 응답 지연 ${stale}`} />}
        {down > 0 && <Chip testid="sum-down" sev="danger" text={`✕ 정지 ${down}`} />}
        <Chip
          testid="sum-hour"
          text={`최근 1시간 ${fmtNum(hourTotal, 0)}건 · NG ${fmtNum(hourNg, 0)}`}
        />
        <span className="ml-auto text-xs text-slate-500" data-testid="live-updated">
          갱신 {dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString("ko-KR") : "-"}
          {" "}(2초마다)
        </span>
        <FullscreenButton />
      </div>

      {isError && (
        <div className="card bg-ng-bg p-3 text-sm text-ng-fg" data-testid="live-error">
          <span aria-hidden="true">✕ </span>
          서버 연결 실패 — 허브 파이 전원/네트워크 확인
          <div className="mt-1 text-xs opacity-80">{(error as Error)?.message}</div>
        </div>
      )}

      {data && stations.length === 0 && (
        <div className="card p-6 text-center text-slate-500" data-testid="live-empty">
          아직 연결된 카메라가 없습니다. 각 파이의 검사 워커가 켜지면 여기에 한 대씩 나타납니다.
        </div>
      )}

      <div className={`grid grid-cols-1 gap-4 ${gridCols(stations.length)}`} data-testid="live-grid">
        {stations.map((s) => (
          <StationCard key={s.cam_id} s={s} />
        ))}
      </div>
    </div>
  );
}

/**
 * 대수에 맞춘 열 수. 고정 3열이면 두 대일 때 화면 3분의 1이 빈다. 네 대는 3+1 보다
 * 2x2 가 한눈에 읽힌다. (Tailwind 는 클래스 문자열을 그대로 찾으므로 조합하지 않는다.)
 */
export function gridCols(n: number): string {
  if (n <= 1) return "";
  if (n === 2 || n === 4) return "lg:grid-cols-2";
  return "lg:grid-cols-2 2xl:grid-cols-3";
}

function Chip({ text, sev = "ok", testid }: { text: string; sev?: Severity; testid: string }) {
  return (
    <span
      className={`rounded-md border border-slate-200 px-2 py-1 text-sm font-semibold ${
        sev === "ok" ? "bg-white text-slate-700" : SEV_BADGE[sev]
      }`}
      data-testid={testid}
    >
      {text}
    </span>
  );
}

function FullscreenButton(): JSX.Element | null {
  if (typeof document === "undefined" || !document.documentElement.requestFullscreen) {
    return null;
  }
  return (
    <button
      type="button"
      className="btn-ghost text-sm"
      onClick={() => {
        if (document.fullscreenElement) void document.exitFullscreen?.();
        else void document.documentElement.requestFullscreen().catch(() => undefined);
      }}
    >
      전체 화면
    </button>
  );
}

/** "n초 전 / n분 전" — 짧게. */
export function agoKo(sec: number | null): string {
  if (sec === null || !Number.isFinite(sec)) return "응답 기록 없음";
  if (sec < 60) return `${Math.round(sec)}초 전`;
  if (sec < 3600) return `${Math.floor(sec / 60)}분 전`;
  return `${Math.floor(sec / 3600)}시간 전`;
}

function sinceIso(iso: string, now: number): number | null {
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : Math.max(0, (now - t) / 1000);
}

export function StationCard({ s, now = Date.now() }: { s: StationLive; now?: number }): JSX.Element {
  const health = workerStatusLabel(s.state);
  const latest = s.latest;
  const live = s.state === "up";
  const ng = latest?.final_verdict === "NG";
  const count = isCountStage(s.stage ?? latest?.inspection_stage);
  const reasons = latest ? reasonLines(latest, { detected: s.detected, expected: s.expected }) : [];
  const hmi = hmiUrlFor(s.cam_id);

  return (
    <section
      className={`card flex flex-col gap-3 p-4 ${s.state === "down" ? "border-2 border-ng" : ""}`}
      data-testid={`station-card-${s.cam_id}`}
      data-state={s.state}
    >
      {/* 머리: 어느 라인 · 무엇을 보나 · 살아있나 */}
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-black text-slate-900">{s.cam_id}</h2>
        <span
          className="rounded bg-slate-800 px-2 py-0.5 text-sm font-bold text-white"
          data-testid="card-stage"
        >
          {stageLabel(s.stage)}
        </span>
        {s.item_code && <span className="text-sm text-slate-500">{s.item_code}</span>}
        <span
          className={`ml-auto inline-flex items-center gap-1 rounded px-2 py-0.5 text-sm font-bold ${SEV_BADGE[health.sev]}`}
          data-testid="card-health"
        >
          {health.symbol && <span aria-hidden="true">{health.symbol}</span>}
          {health.text}
          <span className="whitespace-nowrap font-normal opacity-80">· {agoKo(s.last_seen_s)}</span>
        </span>
        {s.waiting && s.state === "up" && (
          <span className="rounded border border-slate-300 px-1.5 text-xs text-slate-600" data-testid="card-waiting">
            제품 대기(센서)
          </span>
        )}
      </header>

      {s.error && (
        <div className="rounded bg-ng-bg px-2 py-1 text-sm font-semibold text-ng-fg" data-testid="card-error">
          ✕ 취득/검사 오류: {s.error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-5">
        {/* 판정 */}
        <div className={`sm:col-span-2 ${live ? "" : "opacity-50"}`} data-testid="card-verdict-wrap">
          {latest ? (
            <>
              {!live && (
                <div className="mb-1 text-xs font-semibold text-slate-600">
                  {s.state === "down" ? "정지 — " : "응답 지연 — "}마지막 결과
                </div>
              )}
              <div
                className={`flex items-center gap-2 rounded-lg px-3 py-2 text-3xl font-black ${
                  ng ? "bg-ng-bg text-ng-fg" : "bg-ok-bg text-ok-fg"
                }`}
                data-testid="card-verdict"
                data-verdict={latest.final_verdict}
              >
                <span aria-hidden="true">{ng ? "✕" : "✓"}</span>
                {latest.final_verdict}
              </div>
              {reasons.length > 0 && (
                <ul className="mt-2 space-y-0.5 text-sm font-semibold text-ng-fg" data-testid="card-reasons">
                  {reasons.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              )}
              {count ? (
                <div className="mt-2 text-sm text-slate-700" data-testid="card-count">
                  검출 <b className="text-lg tabular-nums">{s.detected ?? "—"}</b>
                  {" / "}기준 <b className="tabular-nums">{s.expected ?? latest.limits?.expected_count ?? "—"}</b>
                </div>
              ) : (
                latest.frame_total > 1 && (
                  <div className="mt-2 text-sm text-slate-700" data-testid="card-frame">
                    한 장 {latest.frame_total}개 중 NG <b>{latest.frame_ng}</b>
                  </div>
                )
              )}
              {!count && latest.meas_length_mm !== null && (
                <div className="mt-1 text-sm tabular-nums text-slate-600">
                  길이 {fmtNum(latest.meas_length_mm, 2)}mm
                </div>
              )}
              <div className="mt-1 text-xs text-slate-500">
                LOT {latest.lot} ·{" "}
                <span className="whitespace-nowrap">{agoKo(sinceIso(latest.inspected_at, now))}</span>
              </div>
            </>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-500" data-testid="card-no-result">
              아직 검사 결과 없음
            </div>
          )}
        </div>

        {/* 사진 — 멈춘 라인이면 판정과 함께 흐리게(지금 사진이 아니다). */}
        <div className={`sm:col-span-3 ${live ? "" : "opacity-50"}`} data-testid="card-image-wrap">
          <LiveImage
            id={latest?.has_result_image ? latest.id : null}
            hadPath={latest?.has_result_image ?? false}
            hasLatest={!!latest}
          />
        </div>
      </div>

      {/* 실적 */}
      <dl className="grid grid-cols-3 gap-2 border-t border-slate-100 pt-2 text-sm" data-testid="card-stats">
        <Stat k="최근 1시간" v={`${s.last_hour.total} / NG ${s.last_hour.ng}`} sub={`NG율 ${fmtNum(s.last_hour.ng_rate_pct, 1)}%`} />
        <Stat k="오늘" v={`${s.today.total} / NG ${s.today.ng}`} sub={`NG율 ${fmtNum(s.today.ng_rate_pct, 1)}%`} />
        <ProcStat s={s} />
      </dl>

      <HostRow host={s.host} />

      {hmi && (
        <a
          href={hmi}
          target="_blank"
          rel="noreferrer"
          className="self-start text-sm font-semibold text-brand underline"
          data-testid="card-hmi-link"
        >
          이 라인 작업자 화면 열기 ↗
        </a>
      )}
    </section>
  );
}

/**
 * 처리시간(§1.2 지표3: 취득~저장, 300ms/ea). 다발이면 1개당으로 판정하고
 * 단계별 분해(취득·판정·저장)를 같이 적어 어디가 느린지 바로 보이게 한다.
 */
export function procView(s: Pick<StationLive, "proc_time_ms" | "timings">): {
  value: string; sub: string; alert: boolean;
} {
  const t = s.timings ?? null;
  const n = t?.n ?? 1;
  const ea = t?.per_ea_ms ?? s.proc_time_ms;
  if (ea === null || ea === undefined) return { value: "—", sub: "목표 300ms/ea", alert: false };
  const parts = [
    t?.grab_ms !== undefined ? `취득 ${t.grab_ms}` : null,
    t?.infer_ms !== undefined ? `판정 ${t.infer_ms}` : null,
    t?.save_ms !== undefined ? `저장 ${t.save_ms}` : null,
  ].filter(Boolean).join(" · ");
  const over = ea > 300;
  const head = n > 1 ? `${ea}ms/ea` : `${ea}ms`;
  const frame = n > 1 && t?.total_ms !== undefined ? `한 장 ${t.total_ms}ms ÷ ${n}개` : "";
  const sub = [over ? "⚠ 300ms 초과" : "목표 300ms/ea", frame, parts].filter(Boolean).join(" · ");
  return { value: head, sub, alert: over };
}

function ProcStat({ s }: { s: StationLive }) {
  const v = procView(s);
  return <Stat k="처리(취득~저장)" v={v.value} sub={v.sub} alert={v.alert} />;
}

function Stat({ k, v, sub, alert }: { k: string; v: string; sub: string; alert?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-slate-400">{k}</dt>
      <dd className="font-semibold tabular-nums">{v}</dd>
      <div className={`text-xs ${alert ? "font-semibold text-ng" : "text-slate-500"}`}>{sub}</div>
    </div>
  );
}

/** 그 파이의 건강. 정상은 무채색 숫자만, 이상만 색+기호. */
export function HostRow({ host }: { host: StationHost | null }): JSX.Element {
  if (!host) {
    return (
      <div className="text-xs text-slate-400" data-testid="card-host-none">
        파이 상태 정보 없음 (이 워커는 상태를 보내지 않는 이전 버전 — 업데이트하면 보입니다)
      </div>
    );
  }
  const temp = cpuTempSeverity(host.cpu_temp_c);
  const items: Array<{ k: string; v: string; sev: Severity; testid: string }> = [
    { k: "온도", v: host.cpu_temp_c === null ? "—" : `${fmtNum(host.cpu_temp_c, 0)}℃`, sev: temp, testid: "host-temp" },
    { k: "CPU", v: host.cpu_percent === null ? "—" : `${fmtNum(host.cpu_percent, 0)}%`, sev: percentSeverity(host.cpu_percent), testid: "host-cpu" },
    { k: "메모리", v: host.mem_percent === null ? "—" : `${fmtNum(host.mem_percent, 0)}%`, sev: percentSeverity(host.mem_percent), testid: "host-mem" },
    {
      k: "디스크",
      v: host.disk_percent === null
        ? "—"
        : `${fmtNum(host.disk_percent, 0)}%${host.disk_free_gb === null ? "" : ` (남음 ${fmtNum(host.disk_free_gb, 1)}GB)`}`,
      sev: percentSeverity(host.disk_percent),
      testid: "host-disk",
    },
    {
      k: "전원",
      v: host.throttled === null ? "—" : host.throttled ? "전원 부족" : "정상",
      sev: host.throttled ? "danger" : "ok",
      testid: "host-power",
    },
  ];
  return (
    <div className="flex flex-wrap gap-1.5 text-xs" data-testid="card-host">
      {items.map((it) => (
        <span
          key={it.k}
          className={`rounded px-1.5 py-0.5 ${it.sev === "ok" || it.sev === "unknown" ? "bg-slate-50 text-slate-600" : SEV_BADGE[it.sev]}`}
          data-testid={it.testid}
          data-sev={it.sev}
        >
          {it.sev === "warn" ? "⚠ " : it.sev === "danger" ? "✕ " : ""}
          {it.k} {it.v}
        </span>
      ))}
    </div>
  );
}

/**
 * 판정 사진. 새 결과가 오면 **새 사진이 다 받아진 뒤에** 바꾼다 — 2초마다
 * 빈 칸으로 깜빡이면 벽에 걸어 둔 화면으로 못 쓴다. 이전 objectURL 은 교체
 * 직후에 해제한다(메모리 누수 방지).
 */
function LiveImage({ id, hadPath, hasLatest }: { id: number | null; hadPath: boolean; hasLatest: boolean }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const shown = useRef<string | null>(null);

  useEffect(() => {
    if (id == null) return;
    let cancelled = false;
    fetchInspectionImageBlob(id, "result")
      .then((blob) => {
        if (cancelled) return;
        const next = URL.createObjectURL(blob);
        const prev = shown.current;
        shown.current = next;
        setUrl(next);
        setFailed(false);
        if (prev) URL.revokeObjectURL(prev);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(
    () => () => {
      if (shown.current) URL.revokeObjectURL(shown.current);
    },
    [],
  );

  if (!hasLatest) {
    return <div className="aspect-video rounded-lg bg-slate-100" data-testid="card-image-empty" />;
  }
  if (!hadPath) {
    // 행에 사진 경로가 없다 = 워커가 저장에 실패했다(디스크 가득 등).
    return (
      <div
        className="flex aspect-video items-center justify-center rounded-lg border border-dashed border-slate-300 p-3 text-center text-xs text-slate-500"
        data-testid="card-image-none"
      >
        이 결과에는 사진이 없습니다(그 파이에서 사진 저장 실패 — 디스크를 확인하세요).
      </div>
    );
  }
  if (failed && !url) {
    // 경로는 있는데 서버에 파일이 없다 = 사진이 그 파이 디스크에만 있다.
    return (
      <div
        className="flex aspect-video items-center justify-center rounded-lg border border-dashed border-slate-300 p-3 text-center text-xs text-slate-500"
        data-testid="card-image-missing"
      >
        사진이 서버에 없습니다. 이 파이가 사진을 자기 디스크에만 저장하고 있습니다
        — 그 파이의 worker.env 에 AIVIS_STORAGE_BACKEND=api 를 설정하세요.
      </div>
    );
  }
  return url ? (
    <img
      src={url}
      alt="최근 판정 사진"
      className="max-h-[55vh] w-full rounded-lg bg-black object-contain"
      data-testid="card-image"
    />
  ) : (
    <div className="aspect-video animate-pulse rounded-lg bg-slate-100" data-testid="card-image-loading" />
  );
}
