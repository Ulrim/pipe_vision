/**
 * 불량유형 코드 뱃지 (CLAUDE.md §7.2: LEN/OIL/DIS/SCR/MULTI).
 * 한글 라벨 병기로 현장 가독성 확보.
 */
import type { DefectCode } from "@aivis/shared-types";

const LABELS: Record<string, string> = {
  LEN: "길이",
  OIL: "유분기",
  DIS: "변색",
  SCR: "스크래치",
  MULTI: "복합",
};

export interface DefectBadgesProps {
  codes: DefectCode[] | null | undefined;
  size?: "sm" | "md";
}

export function DefectBadges({ codes, size = "md" }: DefectBadgesProps) {
  if (!codes || codes.length === 0) return null;
  const sz = size === "sm" ? "text-sm px-2 py-0.5" : "text-hmi px-3 py-1";
  return (
    <div className="flex flex-wrap gap-2" data-testid="defect-badges">
      {codes.map((c) => (
        <span
          key={c}
          className={`inline-flex items-center rounded-md bg-ng text-white font-bold ${sz}`}
          data-defect={c}
        >
          {c} · {LABELS[c] ?? c}
        </span>
      ))}
    </div>
  );
}
