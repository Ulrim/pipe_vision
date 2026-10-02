/**
 * 현장용 숫자 키패드 (오더 교체 입력).
 *
 * **왜 OS 키보드를 안 쓰는가.** 현장 화면은 장갑 낀 손으로 누른다. 업계
 * 가이드는 장갑 착용 시 터치 타깃을 **최소 12~15mm, 권장 20mm** 로 본다
 * (절단방지 장갑이 손끝 지름을 3~5mm 키운다). 15.6" 1920x1080 은 약 141 PPI
 * 라 1mm ≈ 5.6px → 20mm ≈ 112px. 운영체제 키보드는 이보다 훨씬 작고, 화면을
 * 가려 입력 중인 값과 합격 범위를 동시에 못 본다.
 *
 * 증감 버튼을 함께 둔다. 공차처럼 0.1 단위로 미세 조정하는 값은 숫자를 다시
 * 치는 것보다 ± 가 빠르고 오타가 없다(산업 HMI의 보편적 패턴).
 */

export interface NumPadProps {
  /** 현재 입력 문자열(소수점 포함, 빈 문자열 허용). */
  value: string;
  onChange: (next: string) => void;
  /** ± 버튼 증감폭. 0 이면 증감 버튼을 숨긴다. */
  step?: number;
  /** 소수점 자리수 제한. */
  maxDecimals?: number;
}

const KEYS = ["7", "8", "9", "4", "5", "6", "1", "2", "3", "0", "."];

/** 한 키의 최소 크기 — 장갑 20mm 기준(141 PPI 에서 112px). */
const KEY_CLASS =
  "min-h-[112px] rounded-2xl border-4 border-gray-300 bg-white text-hmi-xl " +
  "font-black text-gray-900 active:scale-95 active:bg-gray-100";

export function appendKey(value: string, key: string, maxDecimals = 3): string {
  if (key === ".") {
    // 소수점은 하나만. 맨 앞이면 "0." 으로 시작한다(".5" 는 혼동을 준다).
    if (value.includes(".")) return value;
    return value === "" ? "0." : `${value}.`;
  }
  const dot = value.indexOf(".");
  if (dot >= 0 && value.length - dot - 1 >= maxDecimals) return value;
  // 선행 0 억제: "0" 뒤에 숫자를 치면 치환한다("05" 방지).
  if (value === "0") return key;
  return value + key;
}

export function stepValue(value: string, step: number, maxDecimals = 3): string {
  const n = Number(value === "" ? "0" : value);
  if (!Number.isFinite(n)) return value;
  const next = Math.max(0, n + step);
  return String(Number(next.toFixed(maxDecimals)));
}

export function NumPad({ value, onChange, step = 0, maxDecimals = 3 }: NumPadProps) {
  return (
    <div className="flex flex-col gap-3" data-testid="numpad">
      {step > 0 && (
        <div className="grid grid-cols-2 gap-3">
          <button
            type="button"
            className={KEY_CLASS}
            data-testid="numpad-dec"
            onClick={() => onChange(stepValue(value, -step, maxDecimals))}
            aria-label={`${step} 줄이기`}
          >
            − {step}
          </button>
          <button
            type="button"
            className={KEY_CLASS}
            data-testid="numpad-inc"
            onClick={() => onChange(stepValue(value, step, maxDecimals))}
            aria-label={`${step} 늘리기`}
          >
            + {step}
          </button>
        </div>
      )}
      <div className="grid grid-cols-3 gap-3">
        {KEYS.map((k) => (
          <button
            key={k}
            type="button"
            className={KEY_CLASS}
            data-testid={`numpad-key-${k}`}
            onClick={() => onChange(appendKey(value, k, maxDecimals))}
          >
            {k}
          </button>
        ))}
        <button
          type="button"
          className={KEY_CLASS}
          data-testid="numpad-back"
          aria-label="한 글자 지우기"
          onClick={() => onChange(value.slice(0, -1))}
        >
          ←
        </button>
      </div>
      <button
        type="button"
        className={`${KEY_CLASS} border-gray-400`}
        data-testid="numpad-clear"
        onClick={() => onChange("")}
      >
        전체 지우기
      </button>
    </div>
  );
}
