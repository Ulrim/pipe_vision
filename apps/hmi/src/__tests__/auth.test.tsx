/**
 * 인증 통합 테스트 (CLAUDE.md §5 M10, §7.4).
 * - 로그인 성공 → 토큰/역할 저장.
 * - 인증된 재확인 PATCH 에 Authorization: Bearer 헤더 첨부.
 * - 미인증 재확인 → 로그인 유도 → 성공 후 자동 재시도(헤더 첨부).
 * - 401(토큰 만료) → 로그인 유도 → 재시도 성공.
 * fetch/WS 는 전부 모킹.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "@/App";
import { login } from "@/api/client";
import { useLiveStore } from "@/store/liveStore";
import { useAuthStore, getAuthToken } from "@/store/authStore";
import { MockWebSocket } from "./mockWebSocket";
import { makeNg } from "./factories";
import type { InspectionResult } from "@aivis/shared-types";
import { Role } from "@aivis/shared-types";

function renderApp() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>,
  );
}

function resetStores() {
  useLiveStore.setState({
    feed: [],
    latest: null,
    conn: "connecting",
    reconnectAttempts: 0,
    consecutiveNg: 0,
    lastAlarm: null,
    consecutiveAlarmActive: false,
    soundEnabled: false,
  });
  useAuthStore.setState({ session: null, loginPromptOpen: false });
  globalThis.localStorage?.clear();
}

const TOKEN_RES = {
  access_token: "tok-abc",
  token_type: "bearer",
  role: Role.OPERATOR,
  username: "kim",
};

beforeEach(() => {
  resetStores();
  MockWebSocket.reset();
  vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** /auth/login 200, /inspection/{id}/review 200(또는 401 → 200) 핸들러. */
function makeFetchMock(updated: InspectionResult, reviewStatuses: number[] = [200]) {
  const seen: { url: string; auth?: string; method?: string }[] = [];
  let reviewCall = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const headers = (init?.headers ?? {}) as Record<string, string>;
    seen.push({ url, auth: headers.Authorization, method: init?.method });
    if (url.includes("/auth/login")) {
      return new Response(JSON.stringify(TOKEN_RES), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.includes("/review")) {
      const status = reviewStatuses[Math.min(reviewCall, reviewStatuses.length - 1)];
      reviewCall++;
      if (status === 200) {
        return new Response(JSON.stringify(updated), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ detail: "토큰 만료" }), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("{}", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  return { fetchMock, seen };
}

describe("인증 통합 (로그인 / Bearer 첨부 / 401 재시도)", () => {
  it("login() 성공 시 토큰·역할이 store 와 localStorage 에 저장된다", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(TOKEN_RES), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const tok = await login({ username: "kim", password: "pw" });
    useAuthStore.getState().setSession(tok);

    expect(useAuthStore.getState().session?.username).toBe("kim");
    expect(useAuthStore.getState().session?.role).toBe(Role.OPERATOR);
    expect(getAuthToken()).toBe("tok-abc");
    expect(globalThis.localStorage.getItem("aivis.hmi.auth")).toContain("tok-abc");
  });

  it("인증된 상태에서 재확인 PATCH 에 Authorization: Bearer 가 첨부된다", async () => {
    const ng = makeNg({ id: 77 });
    const updated: InspectionResult = { ...ng, review_flag: false };
    const { fetchMock, seen } = makeFetchMock(updated);
    vi.stubGlobal("fetch", fetchMock);

    // 선(先)로그인.
    useAuthStore.getState().setSession(TOKEN_RES);

    renderApp();
    const ws = MockWebSocket.last();
    ws.triggerOpen();
    ws.triggerMessage({ event: "inspection", data: ng });

    await waitFor(() =>
      expect(screen.getByTestId("open-review")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("open-review"));
    fireEvent.click(screen.getByTestId("review-ng"));
    fireEvent.click(screen.getByTestId("review-submit"));

    await waitFor(() => {
      const review = seen.find((s) => s.url.includes("/review"));
      expect(review?.auth).toBe("Bearer tok-abc");
    });
    await waitFor(() =>
      expect(useLiveStore.getState().latest?.review_flag).toBe(false),
    );
  });

  it("미인증 재확인 → 로그인 모달 → 로그인 성공 후 Bearer 첨부로 재시도된다", async () => {
    const ng = makeNg({ id: 88 });
    const updated: InspectionResult = { ...ng, review_flag: false };
    const { fetchMock, seen } = makeFetchMock(updated);
    vi.stubGlobal("fetch", fetchMock);

    renderApp();
    const ws = MockWebSocket.last();
    ws.triggerOpen();
    ws.triggerMessage({ event: "inspection", data: ng });

    await waitFor(() =>
      expect(screen.getByTestId("open-review")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("open-review"));
    fireEvent.click(screen.getByTestId("review-ng"));
    // 미인증 → submit 은 review 를 호출하지 않고 로그인 모달을 띄운다.
    fireEvent.click(screen.getByTestId("review-submit"));

    await waitFor(() =>
      expect(screen.getByTestId("login-modal")).toBeInTheDocument(),
    );
    expect(seen.some((s) => s.url.includes("/review"))).toBe(false);

    // 로그인 입력 → 성공 → 보류했던 재확인 자동 재시도.
    fireEvent.change(screen.getByTestId("login-username"), {
      target: { value: "kim" },
    });
    fireEvent.change(screen.getByTestId("login-password"), {
      target: { value: "pw" },
    });
    fireEvent.click(screen.getByTestId("login-submit"));

    await waitFor(() => {
      const review = seen.find((s) => s.url.includes("/review"));
      expect(review?.auth).toBe("Bearer tok-abc");
    });
    await waitFor(() =>
      expect(useLiveStore.getState().latest?.review_flag).toBe(false),
    );
  });

  it("재확인이 401 이면 로그인 유도 후 재시도해 성공한다", async () => {
    const ng = makeNg({ id: 99 });
    const updated: InspectionResult = { ...ng, review_flag: false };
    // 1차 review=401, 로그인 후 2차 review=200.
    const { fetchMock, seen } = makeFetchMock(updated, [401, 200]);
    vi.stubGlobal("fetch", fetchMock);

    // 만료된(서버가 거부하는) 토큰을 이미 보유한 상태.
    useAuthStore.getState().setSession({
      access_token: "stale",
      token_type: "bearer",
      role: Role.OPERATOR,
      username: "old",
    });

    renderApp();
    const ws = MockWebSocket.last();
    ws.triggerOpen();
    ws.triggerMessage({ event: "inspection", data: ng });

    await waitFor(() =>
      expect(screen.getByTestId("open-review")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("open-review"));
    fireEvent.click(screen.getByTestId("review-ng"));
    fireEvent.click(screen.getByTestId("review-submit"));

    // 401 → 로그인 모달.
    await waitFor(() =>
      expect(screen.getByTestId("login-modal")).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByTestId("login-username"), {
      target: { value: "kim" },
    });
    fireEvent.change(screen.getByTestId("login-password"), {
      target: { value: "pw" },
    });
    fireEvent.click(screen.getByTestId("login-submit"));

    // 재시도는 새 토큰으로.
    await waitFor(() => {
      const reviews = seen.filter((s) => s.url.includes("/review"));
      expect(reviews.length).toBe(2);
      expect(reviews[1].auth).toBe("Bearer tok-abc");
    });
    await waitFor(() =>
      expect(useLiveStore.getState().latest?.review_flag).toBe(false),
    );
  });

  it("헤더 로그아웃 시 세션이 폐기되고 토큰이 사라진다", async () => {
    useAuthStore.getState().setSession(TOKEN_RES);
    renderApp();
    MockWebSocket.last().triggerOpen();

    await waitFor(() =>
      expect(screen.getByTestId("auth-status")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("auth-logout-button"));

    expect(useAuthStore.getState().session).toBeNull();
    expect(getAuthToken()).toBeNull();
  });
});
