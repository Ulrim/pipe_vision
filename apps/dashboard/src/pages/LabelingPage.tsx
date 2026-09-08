/**
 * 정답 라벨링 화면 (부록 A.2/A.5, §5 M16, §1.2).
 *
 * **무엇을 푸는 화면인가**: 표면 결함 모델을 막고 있는 것은 카메라도 알고리즘도
 * 아니라 정답셋이다. 현장에는 검사 이미지가 수십 GB 쌓였지만 "이 사진이
 * 유분기인지 변색인지"를 사람이 적어 둔 기록이 없다. 라벨링 CLI 는 있었지만
 * 품질담당자가 터미널을 쓰지 않으므로 실제로는 아무 라벨도 만들어지지 않았다.
 *
 * **속도가 설계 목표**: 수백~수천 장을 넘겨야 하므로 한 장에 드는 조작이
 * 정확도만큼 중요하다.
 * - 숫자키 1~4 로 불량유형 토글, 0 으로 정상, B 로 경계 표시, Enter 로 저장+다음.
 * - 저장하면 즉시 다음 장으로 넘어가고, 큐는 값이 큰 것부터 서버가 정렬해 준다.
 * - 시스템 판정은 **기본으로 감춘다**. 먼저 보면 사람이 거기에 끌려가서(anchoring)
 *   정답셋이 모델을 그대로 베끼게 되고, 그러면 정확도 측정이 무의미해진다.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchLabelQueue,
  fetchLabelProgress,
  putLabel,
  type LabelQueueItem,
} from "@/api/endpoints";
import { useAuthedImage } from "@/hooks/useAuthedImage";
import { useAuthStore, canEdit } from "@/store/auth";
import { fmtDateTime, fmtNum } from "@/lib/format";

/** 불량유형 버튼 — 숫자키와 1:1. 코드는 §7.2, 설명은 부록 A.5 판정 기준. */
const CODES: { code: string; label: string; hint: string; key: string }[] = [
  { code: "LEN", label: "길이", hint: "기준 길이·공차 이탈", key: "1" },
  { code: "OIL", label: "유분기", hint: "세척 후 유분·오염 잔존(수분과 구분)", key: "2" },
  { code: "DIS", label: "변색", hint: "은→금→주황→갈색 등 이상 색", key: "3" },
  { code: "SCR", label: "스크래치", hint: "표면 선형 흠집·긁힘", key: "4" },
];

export function LabelingPage(): JSX.Element {
  const role = useAuthStore((s) => s.role);
  const editable = canEdit(role);
  const qc = useQueryClient();

  const { data: queue, isLoading } = useQuery({
    queryKey: ["label-queue"],
    queryFn: () => fetchLabelQueue(30),
  });
  const { data: progress } = useQuery({
    queryKey: ["label-progress"],
    queryFn: fetchLabelProgress,
  });

  const [cursor, setCursor] = useState(0);
  const [picked, setPicked] = useState<string[]>([]);
  const [border, setBorder] = useState(false);
  const [showSystem, setShowSystem] = useState(false);
  const [saved, setSaved] = useState(0);

  const current: LabelQueueItem | undefined = queue?.[cursor];
  const img = useAuthedImage(current?.inspection_id ?? null, "result");

  // 다음 장으로 넘어가면 선택을 초기화한다(직전 라벨이 묻어나면 오염된다).
  useEffect(() => {
    setPicked([]);
    setBorder(false);
    setShowSystem(false);
  }, [current?.inspection_id]);

  const mutation = useMutation({
    mutationFn: (body: { labels: string[]; border: boolean }) =>
      putLabel(current!.inspection_id, body),
    onSuccess: () => {
      setSaved((n) => n + 1);
      qc.invalidateQueries({ queryKey: ["label-progress"] });
      if (queue && cursor + 1 >= queue.length) {
        // 큐를 다 봤으면 새로 받아 온다(라벨된 것은 서버가 빼 준다).
        setCursor(0);
        qc.invalidateQueries({ queryKey: ["label-queue"] });
      } else {
        setCursor((c) => c + 1);
      }
    },
  });

  const toggle = useCallback((code: string) => {
    setPicked((p) => (p.includes(code) ? p.filter((x) => x !== code) : [...p, code]));
  }, []);

  const save = useCallback(() => {
    if (!current || !editable || mutation.isPending) return;
    mutation.mutate({ labels: picked, border });
  }, [current, editable, mutation, picked, border]);

  const skip = useCallback(() => {
    setCursor((c) => c + 1);
  }, []);

  // 단축키 — 검수자가 마우스를 오가지 않고 한 손으로 넘길 수 있어야 한다.
  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return;
      }
      const hit = CODES.find((c) => c.key === e.key);
      if (hit) {
        e.preventDefault();
        toggle(hit.code);
      } else if (e.key === "0") {
        e.preventDefault();
        setPicked([]); // 정상 = 라벨 없음
      } else if (e.key.toLowerCase() === "b") {
        e.preventDefault();
        setBorder((v) => !v);
      } else if (e.key === "Enter") {
        e.preventDefault();
        save();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        skip();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle, save, skip]);

  const remaining = useMemo(
    () => (queue ? Math.max(0, queue.length - cursor) : 0),
    [queue, cursor],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">정답 라벨링</h1>
        <span className="text-sm text-slate-400">
          이번 세션 {saved}건 저장 · 대기 {remaining}건
        </span>
        {!editable && (
          <span className="text-sm text-ng-fg">
            저장 권한이 없습니다(품질관리자 이상).
          </span>
        )}
      </div>

      {progress && <ProgressBar progress={progress} />}

      {isLoading && <div className="card p-4 text-sm">불러오는 중…</div>}

      {!isLoading && !current && (
        <div className="card p-6 text-center" data-testid="label-empty">
          <p className="font-semibold">라벨링할 이미지가 없습니다.</p>
          <p className="mt-1 text-sm text-slate-400">
            검사를 더 진행하거나, 이미 모두 라벨링을 마쳤습니다.
          </p>
        </div>
      )}

      {current && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[3fr_2fr]">
          {/* 이미지 */}
          <div className="card p-3" data-testid="label-image">
            <div className="mb-2 flex items-center justify-between text-sm">
              <span className="font-semibold">
                검사 #{current.inspection_id} · LOT {current.lot}
                {current.review_flag && (
                  <span className="ml-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
                    재확인 대상
                  </span>
                )}
              </span>
              <span className="text-slate-400">
                {fmtDateTime(current.inspected_at)}
              </span>
            </div>
            {img.loading && <div className="py-20 text-center text-sm">이미지 로딩…</div>}
            {img.url && (
              <img
                src={img.url}
                alt={`검사 ${current.inspection_id} 판정 이미지`}
                className="max-h-[55vh] w-full rounded object-contain"
              />
            )}
            {img.error && (
              <div className="py-20 text-center text-sm text-slate-400">
                이미지를 불러오지 못했습니다(보관기간 경과 가능).
              </div>
            )}
          </div>

          {/* 라벨 입력 */}
          <div className="space-y-3">
            <div className="card p-4">
              <p className="mb-2 text-sm font-semibold">
                이 제품의 실제 상태는? (여러 개 선택 가능)
              </p>
              <div className="grid grid-cols-2 gap-2">
                {CODES.map((c) => (
                  <button
                    key={c.code}
                    type="button"
                    title={c.hint}
                    onClick={() => toggle(c.code)}
                    aria-pressed={picked.includes(c.code)}
                    data-testid={`label-btn-${c.code}`}
                    className={`rounded-lg border-2 px-3 py-4 text-left transition ${
                      picked.includes(c.code)
                        ? "border-brand bg-brand/10 font-bold"
                        : "border-slate-200 hover:bg-slate-50"
                    }`}
                  >
                    <span className="text-base">{c.label}</span>
                    <span className="ml-1 text-xs text-slate-400">[{c.key}]</span>
                    <span className="block text-xs text-slate-400">{c.hint}</span>
                  </button>
                ))}
              </div>

              <button
                type="button"
                onClick={() => setPicked([])}
                aria-pressed={picked.length === 0}
                data-testid="label-btn-OK"
                className={`mt-2 w-full rounded-lg border-2 px-3 py-3 transition ${
                  picked.length === 0
                    ? "border-ok bg-ok-bg font-bold text-ok-fg"
                    : "border-slate-200 hover:bg-slate-50"
                }`}
              >
                정상 (문제 없음) <span className="text-xs text-slate-400">[0]</span>
              </button>

              <label className="mt-3 flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={border}
                  onChange={(e) => setBorder(e.target.checked)}
                  data-testid="label-border"
                />
                <span>
                  <b>애매함(경계)</b> [B] — 사람이 봐도 판정이 갈리는 것.
                  <span className="text-slate-400">
                    {" "}버리지 말고 표시해 주세요. 이게 정확도를 올립니다.
                  </span>
                </span>
              </label>
            </div>

            <div className="card p-4">
              <div className="flex gap-2">
                <button
                  type="button"
                  className="btn-primary flex-1 py-3"
                  disabled={!editable || mutation.isPending}
                  onClick={save}
                  data-testid="label-save"
                >
                  {mutation.isPending ? "저장 중…" : "저장하고 다음 [Enter]"}
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={skip}
                  data-testid="label-skip"
                >
                  건너뛰기 [→]
                </button>
              </div>
              {mutation.isError && (
                <p className="mt-2 text-sm text-ng-fg" role="alert">
                  저장 실패: {(mutation.error as Error)?.message}
                </p>
              )}

              {/* 시스템 판정은 접어 둔다 — 먼저 보면 사람이 거기 끌려간다. */}
              <button
                type="button"
                className="mt-3 text-xs text-slate-400 underline"
                onClick={() => setShowSystem((v) => !v)}
                data-testid="label-toggle-system"
              >
                {showSystem ? "시스템 판정 감추기" : "시스템 판정 보기(먼저 판단한 뒤에)"}
              </button>
              {showSystem && (
                <dl className="mt-2 text-sm" data-testid="label-system">
                  <div className="flex gap-2">
                    <dt className="text-slate-400">시스템 판정</dt>
                    <dd className="font-semibold">{current.final_verdict ?? "-"}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="text-slate-400">불량유형</dt>
                    <dd>{current.defect_codes.join(", ") || "-"}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="text-slate-400">측정 길이</dt>
                    <dd>{fmtNum(current.meas_length_mm, 2)} mm</dd>
                  </div>
                </dl>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** 클래스별 수집 진척 — 검수자가 "무엇이 부족한지" 보고 그것부터 모으게 한다. */
function ProgressBar({
  progress,
}: {
  progress: { labeled_total: number; unlabeled_total: number; border_count: number;
    by_class: Record<string, { count: number; target: number }> };
}): JSX.Element {
  return (
    <div className="card p-4" data-testid="label-progress">
      <div className="mb-2 flex flex-wrap gap-4 text-sm">
        <span>
          라벨 완료 <b className="tabular-nums">{progress.labeled_total}</b>
        </span>
        <span className="text-slate-400">
          남은 이미지 {progress.unlabeled_total}
        </span>
        <span className="text-slate-400">경계 샘플 {progress.border_count}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {Object.entries(progress.by_class).map(([code, v]) => {
          const pct = v.target > 0 ? Math.min(100, (v.count / v.target) * 100) : 0;
          return (
            <div key={code}>
              <div className="flex justify-between text-xs">
                <span className="font-medium">{code}</span>
                <span className="tabular-nums text-slate-400">
                  {v.count}/{v.target}
                </span>
              </div>
              <div className="mt-1 h-2 rounded bg-slate-200">
                <div
                  className={`h-2 rounded ${pct >= 100 ? "bg-ok" : "bg-brand"}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
