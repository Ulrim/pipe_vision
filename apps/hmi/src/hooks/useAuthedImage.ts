/**
 * 인증 검사 이미지 로딩 훅 (CLAUDE.md §5 M10).
 *
 * GET {API_BASE}/inspection/{id}/images/{kind} 는 image/jpeg 바이트를 반환하며
 * operator+ JWT 가 필요하다. <img src=URL> 은 Authorization 헤더를 못 싣으므로
 * 인증 fetch → Blob → URL.createObjectURL 로 표시한다.
 *
 * - id/kind 변경·언마운트 시 이전 objectURL 을 revoke(메모리 누수 방지).
 * - 401/404/네트워크 오류는 throw 하지 않고 status='error' 로 노출 →
 *   호출부에서 "이미지 없음" placeholder 를 안전하게 보여준다.
 * - 토큰은 authStore 에서 가져온다(미인증이면 fetch 시 헤더 생략).
 */
import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/config";
import { getAuthToken } from "@/store/authStore";

export type ImageKind = "raw" | "result";

export type AuthedImageStatus = "loading" | "ready" | "error";

export interface AuthedImageState {
  /** objectURL(준비됐을 때) — <img src> 에 그대로 사용. */
  url: string | null;
  status: AuthedImageStatus;
}

/**
 * 검사 이미지 바이트를 인증 fetch 로 받아 objectURL 로 노출한다.
 * id 가 없으면(null/undefined) 로딩하지 않고 error 로 둔다(placeholder).
 */
export function useAuthedImage(
  id: number | null | undefined,
  kind: ImageKind,
): AuthedImageState {
  const [state, setState] = useState<AuthedImageState>({
    url: null,
    status: "loading",
  });

  useEffect(() => {
    if (id === null || id === undefined) {
      setState({ url: null, status: "error" });
      return;
    }

    let cancelled = false;
    let objectUrl: string | null = null;
    setState({ url: null, status: "loading" });

    const headers: Record<string, string> = {};
    const token = getAuthToken();
    if (token) headers.Authorization = `Bearer ${token}`;

    fetch(`${API_BASE}/inspection/${id}/images/${kind}`, { headers })
      .then(async (res) => {
        if (!res.ok) {
          // 401/404/경로없음 → placeholder.
          if (!cancelled) setState({ url: null, status: "error" });
          return;
        }
        const blob = await res.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ url: objectUrl, status: "ready" });
      })
      .catch(() => {
        // 네트워크 오류 → placeholder(화면 깨지지 않게).
        if (!cancelled) setState({ url: null, status: "error" });
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [id, kind]);

  return state;
}
