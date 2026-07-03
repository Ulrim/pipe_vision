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

/**
 * KPI 반원 게이지 (CLAUDE.md §1.1 목표 대비 현재값).
 * SVG 반원 + 채움 비율(ratio). 색상 단독 의존 금지 → 상태 라벨 병기.
 */
export function KpiGauge({ spec }: { spec: KpiGaugeSpec }): JSX.Element {
  const r = 52;
  const cx = 70;
  const cy = 70;
  const circ = Math.PI * r; // 반원 길이
  const dash = `${circ * spec.ratio} ${circ}`;
  const color = STATUS_COLOR[spec.status];

  return (
    <div
      className="card flex flex-col items-center p-4"
      data-testid={`kpi-${spec.key}`}
      data-status={spec.status}
    >
      <div className="mb-1 text-sm font-medium text-slate-600">{spec.label}</div>
      <svg width={140} height={84} viewBox="0 0 140 84" role="img"
        aria-label={`${spec.label} ${spec.value}${spec.unit}, 목표 ${spec.target}${spec.unit}, ${STATUS_LABEL[spec.status]}`}>
        <path
          d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
          fill="none"
          stroke="#e2e8f0"
          strokeWidth={12}
          strokeLinecap="round"
        />
        <path
          d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
          fill="none"
          stroke={color}
          strokeWidth={12}
          strokeLinecap="round"
          strokeDasharray={dash}
          data-testid={`kpi-arc-${spec.key}`}
        />
      </svg>
      <div className="-mt-6 text-2xl font-bold tabular-nums" style={{ color }}>
        {formatValue(spec.value)}
        <span className="ml-0.5 text-sm font-normal text-slate-400">{spec.unit}</span>
      </div>
      <div className="mt-1 flex items-center gap-2 text-xs">
        <span className="text-slate-400">
          목표 {spec.direction === "lower" ? "≤" : "="} {formatValue(spec.target)}{spec.unit}
        </span>
        <span
          className="rounded px-1.5 py-0.5 font-semibold text-white"
          style={{ backgroundColor: color }}
        >
          {STATUS_LABEL[spec.status]}
        </span>
      </div>
    </div>
  );
}

function formatValue(v: number): string {
  if (!Number.isFinite(v)) return "-";
  if (Number.isInteger(v)) return v.toLocaleString();
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
