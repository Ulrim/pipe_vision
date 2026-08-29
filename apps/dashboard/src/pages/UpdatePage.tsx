import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { UpdateInfo, UpdateRemote } from "@/api/endpoints";
import { checkUpdate, fetchUpdateInfo, startUpdate } from "@/api/endpoints";
import { useAuthStore } from "@/store/auth";

/** 진행 상태 폴링 주기(ms). 업데이트는 수 분 걸리므로 3초면 충분하다. */
export const POLL_MS = 3000;

/** 로그는 "돌고 있구나"를 확인하는 용도 — 끝부분만 보여준다. */
export const LOG_TAIL_LINES = 8;

/**
 * 화면이 실제로 그리는 단계.
 * - restarting: 업데이트가 서비스를 재시작해 API 가 잠깐 끊긴 구간(정상).
 */
export type UpdatePhase = "idle" | "running" | "restarting" | "success" | "failed";

/**
 * 서버 상태 + 요청 성공 여부 → 화면 단계.
 *
 * 핵심: 업데이트 도중에는 API 자신이 재시작되므로 폴링이 **반드시 실패하는
 * 구간**이 있다. 그때 에러 화면으로 튕기면 사용자는 업데이트가 깨진 줄 안다.
 * 그래서 "업데이트를 시작했거나 진행 중이었다면" 요청 실패는 오류가 아니라
 * `restarting` 으로 본다(폴링은 계속 돈다).
 */
export function updatePhase(p: {
  state?: UpdateInfo["progress"]["state"] | null;
  /** 이 화면에서 업데이트를 시작했는가(상태 파일 반영 전 공백 구간 대비). */
  started: boolean;
  /** 마지막 폴링 요청이 실패했는가. */
  offline: boolean;
}): UpdatePhase {
  const wasRunning = p.state === "running" || p.started;
  if (p.offline) return wasRunning ? "restarting" : "idle";
  if (p.state === "success") return "success";
  if (p.state === "failed") return "failed";
  if (wasRunning) return "running";
  return "idle";
}

/** ISO 날짜 → "2026년 8월 29일". 현장 사용자는 커밋 날짜 형식을 읽지 않는다. */
export function fmtKoreanDate(iso: string | null | undefined): string {
  if (!iso) return "알 수 없음";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일`;
}

/**
 * 프로그램 업데이트 (M11 운영 보조 — 현장 관리자용).
 *
 * 현장 담당자는 개발자가 아니라 터미널을 쓸 수 없다. 이 화면은
 * [새 버전 확인] → [지금 업데이트](확인 대화상자) → 진행 표시 → 완료/실패
 * 까지를 버튼만으로 끝낸다. 업데이트가 서비스를 재시작하는 구간은
 * "재시작 중"으로 안내하고 폴링을 계속한다(§ updatePhase).
 *
 * pollMs 는 테스트에서 주기를 줄이기 위한 주입점이다(기본 3초).
 */
export function UpdatePage({ pollMs = POLL_MS }: { pollMs?: number } = {}): JSX.Element {
  const role = useAuthStore((s) => s.role);
  const isAdmin = role === "admin";

  const [started, setStarted] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [remote, setRemote] = useState<UpdateRemote | null>(null);

  const info = useQuery({
    queryKey: ["update-info"],
    queryFn: fetchUpdateInfo,
    enabled: isAdmin,
    // 재시도는 react-query 가 아니라 폴링 주기가 맡는다(재시작 구간이 길다).
    retry: false,
    refetchIntervalInBackground: true,
    refetchInterval: (query) =>
      query.state.data?.progress.state === "running" || started ? pollMs : false,
  });

  const data = info.data;
  const serverState = data?.progress.state ?? null;
  const phase = updatePhase({
    state: serverState,
    started,
    offline: info.isError,
  });
  const busy = phase === "running" || phase === "restarting";

  // 결과가 확정되면(성공/실패) 폴링을 멈춘다.
  useEffect(() => {
    if (serverState === "success" || serverState === "failed") setStarted(false);
  }, [serverState]);

  const check = useMutation({
    mutationFn: checkUpdate,
    onSuccess: (r) => setRemote(r),
  });

  const start = useMutation({
    mutationFn: startUpdate,
    onSuccess: (r) => {
      setConfirmOpen(false);
      if (r.started) {
        setStarted(true);
        void info.refetch();
      }
    },
  });

  if (!isAdmin) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-bold">프로그램 업데이트</h1>
        <div className="card p-6 text-sm text-slate-600" data-testid="update-forbidden">
          프로그램 업데이트는 <b>관리자 계정</b>만 사용할 수 있습니다. 관리자에게
          요청해 주세요.
        </div>
      </div>
    );
  }

  const current = data?.current;
  const available = current?.available === true;
  const logTail = data?.progress.log_tail ?? [];
  /**
   * 자동 재시작이 안 되는 설치본: 업데이트가 파일만 갱신하고 끝난다.
   * "완료"만 보여주면 사용자는 새 버전이 도는 줄 착각하므로, 시작 전과
   * 완료 후 양쪽에서 "직접 다시 시작해야 적용된다"를 반드시 알린다.
   */
  const manualRestart = current?.restart_supported === false;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-xl font-bold">프로그램 업데이트</h1>
        <span className="text-xs text-slate-500">
          새 버전이 나오면 이 화면에서 버튼으로 설치할 수 있습니다.
        </span>
      </div>

      {/* 현재 설치본 — 사용자가 이해하는 건 "언제 설치된 무엇인가"다. */}
      <div className="card p-4" data-testid="update-current">
        <div className="label">지금 사용 중인 프로그램</div>
        {info.isLoading && !data ? (
          <p className="text-slate-400">불러오는 중…</p>
        ) : current ? (
          <>
            <div className="text-2xl font-bold" data-testid="current-date">
              {fmtKoreanDate(current.date)} 설치본
            </div>
            <p className="mt-1 text-sm text-slate-600" data-testid="current-subject">
              {current.subject ?? "설명 없음"}
            </p>
            <p className="mt-2 text-xs text-slate-400" data-testid="current-detail">
              버전 코드 {current.commit ?? "-"}
              {current.branch ? ` · 배포 채널 ${current.branch}` : ""}
            </p>
          </>
        ) : (
          <p className="text-slate-400">버전 정보를 확인할 수 없습니다.</p>
        )}

        {data && !available && (
          <div
            className="mt-3 rounded-md bg-amber-50 p-3 text-sm text-amber-900"
            data-testid="update-unavailable"
          >
            <span aria-hidden="true">! </span>
            이 설치본은 자동 업데이트를 지원하지 않습니다. 설치를 담당한 곳에
            문의해 주세요.
          </div>
        )}

        {available && manualRestart && (
          <div
            className="mt-3 rounded-md bg-amber-50 p-3 text-sm text-amber-900"
            data-testid="restart-warning"
          >
            <span aria-hidden="true">! </span>
            이 설치본은 업데이트 후 자동으로 다시 시작하지 못합니다. 업데이트가
            끝나면 프로그램을 직접 다시 시작해야 새 버전이 적용됩니다.
            <div className="mt-1 text-xs opacity-90">
              부팅 자동시작을 등록해두면 이후에는 이 버튼만으로 끝납니다.
            </div>
          </div>
        )}
      </div>

      {/* 정보 조회 자체가 실패한 경우 — 단, 재시작 구간은 아래 진행 카드가 맡는다. */}
      {info.isError && !busy && (
        <div className="card bg-ng-bg p-3 text-sm text-ng-fg" data-testid="update-error">
          <span aria-hidden="true">X </span>
          업데이트 정보를 불러오지 못했습니다. 장비 전원과 네트워크를 확인해
          주세요.
          <div className="mt-1 text-xs opacity-80">
            {(info.error as Error)?.message}
          </div>
        </div>
      )}

      {/* 새 버전 확인 */}
      <div className="card space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn-primary"
            data-testid="check-button"
            disabled={!available || check.isPending || busy}
            onClick={() => check.mutate()}
          >
            {check.isPending ? "확인 중…" : "새 버전 확인"}
          </button>
          <span className="text-xs text-slate-500">
            인터넷에 연결된 상태에서 눌러 주세요. 확인만 하고 설치하지는
            않습니다.
          </span>
        </div>

        {check.isError && (
          <div className="rounded-md bg-ng-bg p-3 text-sm text-ng-fg" data-testid="check-error">
            <span aria-hidden="true">X </span>
            새 버전을 확인하지 못했습니다.
            <div className="mt-1 text-xs opacity-80">
              {(check.error as Error)?.message}
            </div>
          </div>
        )}

        {remote && !remote.reachable && (
          <div
            className="rounded-md bg-amber-50 p-3 text-sm text-amber-900"
            data-testid="check-offline"
          >
            <span aria-hidden="true">! </span>
            인터넷에 연결되어 있는지 확인해 주세요.
            <div className="mt-1 text-xs opacity-80">{remote.error ?? ""}</div>
          </div>
        )}

        {remote?.reachable && remote.behind === 0 && (
          <div
            className="rounded-md bg-ok-bg p-3 text-sm text-ok-fg"
            data-testid="check-latest"
          >
            <span aria-hidden="true">O </span>
            <b>최신 버전입니다.</b> 지금 설치된 프로그램이 가장 최신이라 따로
            할 일이 없습니다.
          </div>
        )}

        {remote?.reachable && remote.behind !== null && remote.behind > 0 && (
          <div className="rounded-md bg-slate-50 p-3 text-sm" data-testid="check-behind">
            <div className="text-base font-bold">
              새 버전 {remote.behind}개 있음
            </div>
            <p className="mt-1 text-slate-600">
              최신 버전 날짜: {fmtKoreanDate(remote.latest_date)}
            </p>
            <p className="text-slate-600" data-testid="check-behind-subject">
              {remote.latest_subject ?? "설명 없음"}
            </p>
            <button
              type="button"
              className="btn-primary mt-3"
              data-testid="start-button"
              disabled={!available || busy || start.isPending}
              onClick={() => setConfirmOpen(true)}
            >
              지금 업데이트
            </button>
          </div>
        )}

        {remote?.reachable && remote.behind === null && (
          <div
            className="rounded-md bg-amber-50 p-3 text-sm text-amber-900"
            data-testid="check-unknown"
          >
            <span aria-hidden="true">! </span>
            새 버전이 몇 개인지 확인하지 못했습니다. 잠시 후 다시 눌러 주세요.
          </div>
        )}

        {start.isError && (
          <div className="rounded-md bg-ng-bg p-3 text-sm text-ng-fg" data-testid="start-error">
            <span aria-hidden="true">X </span>
            업데이트를 시작하지 못했습니다.
            <div className="mt-1 text-xs opacity-80">
              {(start.error as Error)?.message}
            </div>
          </div>
        )}

        {start.data && !start.data.started && (
          <div
            className="rounded-md bg-amber-50 p-3 text-sm text-amber-900"
            data-testid="start-rejected"
          >
            <span aria-hidden="true">! </span>
            {start.data.message}
          </div>
        )}
      </div>

      {/* 진행/결과 */}
      {phase === "running" && (
        <div className="card p-4" data-testid="update-progress">
          <div className="text-lg font-bold">업데이트 중…</div>
          <p className="mt-1 text-sm text-slate-600">
            화면이 잠시 끊길 수 있습니다. 이 창을 닫지 말고 기다려 주세요.
            (보통 몇 분 걸립니다.)
          </p>
          <LogBox lines={logTail} />
        </div>
      )}

      {phase === "restarting" && (
        <div className="card p-4" data-testid="update-restarting">
          <div className="text-lg font-bold">재시작 중… 잠시만 기다려 주세요</div>
          <p className="mt-1 text-sm text-slate-600">
            새 프로그램을 적용하느라 연결이 잠깐 끊겼습니다. <b>정상 과정</b>이며
            잠시 후 자동으로 다시 연결됩니다.
          </p>
          <LogBox lines={logTail} />
        </div>
      )}

      {phase === "success" && (
        <div className="card bg-ok-bg p-4 text-ok-fg" data-testid="update-success">
          <div className="text-lg font-bold">
            <span aria-hidden="true">O </span>
            {manualRestart ? "내려받기 완료 — 아직 적용 전" : "업데이트 완료"}
          </div>
          {manualRestart ? (
            <p className="mt-1 text-sm" data-testid="success-manual-restart">
              새 버전이 내려받아졌습니다. <b>아직 적용되지 않았습니다</b> —
              프로그램을 다시 시작해 주세요. 화면만 새로고침하면 이전 버전이
              그대로 돕니다.
            </p>
          ) : (
            <p className="mt-1 text-sm">
              새 화면을 불러오려면 아래 버튼을 눌러 주세요. 눌러야 바뀐 내용이
              보입니다.
            </p>
          )}
          <button
            type="button"
            className="btn-primary mt-3"
            data-testid="reload-button"
            onClick={() => window.location.reload()}
          >
            화면 새로고침
          </button>
          <LogBox lines={logTail} />
        </div>
      )}

      {phase === "failed" && (
        <div className="card p-4" data-testid="update-failed">
          <div className="text-lg font-bold text-ng-fg">
            <span aria-hidden="true">X </span>업데이트 실패
          </div>
          <p className="mt-1 text-sm text-slate-600">
            업데이트는 적용되지 않았지만 <b>이전 버전으로 계속 사용할 수
            있습니다.</b> 아래 내용을 담당자에게 전달해 주세요.
          </p>
          <LogBox lines={logTail} />
        </div>
      )}

      {confirmOpen && (
        <ConfirmDialog
          manualRestart={manualRestart}
          pending={start.isPending}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => start.mutate()}
        />
      )}
    </div>
  );
}

/** 진행 로그 끝부분 — "멈춘 게 아니라 돌고 있다"를 보여주는 용도. */
function LogBox({ lines }: { lines: string[] }): JSX.Element | null {
  if (lines.length === 0) return null;
  return (
    <pre
      className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-slate-900 p-3 font-mono text-xs text-slate-100"
      data-testid="update-log"
    >
      {lines.slice(-LOG_TAIL_LINES).join("\n")}
    </pre>
  );
}

/**
 * 확인 대화상자(브라우저 confirm 대신 인앱 모달).
 * 교대 중 오조작을 막으려면 "무슨 일이 벌어지는지"를 화면에 적어야 한다.
 */
function ConfirmDialog({
  pending,
  manualRestart,
  onCancel,
  onConfirm,
}: {
  pending: boolean;
  /** 자동 재시작 불가 설치본이면 "직접 다시 시작해야 한다"를 여기서도 알린다. */
  manualRestart: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}): JSX.Element {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="업데이트 확인"
      onClick={onCancel}
      data-testid="update-confirm"
    >
      <div
        className="card w-full max-w-md p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-bold">지금 업데이트할까요?</h2>
        <p className="mt-2 text-sm text-slate-700">
          업데이트 중에는 검사가 잠시 멈추고 화면 연결이 끊길 수 있습니다.
          진행할까요?
        </p>
        {manualRestart && (
          <p
            className="mt-2 rounded-md bg-amber-50 p-3 text-sm text-amber-900"
            data-testid="confirm-restart-warning"
          >
            <span aria-hidden="true">! </span>
            이 설치본은 업데이트 후 자동으로 다시 시작하지 못합니다. 업데이트가
            끝나면 프로그램을 직접 다시 시작해야 새 버전이 적용됩니다.
            <span className="mt-1 block text-xs opacity-90">
              부팅 자동시작을 등록해두면 이후에는 이 버튼만으로 끝납니다.
            </span>
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            className="btn-ghost"
            data-testid="confirm-cancel"
            onClick={onCancel}
          >
            취소
          </button>
          <button
            type="button"
            className="btn-primary"
            data-testid="confirm-start"
            disabled={pending}
            onClick={onConfirm}
          >
            {pending ? "시작하는 중…" : "업데이트 시작"}
          </button>
        </div>
      </div>
    </div>
  );
}
