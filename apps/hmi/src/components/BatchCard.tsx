/**
 * 다중 튜브 배치 카드 (CLAUDE.md §5 M10, 부록 A.1 다중 튜브 오더).
 *
 * 한 번의 촬영(배치)으로 검사된 튜브 N개를 카드 1개로 묶어 표시한다.
 * - 대표 result 오버레이 이미지 1개(모든 튜브가 박스로 표기됨)를 크게.
 *   raw 토글로 원본도 볼 수 있다(선택).
 * - 요약: 검출 N개 / OK / NG / 전체 판정(NG 튜브 1개라도 있으면 NG).
 * - 튜브별 그리드: #index, OK/NG(색+아이콘 이중표기, 색약 고려), 불량코드, 길이mm.
 *   NG 튜브는 빨강 강조 + 클릭 시 재확인(M10).
 * 현장 대형 디스플레이 가독성을 위해 큰 폰트/그리드.
 */
import { useState } from "react";
import type { InspectionResult } from "@aivis/shared-types";
import { Verdict } from "@aivis/shared-types";
import type { BatchGroup } from "@/lib/batching";
import { VerdictBadge } from "./VerdictBadge";
import { DefectBadges } from "./DefectBadges";
import { ImageView } from "./ImageView";

function fmtMm(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(2)} mm`;
}

export interface BatchCardProps {
  batch: BatchGroup;
  onReview?: (r: InspectionResult) => void;
}

export function BatchCard({ batch, onReview }: BatchCardProps) {
  const [showRaw, setShowRaw] = useState(false);
  const isNg = batch.verdict === Verdict.NG;
  const frame = isNg
    ? "border-ng ring-4 ring-ng/30"
    : "border-ok ring-4 ring-ok/20";

  return (
    <section
      className={`flex flex-col gap-4 rounded-2xl border-4 bg-white p-6 shadow-lg ${frame}`}
      data-testid="batch-card"
      data-verdict={batch.verdict}
      data-total={batch.total}
      data-ng={batch.ngCount}
      aria-label={`배치 검사결과 LOT ${batch.lot}, 총 ${batch.total}개 중 NG ${batch.ngCount}개`}
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-hmi-lg font-black">
            {batch.item_code}
            <span className="ml-3 text-hmi text-gray-500">
              LOT {batch.lot}
            </span>
            <span className="ml-3 rounded-lg bg-gray-800 px-3 py-1 text-base font-bold text-white align-middle">
              배치 {batch.total}개
            </span>
          </div>
          <div className="text-base text-gray-500">
            {batch.cam_id} ·{" "}
            {new Date(batch.inspected_at).toLocaleString("ko-KR")}
          </div>
        </div>
        <VerdictBadge
          verdict={batch.verdict}
          size="xl"
          label={isNg ? "NG" : "OK"}
        />
      </header>

      {/* 배치 오버레이 대표 이미지(모든 튜브 표기). raw 토글 선택. */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-end">
          <button
            type="button"
            onClick={() => setShowRaw((v) => !v)}
            aria-pressed={showRaw}
            className="rounded-lg border-2 border-gray-300 bg-white px-3 py-1 text-sm font-semibold text-gray-700 active:scale-95"
            data-testid="batch-raw-toggle"
          >
            {showRaw ? "원본 보는 중" : "원본 보기"}
          </button>
        </div>
        <ImageView
          label={showRaw ? "원본(배치)" : "판정 결과(배치 오버레이)"}
          inspectionId={batch.representativeId}
          kind={showRaw ? "raw" : "result"}
        />
      </div>

      {/* 요약: 검출/OK/NG/전체 판정. */}
      <div className="grid grid-cols-3 gap-3 text-center">
        <Summary label="검출" value={`${batch.total}개`} emphasize />
        <Summary label="OK" value={`${batch.okCount}개`} tone="ok" />
        <Summary
          label="NG"
          value={`${batch.ngCount}개`}
          tone={batch.ngCount > 0 ? "ng" : "muted"}
        />
      </div>

      {isNg && (
        <div
          className="rounded-xl border-2 border-ng bg-ng-bg px-4 py-3 text-hmi font-bold text-ng-fg"
          data-testid="batch-ng-summary"
        >
          <span aria-hidden className="mr-2 font-black">
            ✕
          </span>
          총 {batch.total}개 중 {batch.ngCount}개 NG
        </div>
      )}

      {/* 튜브별 그리드. */}
      <ul
        className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4"
        data-testid="batch-tube-grid"
      >
        {batch.tubes.map((t, i) => (
          <TubeCell
            key={t.id ?? `tube-${i}`}
            tube={t}
            index={t.tube_index ?? i}
            onReview={onReview}
          />
        ))}
      </ul>
    </section>
  );
}

function TubeCell({
  tube,
  index,
  onReview,
}: {
  tube: InspectionResult;
  index: number;
  onReview?: (r: InspectionResult) => void;
}) {
  const isNg = tube.final_verdict === Verdict.NG;
  const clickable = isNg && !!onReview;
  return (
    <li>
      <button
        type="button"
        disabled={!clickable}
        onClick={() => clickable && onReview?.(tube)}
        data-testid="batch-tube"
        data-verdict={tube.final_verdict}
        data-tube-index={index}
        className={`flex w-full flex-col gap-2 rounded-xl border-2 p-3 text-left ${
          isNg ? "border-ng bg-ng-bg" : "border-ok bg-ok-bg/40"
        } ${clickable ? "active:scale-[0.98]" : "cursor-default"}`}
      >
        <div className="flex items-center justify-between">
          <span className="text-hmi font-black text-gray-800">#{index}</span>
          <VerdictBadge verdict={tube.final_verdict} size="sm" />
        </div>
        <div className="text-base font-bold text-gray-900">
          {fmtMm(tube.meas_length_mm)}
        </div>
        {isNg && <DefectBadges codes={tube.defect_codes} size="sm" />}
      </button>
    </li>
  );
}

function Summary({
  label,
  value,
  emphasize,
  tone = "default",
}: {
  label: string;
  value: string;
  emphasize?: boolean;
  tone?: "default" | "ok" | "ng" | "muted";
}) {
  const toneCls =
    tone === "ng"
      ? "text-ng-fg"
      : tone === "ok"
        ? "text-ok-fg"
        : tone === "muted"
          ? "text-gray-400"
          : "text-gray-900";
  return (
    <div className="rounded-xl bg-gray-50 p-3">
      <div className="text-sm font-medium text-gray-500">{label}</div>
      <div
        className={`font-black ${emphasize ? "text-hmi-lg" : "text-hmi"} ${toneCls}`}
      >
        {value}
      </div>
    </div>
  );
}
