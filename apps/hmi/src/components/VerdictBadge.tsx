/**
 * OK/NG 판정 뱃지 — 색 + 아이콘 이중표기(CLAUDE.md §5 M10, 색약 고려).
 * 색 단독 의존 금지: OK=초록+체크, NG=빨강+X. 라벨 텍스트도 병기.
 */
import type { Verdict } from "@aivis/shared-types";
import { Verdict as V } from "@aivis/shared-types";

type Size = "sm" | "md" | "xl";

const SIZE: Record<Size, string> = {
  sm: "text-base px-2 py-1 gap-1",
  md: "text-hmi px-4 py-2 gap-2",
  xl: "text-hmi-xl px-8 py-4 gap-4",
};

export interface VerdictBadgeProps {
  verdict: Verdict | null | undefined;
  size?: Size;
  label?: string;
}

export function VerdictBadge({ verdict, size = "md", label }: VerdictBadgeProps) {
  const isOk = verdict === V.OK;
  const isNg = verdict === V.NG;
  const text = label ?? (verdict ?? "—");

  const cls = isOk
    ? "bg-ok-bg text-ok-fg border-ok"
    : isNg
      ? "bg-ng-bg text-ng-fg border-ng"
      : "bg-gray-100 text-gray-500 border-gray-300";

  // 아이콘: OK=✓, NG=✕, 미판정=•. 색약 사용자도 형태로 구분.
  const icon = isOk ? "✓" : isNg ? "✕" : "•";

  return (
    <span
      role="status"
      aria-label={isOk ? "판정 OK" : isNg ? "판정 NG" : "판정 미정"}
      data-verdict={verdict ?? "none"}
      className={`inline-flex items-center rounded-lg border-2 font-bold ${cls} ${SIZE[size]}`}
    >
      <span aria-hidden className="font-black">
        {icon}
      </span>
      <span>{text}</span>
    </span>
  );
}
