/**
 * 인증이 필요한 검사 이미지를 안전하게 표시하기 위한 훅 (M8/M11).
 *
 * `<img src>` 는 Authorization 헤더를 첨부할 수 없으므로, 토큰을 실은 fetch 로
 * 바이트를 받아 Blob → objectURL 로 변환해 노출한다. 언마운트/대상 변경 시
 * 직전 objectURL 을 revoke 해 메모리 누수를 막고, 401/403/404 등은 placeholder
 * 로 처리할 수 있도록 status 와 error 상태를 함께 반환한다.
 */
import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";
import {
  fetchInspectionImageBlob,
  type InspectionImageKind,
} from "@/api/endpoints";

export interface AuthedImage {
  /** objectURL (성공 시) — `<img src>` 에 그대로 사용. */
  url: string | null;
  loading: boolean;
  /** HTTP status (실패 시). 404/403/401 등 placeholder 분기용. */
  status: number | null;
  error: boolean;
  /** Blob 재사용(다운로드 등). 성공 시에만 채워짐. */
  blob: Blob | null;
}

/**
 * @param id   검사 id (null 이면 비활성).
 * @param kind 'raw'(원본) | 'result'(판정 오버레이).
 */
export function useAuthedImage(
  id: number | null | undefined,
  kind: InspectionImageKind,
): AuthedImage {
  const [state, setState] = useState<AuthedImage>({
    url: null,
    loading: id != null,
    status: null,
    error: false,
    blob: null,
  });
  // 직전 objectURL 추적 → 변경/언마운트 시 revoke.
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    if (id == null) {
      setState({ url: null, loading: false, status: null, error: false, blob: null });
      return;
    }

    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: false, status: null }));

    const revokePrev = () => {
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
    };

    fetchInspectionImageBlob(id, kind)
      .then((blob) => {
        if (cancelled) return;
        revokePrev();
        const url = URL.createObjectURL(blob);
        urlRef.current = url;
        setState({ url, loading: false, status: 200, error: false, blob });
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        revokePrev();
        const status = e instanceof ApiError ? e.status : null;
        setState({ url: null, loading: false, status, error: true, blob: null });
      });

    return () => {
      cancelled = true;
      revokePrev();
    };
  }, [id, kind]);

  return state;
}
