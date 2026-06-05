/**
 * 원본/결과 이미지 표시 (M10). 경로가 있으면 API_BASE 기준으로 로드,
 * 없으면 placeholder. 현재 단계는 placeholder 위주(이미지 서빙은 P4/백엔드 연계).
 */
import { API_BASE } from "@/lib/config";

export interface ImageViewProps {
  label: string;
  path?: string | null;
}

export function ImageView({ label, path }: ImageViewProps) {
  // MinIO/NAS 경로는 백엔드 정적 서빙 규약 확정 전이므로 placeholder 우선.
  const src = path ? `${API_BASE}/static/${encodeURIComponent(path)}` : null;
  return (
    <figure className="flex flex-col items-center" data-testid="image-view">
      <div className="flex aspect-video w-full items-center justify-center overflow-hidden rounded-lg border-2 border-dashed border-gray-300 bg-gray-50">
        {src ? (
          <img
            src={src}
            alt={label}
            className="h-full w-full object-contain"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = "none";
            }}
          />
        ) : (
          <span className="text-hmi text-gray-400">이미지 없음</span>
        )}
      </div>
      <figcaption className="mt-1 text-sm font-medium text-gray-500">
        {label}
      </figcaption>
    </figure>
  );
}
