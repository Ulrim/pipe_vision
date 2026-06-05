/**
 * NG 제품 재확인 입력 다이얼로그 (CLAUDE.md §5 M10).
 * 작업자가 수동 확인 결과(manual_verdict)를 입력 → PATCH /inspection/{id}/review.
 * 성공 시 store 의 해당 행 갱신(applyReview).
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import type { InspectionResult, ReviewUpdate } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";
import { submitReview, ApiError } from "@/api/client";
import { useLiveStore } from "@/store/liveStore";

export interface ReviewDialogProps {
  result: InspectionResult;
  onClose: () => void;
}

export function ReviewDialog({ result, onClose }: ReviewDialogProps) {
  const [choice, setChoice] = useState<Verdict | null>(null);
  const applyReview = useLiveStore((s) => s.applyReview);

  const mutation = useMutation({
    mutationFn: (body: ReviewUpdate) => {
      if (result.id == null) {
        return Promise.reject(new ApiError(0, "검사 ID 없음 — 재확인 불가"));
      }
      return submitReview(result.id, body);
    },
    onSuccess: (updated) => {
      applyReview(updated);
      onClose();
    },
  });

  const submit = () => {
    if (!choice) return;
    mutation.mutate({
      manual_verdict: choice,
      review_flag: false,
      operator: result.operator ?? undefined,
    });
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="NG 제품 재확인"
      data-testid="review-dialog"
    >
      <div className="w-full max-w-xl rounded-2xl bg-white p-6 shadow-2xl">
        <h2 className="text-hmi-lg font-black">재확인 입력</h2>
        <p className="mt-1 text-hmi text-gray-600">
          {result.item_code} · LOT {result.lot}
        </p>

        <fieldset className="mt-5 grid grid-cols-2 gap-4">
          <legend className="sr-only">작업자 판정</legend>
          <ChoiceButton
            label="실제 양품 (OK)"
            active={choice === Verdict.OK}
            tone="ok"
            onClick={() => setChoice(Verdict.OK)}
            testid="review-ok"
          />
          <ChoiceButton
            label="실제 불량 (NG)"
            active={choice === Verdict.NG}
            tone="ng"
            onClick={() => setChoice(Verdict.NG)}
            testid="review-ng"
          />
        </fieldset>

        {mutation.isError && (
          <p className="mt-3 font-semibold text-ng-fg" role="alert">
            저장 실패:{" "}
            {(mutation.error as ApiError)?.message ?? "알 수 없는 오류"}
          </p>
        )}

        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border-2 border-gray-300 px-6 py-3 text-hmi font-bold text-gray-700 active:scale-95"
            data-testid="review-cancel"
          >
            취소
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!choice || mutation.isPending}
            className="rounded-xl bg-blue-600 px-8 py-3 text-hmi font-bold text-white disabled:opacity-40 active:scale-95"
            data-testid="review-submit"
          >
            {mutation.isPending ? "저장 중…" : "저장"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ChoiceButton({
  label,
  active,
  tone,
  onClick,
  testid,
}: {
  label: string;
  active: boolean;
  tone: "ok" | "ng";
  onClick: () => void;
  testid: string;
}) {
  const base =
    tone === "ok"
      ? active
        ? "bg-ok text-white border-ok"
        : "bg-ok-bg text-ok-fg border-ok"
      : active
        ? "bg-ng text-white border-ng"
        : "bg-ng-bg text-ng-fg border-ng";
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      data-testid={testid}
      className={`rounded-xl border-2 px-4 py-6 text-hmi font-bold active:scale-95 ${base}`}
    >
      {label}
    </button>
  );
}
