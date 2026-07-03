import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "@/App";
import { useLiveStore, CONSECUTIVE_NG_THRESHOLD } from "@/store/liveStore";
import { useAuthStore } from "@/store/authStore";
import { MockWebSocket } from "./mockWebSocket";
import { makeResult, makeNg } from "./factories";
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

function resetStore() {
  useLiveStore.setState({
    feed: [],
    latest: null,
    conn: "connecting",
    reconnectAttempts: 0,
    consecutiveNg: 0,
    lastAlarm: null,
    consecutiveAlarmActive: false,
    soundEnabled: false, // 테스트에서 비프 비활성
  });
  // 쓰기(재확인)는 인증을 요구한다(§7.4) → 기본 작업자 세션을 주입.
  // (미인증/401 유도 흐름은 auth.test.tsx 에서 별도 검증.)
  useAuthStore.setState({
    session: { token: "test-token", role: Role.OPERATOR, username: "tester" },
    loginPromptOpen: false,
  });
}

beforeEach(() => {
  resetStore();
  MockWebSocket.reset();
  vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("App 통합 (WS → 카드 / NG 알람 / 재확인 PATCH)", () => {
  it("WS open 시 연결 인디케이터가 '실시간 연결됨' 으로 바뀐다", async () => {
    renderApp();
    MockWebSocket.last().triggerOpen();
    await waitFor(() =>
      expect(screen.getByText("실시간 연결됨")).toBeInTheDocument(),
    );
  });

  it("inspection 메시지 수신 시 검사 카드가 렌더된다", async () => {
    renderApp();
    const ws = MockWebSocket.last();
    ws.triggerOpen();
    ws.triggerMessage({ event: "inspection", data: makeResult({ id: 11 }) });
    await waitFor(() =>
      expect(screen.getByTestId("inspection-card")).toBeInTheDocument(),
    );
    const card = screen.getByTestId("inspection-card");
    expect(card).toHaveTextContent("LOT-001");
    expect(card).toHaveAttribute("data-verdict", "OK");
  });

  it("alarm 메시지 수신 시 NG 알람 배너를 보여준다", async () => {
    renderApp();
    const ws = MockWebSocket.last();
    ws.triggerOpen();
    ws.triggerMessage({
      event: "alarm",
      data: { id: 3, lot: "LOT-NG", defect_codes: ["SCR"] },
    });
    await waitFor(() =>
      expect(screen.getByTestId("ng-alarm")).toBeInTheDocument(),
    );
    expect(screen.getByText(/LOT-NG/)).toBeInTheDocument();
  });

  it("연속 NG 임계 초과 시 관리자 확인 배너가 뜨고 확인 시 사라진다", async () => {
    renderApp();
    const ws = MockWebSocket.last();
    ws.triggerOpen();
    for (let i = 0; i < CONSECUTIVE_NG_THRESHOLD; i++) {
      ws.triggerMessage({ event: "inspection", data: makeNg({ id: 50 + i }) });
    }
    await waitFor(() =>
      expect(screen.getByTestId("consecutive-alarm")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("ack-consecutive"));
    await waitFor(() =>
      expect(screen.queryByTestId("consecutive-alarm")).not.toBeInTheDocument(),
    );
  });

  it("WS 서버 종료 시 재연결 상태로 전환된다(자동 재연결)", async () => {
    vi.useFakeTimers();
    renderApp();
    const ws = MockWebSocket.last();
    ws.triggerOpen();
    expect(useLiveStore.getState().conn).toBe("open");
    ws.triggerServerClose();
    // onclose → scheduleReconnect → setConn("reconnecting")
    expect(useLiveStore.getState().conn).toBe("reconnecting");
    vi.useRealTimers();
  });

  it("재확인 다이얼로그에서 PATCH /review 가 호출되고 store 가 갱신된다", async () => {
    const ng = makeNg({ id: 77 });
    const updated: InspectionResult = {
      ...ng,
      manual_verdict: ng.final_verdict,
      review_flag: false,
    };
    const reviewMock = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      // ImageView(useAuthedImage)가 검사 이미지 바이트를 요청한다 → 빈 응답으로 흘려보냄.
      if (url.includes("/images/")) {
        return new Response(new Blob([]), {
          status: 200,
          headers: { "Content-Type": "image/jpeg" },
        });
      }
      reviewMock();
      expect(url).toContain("/inspection/77/review");
      expect(init?.method).toBe("PATCH");
      const body = JSON.parse(String(init?.body));
      expect(body.manual_verdict).toBe("NG");
      return new Response(JSON.stringify(updated), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();
    const ws = MockWebSocket.last();
    ws.triggerOpen();
    ws.triggerMessage({ event: "inspection", data: ng });

    await waitFor(() =>
      expect(screen.getByTestId("open-review")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("open-review"));
    await waitFor(() =>
      expect(screen.getByTestId("review-dialog")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("review-ng"));
    fireEvent.click(screen.getByTestId("review-submit"));

    await waitFor(() => expect(reviewMock).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(useLiveStore.getState().latest?.review_flag).toBe(false),
    );
  });
});
