/**
 * NG 제품 재확인 입력 다이얼로그 (CLAUDE.md §5 M10, §7.4 PATCH /inspection/{id}/review).
 * 작업자가 수동 확인 결과(manual_verdict)를 입력 → PATCH /inspection/{id}/review.
 * 성공 시 store 의 해당 행 갱신(applyReview).
 *
 * 인증: 재확인은 operator+ 권한(JWT Bearer) 필요(쓰기 액션).
 * - 미인증이면 제출 시 로그인 모달을 띄우고, 로그인 성공 후 자동 재시도.
 * - 토큰 만료(401)도 동일하게 로그인 유도 후 재시도.
 * - 실시간 표시(WS)는 미인증이어도 동작하므로 다이얼로그 열람 자체는 막지 않는다.
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import type { InspectionResult, ReviewUpdate } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";
import { submitReview, ApiError } from "@/api/client";
import { useLiveStore } from "@/store/liveStore";
import { useAuthStore } from "@/store/authStore";
import { LoginModal } from "@/components/LoginModal";

export interface ReviewDialogProps {
  result: InspectionResult;
  onClose: () => void;
}

export function ReviewDialog({ result, onClose }: ReviewDialogProps) {
  const [choice, setChoice] = useState<Verdict | null>(null);
  const applyReview = useLiveStore((s) => s.applyReview);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const loginPromptOpen = useAuthStore((s) => s.loginPromptOpen);
  const openLoginPrompt = useAuthStore((s) => s.openLoginPrompt);

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
    onError: (err) => {
      // 토큰 만료/무효 → 401. 로그인 유도 후 재시도(onSuccess 콜백에서 mutate).
      if (err instanceof ApiError && err.status === 401) {
        openLoginPrompt();
      }
    },
  });

  const runMutation = () => {
    if (!choice) return;
    mutation.mutate({
      manual_verdict: choice,
      review_flag: false,
      operator: result.operator ?? undefined,
    });
  };

  const submit = () => {
    if (!choice) return;
    // 미인증이면 먼저 로그인 유도. 로그인 성공 시 onSuccess 에서 재시도.
    if (!isAuthenticated()) {
      openLoginPrompt();
      return;
    }
    runMutation();
  };

  return (
    <>
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

        {mutation.isError &&
          !(
            mutation.error instanceof ApiError &&
            mutation.error.status === 401
          ) && (
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

      {loginPromptOpen && (
        <LoginModal
          onSuccess={() => {
            // 로그인 성공 → 보류했던 재확인 저장을 자동 재시도.
            runMutation();
          }}
        />
      )}
    </>
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
