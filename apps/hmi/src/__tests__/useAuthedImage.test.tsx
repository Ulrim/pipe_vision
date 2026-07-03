/**
 * useAuthedImage 단위 테스트 (CLAUDE.md §5 M10).
 *
 * 검증 포인트:
 *  - Authorization: Bearer <token> 헤더로 검사 이미지 바이트를 fetch 한다.
 *  - 200 응답 시 blob → URL.createObjectURL 로 objectURL 을 만들고 status=ready.
 *  - 언마운트 / id·kind 변경 시 이전 objectURL 을 revoke(메모리 누수 방지).
 *  - 404(및 네트워크 오류) 시 throw 없이 status=error(placeholder 유도).
 *  - id 가 없으면(null) 로딩하지 않고 error.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useAuthedImage } from "@/hooks/useAuthedImage";
import { useAuthStore } from "@/store/authStore";
import { Role } from "@aivis/shared-types";

function setSession(token: string | null): void {
  useAuthStore.setState({
    session: token
      ? { token, role: Role.OPERATOR, username: "tester" }
      : null,
    loginPromptOpen: false,
  });
}

beforeEach(() => {
  setSession("test-token");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useAuthedImage", () => {
  it("Authorization 헤더로 fetch 하고 objectURL 을 만들어 ready 가 된다", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(new Blob(["jpegbytes"], { type: "image/jpeg" }), {
        status: 200,
        headers: { "Content-Type": "image/jpeg" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const createSpy = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:test-1");

    const { result } = renderHook(() => useAuthedImage(42, "result"));

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.url).toBe("blob:test-1");

    // 올바른 경로 + Bearer 토큰.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toContain("/inspection/42/images/result");
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer test-token",
    );
    expect(createSpy).toHaveBeenCalledTimes(1);
  });

  it("언마운트 시 objectURL 을 revoke 한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(new Blob(["x"]), { status: 200 }),
      ),
    );
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:revoke-me");
    const revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

    const { result, unmount } = renderHook(() =>
      useAuthedImage(7, "raw"),
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));

    unmount();
    expect(revokeSpy).toHaveBeenCalledWith("blob:revoke-me");
  });

  it("id/kind 변경 시 이전 objectURL 을 revoke 하고 새로 로드한다", async () => {
    let n = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(new Blob(["x"]), { status: 200 })),
    );
    vi.spyOn(URL, "createObjectURL").mockImplementation(
      () => `blob:url-${++n}`,
    );
    const revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});

    const { result, rerender } = renderHook(
      ({ kind }: { kind: "raw" | "result" }) => useAuthedImage(9, kind),
      { initialProps: { kind: "result" as "raw" | "result" } },
    );
    await waitFor(() => expect(result.current.url).toBe("blob:url-1"));

    rerender({ kind: "raw" });
    await waitFor(() => expect(result.current.url).toBe("blob:url-2"));
    // 이전(result) objectURL 정리.
    expect(revokeSpy).toHaveBeenCalledWith("blob:url-1");
  });

  it("404 응답 시 throw 없이 error 상태가 된다(placeholder)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 404 })),
    );
    const { result } = renderHook(() => useAuthedImage(404, "result"));
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.url).toBeNull();
  });

  it("네트워크 오류 시 error 상태가 된다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );
    const { result } = renderHook(() => useAuthedImage(1, "result"));
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.url).toBeNull();
  });

  it("id 가 없으면 fetch 없이 error 상태가 된다", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useAuthedImage(null, "result"));
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("미인증이면 Authorization 헤더 없이 요청한다", async () => {
    setSession(null);
    const fetchMock = vi.fn(async () =>
      new Response(new Blob(["x"]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:noauth");

    const { result } = renderHook(() => useAuthedImage(5, "raw"));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });
});
