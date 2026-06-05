import { Verdict } from "@aivis/shared-types";

/** OK/NG 배지. 색약 고려 — 색 + 아이콘/라벨 이중 표기. */
export function VerdictBadge({ verdict }: { verdict?: Verdict | string | null }): JSX.Element {
  const ng = verdict === Verdict.NG;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-bold ${
        ng ? "bg-ng-bg text-ng-fg" : "bg-ok-bg text-ok-fg"
      }`}
      data-testid="verdict-badge"
    >
      <span aria-hidden>{ng ? "✕" : "✓"}</span>
      {verdict ?? "-"}
    </span>
  );
}
