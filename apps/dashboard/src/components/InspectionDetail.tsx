import { useQuery } from "@tanstack/react-query";
import type { InspectionResult } from "@aivis/shared-types";
import { fetchInspectionImages } from "@/api/endpoints";
import { VerdictBadge } from "@/components/VerdictBadge";
import { fmtNum, fmtDateTime } from "@/lib/format";

/** M8/M11 — 검사 단건 상세(이미지 경로 + 길이값 + 불량유형 + 처리속도). */
export function InspectionDetail({
  row,
  onClose,
}: {
  row: InspectionResult;
  onClose: () => void;
}): JSX.Element {
  const { data: images } = useQuery({
    queryKey: ["inspection-images", row.id],
    queryFn: () => fetchInspectionImages(row.id as number),
    enabled: row.id != null,
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      data-testid="insp-detail"
    >
      <div
        className="card max-h-[90vh] w-full max-w-3xl overflow-y-auto p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-bold">
            검사 #{row.id} · LOT {row.lot}
          </h2>
          <button type="button" className="btn-ghost" onClick={onClose} data-testid="detail-close">
            닫기
          </button>
        </div>

        <div className="grid grid-cols-2 gap-4">
          {/* 이미지 placeholder (raw/result 경로) */}
          <ImagePane label="원본" path={images?.raw_image_path ?? row.raw_image_path} />
          <ImagePane label="판정 오버레이" path={images?.result_image_path ?? row.result_image_path} />
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-3">
          <Row k="품목" v={row.item_code} />
          <Row k="검사시각" v={fmtDateTime(row.inspected_at)} />
          <Row k="Cam" v={row.cam_id} />
          <Row k="기준길이(mm)" v={fmtNum(row.ref_length_mm, 3)} />
          <Row k="측정길이(mm)" v={fmtNum(row.meas_length_mm, 3)} />
          <Row k="편차(mm)" v={fmtNum(row.deviation_mm, 3)} />
          <Row k="유분기" v={fmtNum(row.oil_score, 4)} />
          <Row k="변색" v={fmtNum(row.discolor_score, 4)} />
          <Row k="스크래치" v={fmtNum(row.scratch_score, 4)} />
          <Row k="신뢰도" v={fmtNum(row.confidence, 4)} />
          <Row k="처리속도(ms)" v={fmtNum(row.proc_time_ms, 0)} />
          <Row k="MES 연계" v={row.mes_synced ? "완료" : "대기"} />
        </dl>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <span className="text-sm text-slate-500">종합판정</span>
          <VerdictBadge verdict={row.final_verdict} />
          <span className="text-sm text-slate-500">불량유형</span>
          <span className="font-mono text-sm" data-testid="detail-defects">
            {(row.defect_codes ?? []).join(", ") || "없음"}
          </span>
          {row.review_flag && (
            <span className="rounded bg-warn/20 px-2 py-0.5 text-xs text-warn">재확인 대상</span>
          )}
        </div>
      </div>
    </div>
  );
}

function ImagePane({ label, path }: { label: string; path?: string | null }): JSX.Element {
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-slate-500">{label}</div>
      <div className="flex aspect-video items-center justify-center rounded border border-dashed border-slate-300 bg-slate-50 text-xs text-slate-400">
        {path ? (
          <span className="break-all px-2 text-center font-mono">{path}</span>
        ) : (
          <span>이미지 없음</span>
        )}
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }): JSX.Element {
  return (
    <div>
      <dt className="text-xs text-slate-400">{k}</dt>
      <dd className="tabular-nums">{v}</dd>
    </div>
  );
}
