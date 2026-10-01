/**
 * 공차 밴드 게이지 — "얼마나 벗어났는가"를 읽지 않고 보게 한다.
 *
 * **왜 필요한가**: 전에는 숫자만 있었다. "편차 +19.75 / 기준 125.00" 을 보고
 * 작업자가 공차(±0.5)를 떠올려 암산해야 벗어난 정도를 알 수 있었다. 현장에서
 * 1.5초마다 제품이 지나가는데 암산을 시키면 안 된다.
 *
 * 공차 밴드 안에 마커를 찍으면 위치만으로 즉시 읽힌다 — 가운데면 여유, 끝에
 * 붙으면 아슬아슬, 밖이면 불량. 특히 **아슬아슬하게 통과한 제품**을 눈에 띄게
 * 한다. 숫자만 보면 +0.48 과 +0.05 가 비슷해 보이지만, 공정이 한쪽으로 쏠리고
 * 있다는 신호는 전자다.
 *
 * 색 규칙(ISA-101): 공차 안이면 무채색, 벗어나야 빨강. 밴드 자체는 늘 회색이다.
 */

export interface ToleranceGaugeProps {
  /** 편차(mm). 기준 대비 +/-. null 이면 측정 실패 → 게이지를 그리지 않는다. */
  deviationMm: number | null | undefined;
  /** 허용 공차 + 쪽(mm). */
  tolPlusMm: number | null | undefined;
  /** 허용 공차 − 쪽(mm). 보통 양수로 들어온다. */
  tolMinusMm: number | null | undefined;
}

/** 마커 위치를 밴드 기준 0~1 로. 범위 밖이면 0/1 에 붙인다(화면 밖으로 안 나감). */
export function markerRatio(
  dev: number,
  tolPlus: number,
  tolMinus: number,
): number {
  // 밴드 양옆에 공차폭의 절반씩 여유를 둬, 벗어난 제품도 "얼마나" 벗어났는지
  // 어느 정도 보이게 한다. 여유 밖은 끝에 붙어 "한참 벗어남" 으로 읽힌다.
  const minus = Math.abs(tolMinus) || 0;
  const plus = Math.abs(tolPlus) || 0;
  const span = minus + plus;
  if (span <= 0) return 0.5;
  const pad = span * 0.5;
  const lo = -minus - pad;
  const hi = plus + pad;
  return Math.min(1, Math.max(0, (dev - lo) / (hi - lo)));
}

/** 공차 안쪽인지. 경계값(정확히 공차)은 통과로 본다. */
export function withinTolerance(
  dev: number,
  tolPlus: number,
  tolMinus: number,
): boolean {
  return dev <= Math.abs(tolPlus) && dev >= -Math.abs(tolMinus);
}

export function ToleranceGauge({
  deviationMm,
  tolPlusMm,
  tolMinusMm,
}: ToleranceGaugeProps) {
  if (
    deviationMm === null ||
    deviationMm === undefined ||
    tolPlusMm === null ||
    tolPlusMm === undefined ||
    tolMinusMm === null ||
    tolMinusMm === undefined
  ) {
    return null;
  }

  const plus = Math.abs(tolPlusMm);
  const minus = Math.abs(tolMinusMm);
  const ok = withinTolerance(deviationMm, plus, minus);
  const ratio = markerRatio(deviationMm, plus, minus);

  // 공차 밴드(허용 구간)가 전체 폭에서 차지하는 비율 — markerRatio 와 같은 척도.
  const left = markerRatio(-minus, plus, minus);
  const right = markerRatio(plus, plus, minus);

  return (
    <div data-testid="tolerance-gauge" data-within={ok ? "yes" : "no"}>
      <div className="mb-0.5 flex items-baseline justify-between text-hmi-cap font-semibold text-gray-500">
        <span>공차 −{minus.toFixed(2)}</span>
        <span>허용 범위</span>
        <span>+{plus.toFixed(2)}</span>
      </div>
      <div className="relative h-5 rounded-full bg-gray-200">
        {/* 허용 구간 — 늘 무채색. 여기가 '안전지대'라는 배경 정보일 뿐이다. */}
        <div
          className="absolute inset-y-0 rounded-full bg-gray-400/50"
          style={{ left: `${left * 100}%`, right: `${(1 - right) * 100}%` }}
        />
        {/* 기준선(편차 0) */}
        <div
          className="absolute inset-y-0 w-0.5 bg-gray-600"
          style={{ left: `${markerRatio(0, plus, minus) * 100}%` }}
        />
        {/* 현재 측정 마커 — 공차를 벗어났을 때만 색을 쓴다. */}
        <div
          className={`absolute top-1/2 h-6 w-2 -translate-x-1/2 -translate-y-1/2 rounded ${
            ok ? "bg-gray-900" : "bg-ng"
          }`}
          style={{ left: `${ratio * 100}%` }}
          data-testid="tolerance-marker"
        />
      </div>
    </div>
  );
}
