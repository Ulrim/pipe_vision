/**
 * 검사 이미지 표시 (M10). 인증 fetch→Blob→objectURL 방식으로
 * GET /inspection/{id}/images/{kind} 바이트를 받아 표시한다.
 * - 로딩 중: 스피너 placeholder.
 * - 401/404/네트워크 오류·경로없음: "이미지 없음" placeholder(화면 안 깨짐).
 * 현장 대형 디스플레이 가독성을 위해 큰 폰트 유지.
 */
import { useAuthedImage, type ImageKind } from "@/hooks/useAuthedImage";

export interface ImageViewProps {
  label: string;
  /** 검사 결과 id(없으면 placeholder). */
  inspectionId?: number | null;
  /** raw(원본) | result(판정 오버레이). 기본 result. */
  kind?: ImageKind;
}

export function ImageView({ label, inspectionId, kind = "result" }: ImageViewProps) {
  const { url, status } = useAuthedImage(inspectionId, kind);

  return (
    <figure className="flex flex-col items-center" data-testid="image-view">
      <div
        className="flex aspect-video w-full items-center justify-center overflow-hidden rounded-lg border-2 border-dashed border-gray-300 bg-gray-50"
        data-status={status}
      >
        {status === "ready" && url ? (
          <img
            src={url}
            alt={label}
            className="h-full w-full object-contain"
            data-testid="image-view-img"
          />
        ) : status === "loading" ? (
          <span
            className="text-hmi text-gray-400 motion-safe:animate-pulse"
            data-testid="image-view-loading"
          >
            불러오는 중…
          </span>
        ) : (
          <span className="text-hmi text-gray-400" data-testid="image-view-empty">
            이미지 없음
          </span>
        )}
      </div>
      <figcaption className="mt-1 text-sm font-medium text-gray-500">
        {label}
      </figcaption>
    </figure>
  );
}
