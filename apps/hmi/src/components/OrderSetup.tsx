/**
 * 오더 교체 — 기준길이·공차·개수를 현장에서 직접 입력 (M13).
 *
 * **왜 HMI 에 두는가.** 제품 길이가 주문마다 바뀌는 공정인데(도입기업 확인),
 * 종전에는 이 값들이 관리자 대시보드에서 품질관리자 권한으로만 바뀌었다.
 * 오더가 바뀔 때마다 다른 기기에서 다른 계정으로 로그인해야 하면 라인이 선다.
 *
 * **대신 바꿀 수 있는 것을 좁혔다.** 서버의 /spec 엔드포인트는 기준길이·공차·
 * 개수만 받는다. px→mm 보정계수나 표면 임계값은 애초에 전송되지 않으므로,
 * 라인에서 급히 만지다 엉뚱한 값을 흔드는 사고가 구조적으로 불가능하다.
 *
 * 설계(산업 HMI 관행 참고):
 * - 입력은 **전용 숫자 키패드**. 장갑 기준 20mm(≈112px) 타깃.
 * - 입력하는 동안 **합격 범위(min~max)를 실시간**으로 보여준다. 숫자만 보고
 *   "이게 맞나" 를 판단하게 하면 안 된다.
 * - 저장 전 **이전값 → 새값 확인 단계**. 판정 기준을 바꾸는 일이라 한 번 더 묻는다.
 * - 서버와 **같은 규칙으로 미리 막는다**(공차 양쪽 0, 공차 ≥ 길이 등).
 */
import { useEffect, useMemo, useState } from "react";
import type { ItemMaster } from "@aivis/shared-types";
import { MAX_EXPECTED_COUNT } from "@aivis/shared-types";
import { InspectionStage } from "@aivis/shared-types";
import { setActiveStage, updateItemSpec } from "@/api/client";
import { STAGE_LABEL } from "@/lib/stage";
import { NumPad } from "./NumPad";

/** 모드 버튼 순서 = 공정 순서(길이 → 표면 → 개수). */
const STAGES: InspectionStage[] = [
  InspectionStage.CUT_LENGTH,
  InspectionStage.POST_WASH_SURFACE,
  InspectionStage.CRATE_COUNT,
];

type FieldKey = "len" | "plus" | "minus" | "count";

const FIELD_META: Record<FieldKey, { label: string; unit: string; step: number; dec: number }> = {
  len: { label: "기준 길이", unit: "mm", step: 1, dec: 3 },
  plus: { label: "공차 +", unit: "mm", step: 0.1, dec: 3 },
  minus: { label: "공차 −", unit: "mm", step: 0.1, dec: 3 },
  count: { label: "한 판 개수", unit: "개", step: 1, dec: 0 },
};

export interface OrderSetupProps {
  item: ItemMaster;
  onClose: () => void;
  onSaved?: (next: ItemMaster) => void;
  /** 지금 돌고 있는 검사 모드(하트비트). 모르면 null — 버튼은 모두 비활성 표시. */
  currentStage?: string | null;
  /** 모드 전환 성공 콜백(테스트/상위 표시용). */
  onStageChanged?: (stage: InspectionStage) => void;
}

/** 서버(ItemSpecUpdate)와 같은 규칙. 통과하면 저장 버튼이 열린다. */
export function validateSpec(
  len: number,
  plus: number,
  minus: number,
  count: number,
): string | null {
  if (!Number.isFinite(len) || len <= 0) return "기준 길이를 입력하세요.";
  if (!Number.isFinite(plus) || !Number.isFinite(minus) || plus < 0 || minus < 0)
    return "공차는 0 이상이어야 합니다.";
  if (plus <= 0 && minus <= 0) return "공차가 양쪽 모두 0 이면 전부 불량이 됩니다.";
  if (plus >= len || minus >= len) return "공차가 기준 길이보다 큽니다. 자릿수를 확인하세요.";
  if (!Number.isInteger(count) || count < 1 || count > MAX_EXPECTED_COUNT)
    return `한 판 개수는 1 ~ ${MAX_EXPECTED_COUNT} 사이여야 합니다.`;
  return null;
}

export function OrderSetup({
  item,
  onClose,
  onSaved,
  currentStage,
  onStageChanged,
}: OrderSetupProps) {
  // 모드 전환은 저장 2단계와 별개의 즉시 동작이다 — 카메라를 다른 자리로
  // 돌려 세운 작업자가 바로 모드를 맞춰야 하기 때문. 대신 현재 모드를 크게
  // 보여 주고, 눌린 뒤 워커가 15초 내 따라온다는 안내를 붙인다.
  const [stageBusy, setStageBusy] = useState<InspectionStage | null>(null);
  const [stageDone, setStageDone] = useState<InspectionStage | null>(null);
  const [stageError, setStageError] = useState<string | null>(null);
  async function switchStage(next: InspectionStage) {
    setStageBusy(next);
    setStageError(null);
    try {
      await setActiveStage(item.item_code, next);
      setStageDone(next);
      onStageChanged?.(next);
    } catch (e) {
      setStageError(e instanceof Error ? e.message : "모드 전환에 실패했습니다.");
    } finally {
      setStageBusy(null);
    }
  }
  const shownStage = stageDone ?? currentStage ?? null;

  const [vals, setVals] = useState<Record<FieldKey, string>>({
    len: String(item.ref_length_mm ?? ""),
    plus: String(item.tol_plus_mm ?? ""),
    minus: String(item.tol_minus_mm ?? ""),
    count: String(item.expected_count ?? 1),
  });
  const [active, setActive] = useState<FieldKey>("len");
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const num = (k: FieldKey) => Number(vals[k] === "" ? NaN : vals[k]);
  const len = num("len");
  const plus = num("plus");
  const minus = num("minus");
  const count = num("count");

  const invalid = useMemo(
    () => validateSpec(len, plus, minus, count),
    [len, plus, minus, count],
  );
  const changed =
    len !== item.ref_length_mm ||
    plus !== item.tol_plus_mm ||
    minus !== item.tol_minus_mm ||
    count !== (item.expected_count ?? 1);

  useEffect(() => {
    if (invalid) setConfirming(false);
  }, [invalid]);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const next = await updateItemSpec(item.item_code, {
        ref_length_mm: len,
        tol_plus_mm: plus,
        tol_minus_mm: minus,
        expected_count: count,
      });
      onSaved?.(next);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장에 실패했습니다.");
      setConfirming(false);
    } finally {
      setSaving(false);
    }
  }

  const meta = FIELD_META[active];

  return (
    <div
      className="fixed inset-0 z-40 flex flex-col bg-white p-4"
      role="dialog"
      aria-modal="true"
      aria-label="오더 설정"
      data-testid="order-setup"
    >
      <header className="flex flex-none items-baseline justify-between pb-3">
        <div className="flex items-baseline gap-4">
          <h1 className="text-hmi-xl font-black text-gray-900">오더 설정</h1>
          <span className="text-hmi-body font-bold text-gray-600">
            품목 {item.item_code}
          </span>
        </div>
        <button
          type="button"
          className="min-h-[96px] rounded-2xl border-4 border-gray-400 px-10 text-hmi-lg font-black text-gray-700 active:scale-95"
          onClick={onClose}
          data-testid="order-setup-close"
        >
          닫기
        </button>
      </header>

      <div className="flex min-h-0 flex-1 gap-4">
        {/* 좌: 값 + 합격 범위 */}
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          {/* 검사 모드 — 한 모드는 한 질문만 한다(길이/표면/개수). 현재 모드를
              눌러 둔 상태로 보여 "지금 뭘 보는지" 를 먼저 확인하게 한다. */}
          <div
            className="rounded-2xl border-4 border-gray-300 bg-white p-3"
            data-testid="stage-picker"
            data-current={shownStage ?? ""}
          >
            <div className="mb-2 flex items-baseline justify-between">
              <span className="text-hmi-body font-bold text-gray-600">검사 모드</span>
              <span className="text-hmi-cap font-semibold text-gray-500">
                {stageDone ? "저장됨 — 워커가 15초 내 전환" : "누르면 바로 바뀝니다"}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {STAGES.map((st) => {
                const on = shownStage === st;
                return (
                  <button
                    key={st}
                    type="button"
                    disabled={stageBusy !== null}
                    onClick={() => switchStage(st)}
                    data-testid={`stage-${st}`}
                    data-on={on ? "yes" : "no"}
                    className={`min-h-[96px] rounded-2xl border-4 text-hmi-body font-black active:scale-95 disabled:opacity-60 ${
                      on
                        ? "border-gray-900 bg-gray-900 text-white"
                        : "border-gray-300 bg-white text-gray-800"
                    }`}
                  >
                    {on ? "● " : ""}
                    {STAGE_LABEL[st]}
                  </button>
                );
              })}
            </div>
            {stageError && (
              <p className="mt-2 text-hmi-cap font-bold text-ng-fg" data-testid="stage-error">
                {stageError}
              </p>
            )}
          </div>

          {(Object.keys(FIELD_META) as FieldKey[]).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setActive(k)}
              data-testid={`spec-field-${k}`}
              data-active={active === k ? "yes" : "no"}
              className={`flex min-h-[96px] items-center justify-between rounded-2xl border-4 px-5 text-left ${
                active === k ? "border-gray-900 bg-gray-100" : "border-gray-300 bg-white"
              }`}
            >
              <span className="text-hmi-body font-bold text-gray-600">
                {FIELD_META[k].label}
              </span>
              <span className="text-hmi-lg font-black tabular-nums text-gray-900">
                {vals[k] === "" ? "—" : vals[k]}
                <span className="ml-2 text-hmi-body font-bold text-gray-500">
                  {FIELD_META[k].unit}
                </span>
              </span>
            </button>
          ))}

          {/* 합격 범위 — 숫자만 보고 판단하게 하지 않는다. */}
          <div
            className="rounded-2xl border-4 border-gray-300 bg-white p-4"
            data-testid="pass-band"
          >
            <div className="text-hmi-body font-bold text-gray-600">합격 범위</div>
            <div className="mt-1 text-hmi-lg font-black tabular-nums text-gray-900">
              {invalid ? (
                <span className="text-ng-fg">—</span>
              ) : (
                <>
                  {(len - minus).toFixed(2)} ~ {(len + plus).toFixed(2)} mm
                </>
              )}
            </div>
            <div className="mt-1 text-hmi-cap font-semibold text-gray-500">
              폭 {invalid ? "—" : (plus + minus).toFixed(2)} mm
            </div>
          </div>

          {invalid && (
            <p className="text-hmi-body font-bold text-ng-fg" data-testid="spec-error">
              {invalid}
            </p>
          )}
          {error && (
            <p className="text-hmi-body font-bold text-ng-fg" data-testid="save-error">
              {error}
            </p>
          )}
        </div>

        {/* 우: 키패드 */}
        <div className="flex w-[520px] flex-none flex-col gap-3">
          <div className="text-hmi-body font-bold text-gray-700">
            입력 중: {meta.label} ({meta.unit})
          </div>
          <NumPad
            value={vals[active]}
            onChange={(next) => setVals((v) => ({ ...v, [active]: next }))}
            step={meta.step}
            maxDecimals={meta.dec}
          />
        </div>
      </div>

      {/* 하단: 확인 → 저장 2단계 */}
      <footer className="flex flex-none items-center gap-4 pt-3">
        {!confirming ? (
          <button
            type="button"
            disabled={Boolean(invalid) || !changed}
            onClick={() => setConfirming(true)}
            data-testid="spec-review"
            className="min-h-[112px] flex-1 rounded-2xl border-4 border-gray-900 bg-gray-900 text-hmi-lg font-black text-white disabled:border-gray-300 disabled:bg-gray-200 disabled:text-gray-400"
          >
            {changed ? "변경 내용 확인" : "변경 없음"}
          </button>
        ) : (
          <>
            <div
              className="flex-1 rounded-2xl border-4 border-gray-900 bg-white p-3 text-hmi-body font-bold text-gray-900"
              data-testid="spec-diff"
            >
              길이 {item.ref_length_mm} → {len} mm · 공차 +{item.tol_plus_mm}/−
              {item.tol_minus_mm} → +{plus}/−{minus} · 개수{" "}
              {item.expected_count ?? 1} → {count}
            </div>
            <button
              type="button"
              disabled={saving}
              onClick={save}
              data-testid="spec-save"
              className="min-h-[112px] w-[320px] rounded-2xl border-4 border-ok bg-ok text-hmi-lg font-black text-white disabled:opacity-60"
            >
              {saving ? "저장 중…" : "이대로 저장"}
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              data-testid="spec-cancel"
              className="min-h-[112px] w-[220px] rounded-2xl border-4 border-gray-400 text-hmi-lg font-black text-gray-700"
            >
              다시 입력
            </button>
          </>
        )}
      </footer>
    </div>
  );
}
