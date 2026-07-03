import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import type { MockInstance } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { Role } from "@aivis/shared-types";
import { useAuthStore } from "@/store/auth";
import { useAuthedImage } from "./useAuthedImage";

/**
 * useAuthedImage: 인증 fetch→Blob→objectURL (M8/M11).
 * 실 client(requestImageBlob) 경유 → global fetch 를 모킹해 Authorization
 * 헤더 첨부, objectURL 생성/revoke, 404 placeholder 를 검증한다.
 */

// 스파이는 매 테스트 beforeEach 에서 새로 건다. 모듈 최상위에서 한 번만 spyOn 하면
// afterEach 의 restoreAllMocks 가 첫 테스트 후 스파이를 영구 복원해(이후 setup.ts 의
// "blob:mock" 스텁이 노출) 후속 테스트가 깨진다.
let createSpy: MockInstance<(obj: Blob | MediaSource) => string>;
let revokeSpy: MockInstance<(url: string) => void>;

function jpegResponse(): Response {
  return new Response(new Blob(["bytes"], { type: "image/jpeg" }), {
    status: 200,
    headers: { "Content-Type": "image/jpeg" },
  });
}

beforeEach(() => {
  createSpy = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:obj-1");
  revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockReturnValue(undefined);
  useAuthStore.setState({ token: "tok-123", username: "op", role: Role.OPERATOR });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useAuthedImage", () => {
  it("Authorization Bearer 헤더로 fetch 하고 objectURL 을 생성한다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jpegResponse());
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useAuthedImage(7, "result"));

    await waitFor(() => expect(result.current.loading).toBe(false));

    // fetch URL + Authorization 헤더 검증.
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/inspection/7/images/result");
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-123");

    expect(createSpy).toHaveBeenCalledTimes(1);
    expect(result.current.url).toBe("blob:obj-1");
    expect(result.current.status).toBe(200);
    expect(result.current.error).toBe(false);
    // jsdom/undici 의 Response.blob() 은 전역 Blob 과 다른 realm 의 Blob 을 반환해
    // instanceof 가 실패한다 → 덕타이핑(type/size)으로 jpeg blob 임을 검증.
    expect(result.current.blob?.type).toBe("image/jpeg");
    expect(result.current.blob?.size).toBeGreaterThan(0);
  });

  it("언마운트 시 objectURL 을 revoke 한다(메모리 누수 방지)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jpegResponse()));

    const { result, unmount } = renderHook(() => useAuthedImage(9, "raw"));
    await waitFor(() => expect(result.current.url).toBe("blob:obj-1"));

    unmount();
    expect(revokeSpy).toHaveBeenCalledWith("blob:obj-1");
  });

  it("404 는 url 없이 status=404 placeholder 상태로 둔다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 404 })),
    );

    const { result } = renderHook(() => useAuthedImage(13, "result"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.url).toBeNull();
    expect(result.current.status).toBe(404);
    expect(result.current.error).toBe(true);
    expect(createSpy).not.toHaveBeenCalled();
  });

  it("id 가 null 이면 fetch 하지 않고 비활성 상태", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jpegResponse());
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useAuthedImage(null, "raw"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.url).toBeNull();
  });

  it("kind 변경 시 직전 objectURL 을 revoke 하고 재요청한다", async () => {
    // 매 호출 새 Response — 단일 인스턴스 재사용 시 두 번째 .blob() 이 소비된 body 로 실패.
    const fetchMock = vi.fn().mockImplementation(async () => jpegResponse());
    vi.stubGlobal("fetch", fetchMock);
    createSpy.mockReturnValueOnce("blob:obj-1").mockReturnValueOnce("blob:obj-2");

    const { result, rerender } = renderHook(
      ({ kind }: { kind: "raw" | "result" }) => useAuthedImage(5, kind),
      { initialProps: { kind: "result" as "raw" | "result" } },
    );
    await waitFor(() => expect(result.current.url).toBe("blob:obj-1"));

    rerender({ kind: "raw" });
    await waitFor(() => expect(result.current.url).toBe("blob:obj-2"));

    expect(revokeSpy).toHaveBeenCalledWith("blob:obj-1");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
