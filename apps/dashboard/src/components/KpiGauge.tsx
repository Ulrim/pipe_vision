import type { KpiGaugeSpec } from "@/lib/kpi";

const STATUS_COLOR: Record<KpiGaugeSpec["status"], string> = {
  pass: "#16a34a",
  warn: "#d97706",
  fail: "#dc2626",
};
const STATUS_LABEL: Record<KpiGaugeSpec["status"], string> = {
  pass: "달성",
  warn: "근접",
  fail: "미달",
};

/** 낮을수록 좋은 지표에서 목표선이 트랙의 어디에 오는가(0~1). */
const TARGET_ANCHOR_LOWER = 0.6;

/**
 * 막대가 목표선 위치에 비해 어디까지 차오르는가(0~1), 그리고 척도를 넘었는가.
 *
 * - 낮을수록 좋은 지표(불량률): 목표를 트랙의 60% 지점에 **고정**한다. 그래야
 *   카드마다 목표선 위치가 같아 "목표를 넘었나" 를 위치만으로 읽을 수 있다.
 *   목표의 1.67배를 넘으면 막대가 트랙을 다 채우고 초과 배수를 글로 적는다.
 * - 높을수록 좋은 지표(자동검사율): 목표가 트랙 끝(100%)이다. 가득 차면 달성.
 */
export function barFill(
  value: number,
  target: number,
  direction: KpiGaugeSpec["direction"],
): { ratio: number; overflow: boolean; multiple: number | null } {
  if (!Number.isFinite(value) || !Number.isFinite(target) || target <= 0) {
    return { ratio: 0, overflow: false, multiple: null };
  }
  if (direction === "higher") {
    return {
      ratio: Math.min(1, Math.max(0, value / target)),
      overflow: false,
      multiple: null,
    };
  }
  const raw = (value / target) * TARGET_ANCHOR_LOWER;
  if (raw > 1) {
    return { ratio: 1, overflow: true, multiple: value / target };
  }
  return { ratio: Math.max(0, raw), overflow: false, multiple: null };
}

/**
 * KPI 불릿 차트 (CLAUDE.md §1.1 목표 대비 현재값).
 *
 * **반원 게이지에서 바꿨다.** 두 가지 문제가 있었다.
 *
 * 1. 반원은 0~100 같은 **유계 척도**를 암시한다. 공정불량률 681,818ppm 을
 *    목표 600ppm 에 대고 그리면 호가 그냥 꽉 차서 "얼마나 초과했는지" 가
 *    사라진다 — 목표의 2배나 1,000배나 똑같이 보인다.
 * 2. 큰 값에서 숫자가 호와 **겹쳐 렌더링**됐다(음수 마진으로 끌어올린 탓).
 *    `681,818.18ppm` 같은 긴 값이 게이지를 뚫고 나왔다.
 *
 * 불릿 차트는 목표선과 실적 막대를 같은 축에 놓아 초과분을 정직하게 보여주고,
 * 숫자가 차트와 겹칠 구조적 여지가 없다. 색 단독 의존 금지 원칙대로 상태
 * 라벨(달성/근접/미달)을 함께 적는다.
 */
export function KpiGauge({ spec }: { spec: KpiGaugeSpec }): JSX.Element {
  const color = STATUS_COLOR[spec.status];
  const { ratio, overflow, multiple } = barFill(
    spec.value,
    spec.target,
    spec.direction,
  );
  const targetAt = spec.direction === "lower" ? TARGET_ANCHOR_LOWER : 1;

  return (
    <div
      className="card flex flex-col gap-2 p-4"
      data-testid={`kpi-${spec.key}`}
      data-status={spec.status}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-slate-600">{spec.label}</span>
        <span
          className="flex-none rounded px-1.5 py-0.5 text-xs font-semibold text-white"
          style={{ backgroundColor: color }}
        >
          {STATUS_LABEL[spec.status]}
        </span>
      </div>

      {/* 숫자는 차트와 같은 줄에 겹치지 않는다 — 겹침이 구조적으로 불가능하다. */}
      <div
        className="truncate text-2xl font-bold tabular-nums"
        style={{ color }}
        title={`${spec.value}${spec.unit}`}
      >
        {formatValue(spec.value)}
        <span className="ml-0.5 text-sm font-normal text-slate-400">
          {spec.unit}
        </span>
      </div>

      <div
        className="relative h-3 rounded-full bg-slate-200"
        role="img"
        aria-label={`${spec.label} ${spec.value}${spec.unit}, 목표 ${spec.target}${spec.unit}, ${STATUS_LABEL[spec.status]}`}
      >
        <div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{ width: `${ratio * 100}%`, backgroundColor: color }}
          data-testid={`kpi-bar-${spec.key}`}
        />
        {/* 목표선 — 카드마다 같은 자리라 위치만으로 넘었는지 읽힌다. */}
        <div
          className="absolute -top-0.5 bottom-[-2px] w-0.5 bg-slate-700"
          style={{ left: `${targetAt * 100}%` }}
          data-testid={`kpi-target-${spec.key}`}
        />
      </div>

      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="text-slate-400">
          목표 {spec.direction === "lower" ? "≤" : "="}{" "}
          {formatValue(spec.target)}
          {spec.unit}
        </span>
        {overflow && multiple !== null && (
          <span
            className="font-semibold"
            style={{ color }}
            data-testid={`kpi-overflow-${spec.key}`}
          >
            목표의 {formatValue(Math.round(multiple))}배
          </span>
        )}
      </div>
    </div>
  );
}

/** 큰 값은 축약한다 — 카드 폭을 넘겨 레이아웃을 깨뜨리지 않기 위함. */
export function formatValue(v: number): string {
  if (!Number.isFinite(v)) return "-";
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `${Math.round(v / 1_000).toLocaleString()}k`;
  if (Number.isInteger(v)) return v.toLocaleString();
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
