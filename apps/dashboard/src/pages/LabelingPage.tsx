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
 *
 * **다발 사진(2026-10-11)**: 다발 한 장은 튜브마다 한 행이라 같은 사진이 N번
 * 나온다. 어느 튜브를 판정하는지 모르면 라벨이 엉뚱한 튜브에 붙는다. 그래서
 * "튜브 #n / N" 을 크게 적고, 그 튜브 칸만 밝게 남기고 나머지를 흐린다.
 * 번호는 판정 이미지에 찍힌 '#n' 과 같다.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchLabelExport,
  fetchLabelQueue,
  fetchLabelProgress,
  putLabel,
  type LabelQueueItem,
} from "@/api/endpoints";
import { triggerBlobDownload } from "@/lib/download";
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

  // 정답 내보내기 — 학습 담당자가 터미널 없이 받아 labeling.cli dataset 에 넣는다.
  const exporter = useMutation({
    mutationFn: () => fetchLabelExport(),
    onSuccess: (data) => {
      const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      triggerBlobDownload(blob, `labels_${stamp}.json`);
    },
  });

  const bundle = (current?.frame_total ?? 1) > 1;

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
        {editable && (
          <div className="ml-auto flex items-center gap-2">
            {exporter.isSuccess && (
              <span className="text-sm text-slate-400" data-testid="label-export-done">
                {exporter.data.count}건 내보냄
              </span>
            )}
            {exporter.isError && (
              <span className="text-sm text-ng-fg" role="alert">
                내보내기 실패: {(exporter.error as Error)?.message}
              </span>
            )}
            <button
              type="button"
              className="btn-ghost"
              disabled={exporter.isPending}
              onClick={() => exporter.mutate()}
              title="라벨 전체를 labels.json 으로 받는다 — 학습 PC 에서 labeling.cli dataset 에 넣는다"
              data-testid="label-export"
            >
              {exporter.isPending ? "내보내는 중…" : "정답 내보내기 (JSON)"}
            </button>
          </div>
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

      {/* 이미지에 폭을 더 준다(3:2 → 5:3). 라벨링 도구에서 이미지 크기는 곧
          판정 정확도이자 속도인데, 가로로 긴 제품 사진이라 세로로 늘려도 여백만
          늘 뿐 커지지 않는다. 폭을 늘리는 것이 실제로 이미지를 키운다.
          items-start: 높이를 억지로 맞추지 않는다. 전에는 입력 패널 높이에
          맞춰 늘어난 카드 안에서 이미지가 위쪽에만 박혀 3분의 2가 빈 흰색이었다.
          조작은 숫자키로 하므로 두 열의 높이가 같을 이유가 없다. */}
      {current && (
        <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[5fr_3fr]">
          {/* 이미지 */}
          <div className="card flex flex-col p-3" data-testid="label-image">
            <div className="mb-2 flex items-center justify-between text-sm">
              <span className="font-semibold">
                검사 #{current.inspection_id} · LOT {current.lot}
                {current.cam_id && (
                  <span className="ml-1 font-normal text-slate-400">· {current.cam_id}</span>
                )}
                {current.review_flag && (
                  <span className="ml-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
                    재확인 대상
                  </span>
                )}
                {current.training && (
                  <span
                    className="ml-2 rounded bg-sky-100 px-2 py-0.5 text-xs text-sky-800"
                    title="수집용 LOT — KPI·MES 에서 빠진다"
                    data-testid="label-training"
                  >
                    수집용
                  </span>
                )}
              </span>
              <span className="text-slate-400">
                {fmtDateTime(current.inspected_at)}
              </span>
            </div>
            {img.loading && (
              <div className="flex flex-1 items-center justify-center text-sm text-slate-400">
                이미지 로딩…
              </div>
            )}
            {bundle && (
              <p
                className="mb-2 rounded bg-brand/10 px-3 py-2 text-base"
                data-testid="label-tube"
              >
                <b className="text-lg">튜브 #{(current.tube_index ?? 0) + 1}</b>
                <span className="text-slate-500"> / {current.frame_total}개 다발</span>
                <span className="ml-2 text-sm text-slate-500">
                  — 밝게 남긴 칸의 튜브 하나만 판정하세요.
                </span>
              </p>
            )}
            {img.url && (
              <TubeImage
                key={current.inspection_id}
                src={img.url}
                alt={`검사 ${current.inspection_id} 판정 이미지`}
                bbox={bundle ? current.tube_bbox ?? null : null}
              />
            )}
            {img.error && (
              <div className="flex flex-1 items-center justify-center text-center text-sm text-slate-400">
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

/**
 * 판정 이미지 + 이 튜브 강조. bbox 는 원본 프레임 픽셀이고 판정 이미지는 원본과
 * 크기가 같으므로(오버레이는 원본 사본에 그린다) naturalWidth/Height 비율로 놓는다.
 * 나머지는 box-shadow 로 흐린다 — 옆 튜브와 색을 비교할 수 있게 완전히 가리진 않는다.
 */
function TubeImage({
  src,
  alt,
  bbox,
}: {
  src: string;
  alt: string;
  bbox: number[] | null;
}): JSX.Element {
  const [nat, setNat] = useState<{ w: number; h: number } | null>(null);
  const box =
    bbox && bbox.length === 4 && nat && nat.w > 0 && nat.h > 0
      ? {
          left: `${(Math.max(0, bbox[0]) / nat.w) * 100}%`,
          top: `${(Math.max(0, bbox[1]) / nat.h) * 100}%`,
          width: `${((bbox[2] - bbox[0]) / nat.w) * 100}%`,
          height: `${((bbox[3] - bbox[1]) / nat.h) * 100}%`,
        }
      : null;
  return (
    <div className="relative overflow-hidden rounded">
      <img
        src={src}
        alt={alt}
        className="block w-full object-contain"
        onLoad={(e) =>
          setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })
        }
      />
      {box && (
        <div
          className="pointer-events-none absolute rounded-sm outline outline-4 outline-sky-400"
          style={{ ...box, boxShadow: "0 0 0 9999px rgba(15, 23, 42, 0.55)" }}
          data-testid="label-tube-box"
        />
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
              {/* "무엇이 부족한지" 알려주는 장치인데 2px 막대에 깨알 숫자라
                  읽히지 않았다. 검수자가 다음에 무엇을 모을지 정하는 근거다. */}
              <div className="flex justify-between text-sm">
                <span className="font-semibold">{code}</span>
                <span className="tabular-nums text-slate-500">
                  {v.count}/{v.target}
                </span>
              </div>
              <div className="mt-1 h-3 rounded bg-slate-200">
                <div
                  className={`h-3 rounded ${pct >= 100 ? "bg-ok" : "bg-brand"}`}
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
