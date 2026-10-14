/**
 * MES 클라이언트 — MES 가 없을 때도 화면이 깨지지 않고 같은 문구로 알린다.
 * (80/443 배포에서 /mes-api 프록시가 없으면 SPA index.html 이 200 으로 돌아온다.)
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Role } from "@aivis/shared-types";
import { ApiError } from "./client";
import { MES_AUTH_REJECTED, MES_UNREACHABLE, fetchWorkOrders, pollIntake, requestMes } from "./mes";
import { MES_BASE } from "@/lib/env";
import { useAuthStore } from "@/store/auth";

function res(body: string, init: { status?: number; type?: string } = {}): Response {
  const headers: Record<string, string> = {};
  if (init.type) headers["content-type"] = init.type;
  return new Response(body, { status: init.status ?? 200, headers });
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  useAuthStore.getState().setAuth({ token: "tok.en", username: "kim", role: Role.QUALITY });
});

afterEach(() => {
  vi.unstubAllGlobals();
  useAuthStore.getState().clear();
});

describe("requestMes", () => {
  it("MES_BASE 로 요청하고 AIVIS 와 같은 토큰을 싣는다", async () => {
    fetchMock.mockResolvedValue(res("[]", { type: "application/json" }));
    await expect(fetchWorkOrders("PLANNED,RUNNING")).resolves.toEqual([]);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${MES_BASE}/work-orders?status=PLANNED%2CRUNNING`);
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok.en");
  });

  it("JSON 이 아닌 200 응답(SPA index.html)은 친절한 오류로 바꾼다", async () => {
    fetchMock.mockResolvedValue(res("<!doctype html><html></html>", { type: "text/html" }));
    const err = await requestMes("/orders").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe(MES_UNREACHABLE);
    expect(MES_UNREACHABLE).toBe("MES 서버에 연결할 수 없습니다");
  });

  it("네트워크 실패(MES 꺼짐)도 같은 문구", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(requestMes("/orders")).rejects.toMatchObject({ status: 0, message: MES_UNREACHABLE });
  });

  it("프록시 502 HTML 은 상태코드를 덧붙인 같은 문구", async () => {
    fetchMock.mockResolvedValue(res("<html>Bad Gateway</html>", { status: 502, type: "text/html" }));
    await expect(requestMes("/orders")).rejects.toMatchObject({
      status: 502,
      message: `${MES_UNREACHABLE} (HTTP 502)`,
    });
  });

  it("JSON 오류는 detail 을 그대로(409: 테이블 연계 꺼짐)", async () => {
    fetchMock.mockResolvedValue(
      res(JSON.stringify({ detail: "테이블 연계가 꺼져 있다" }), { status: 409, type: "application/json" }),
    );
    await expect(pollIntake()).rejects.toMatchObject({ status: 409, message: "테이블 연계가 꺼져 있다" });
    expect((fetchMock.mock.calls[0] as [string, RequestInit])[1].method).toBe("POST");
  });

  it("입력 검증(422) detail 배열은 msg 를 이어 붙인다", async () => {
    fetchMock.mockResolvedValue(
      res(JSON.stringify({ detail: [{ loc: ["body", "qty"], msg: "greater than 0" }] }), {
        status: 422,
        type: "application/json",
      }),
    );
    await expect(requestMes("/orders")).rejects.toMatchObject({ message: "greater than 0" });
  });

  it("401 은 로그아웃하지 않고 원인을 알려준다(서명키가 다르면 매번 쫓겨나므로)", async () => {
    const before = useAuthStore.getState().token;
    fetchMock.mockResolvedValue(
      res(JSON.stringify({ detail: "토큰이 유효하지 않습니다" }), { status: 401, type: "application/json" }),
    );
    await expect(requestMes("/orders")).rejects.toMatchObject({
      status: 401,
      message: MES_AUTH_REJECTED,
    });
    expect(useAuthStore.getState().token).toBe(before);
  });
});
