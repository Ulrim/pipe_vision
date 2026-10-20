/**
 * 라이브 뷰 (2026-10-20).
 *
 * - 열면 start, 닫으면 stop. 프레임 번호가 바뀔 때만 JPEG 을 다시 받는다.
 * - 검사 영역 오버레이, 초점 막대(최고값 대비 %), 노출 경고.
 * - 1장 촬영은 capture 를 보낸다.
 * - 시간이 끝나면 '다시 켜기' — stop 을 먼저 끝내고 start 한다.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { LiveViewState } from "@aivis/shared-types";
import { Role } from "@aivis/shared-types";
import { LiveView } from "@/components/LiveView";
import { focusRatio, liveAdvice } from "@/hooks/useLiveView";
import { useAuthStore } from "@/store/authStore";

const BASE: LiveViewState = {
  cam_id: "PI-CAM1",
  requested: true,
  streaming: true,
  seq: 1,
  capture_pending: false,
  focus: 450,
  focus_peak: 500,
  brightness: 120,
  saturated_pct: 0.2,
  dark_pct: 2,
  width: 640,
  height: 480,
  roi: [0.1, 0.2, 0.9, 0.8],
  session_left_s: 590,
};

describe("지표 해석", () => {
  it("초점은 이번 세션 최고값 대비", () => {
    expect(focusRatio(BASE)).toBeCloseTo(0.9);
    expect(focusRatio({ ...BASE, focus_peak: null })).toBeNull();
    expect(focusRatio(null)).toBeNull();
  });

  it("대기·끊김·포화·암부·밝기 안내", () => {
    expect(liveAdvice({ ...BASE, streaming: false, seq: 0 })[0].text).toContain("연결 대기");
    expect(
      liveAdvice({ ...BASE, streaming: false, seq: 5, age_s: 9, frame_at: "2026-10-20T00:00:00Z" })[0].text,
    ).toContain("끊김");
    // 다시 켠 직후(서버가 옛 장면을 지웠다) — 번호는 남아도 '대기' 다.
    expect(liveAdvice({ ...BASE, streaming: false, seq: 5, frame_at: null })[0].text).toContain("연결 대기");
    expect(liveAdvice(BASE)).toEqual([]);
    const warn = liveAdvice({ ...BASE, saturated_pct: 3.2, dark_pct: 40, brightness: 230 });
    expect(warn.map((a) => a.text).join(" ")).toMatch(/하얗게.*3\.2%.*검게.*40%.*너무 밝/);
    expect(liveAdvice({ ...BASE, requested: false })).toEqual([]);
  });
});

type Call = { method: string; path: string };

function stubApi(stateFor: (n: number) => LiveViewState) {
  const calls: Call[] = [];
  let polls = 0;
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname;
      const method = init?.method ?? "GET";
      calls.push({ method, path });
      if (path.endsWith("/start")) return json({ ...BASE, streaming: false, seq: 0 });
      if (path.endsWith("/stop")) return json({ ...BASE, requested: false, streaming: false });
      if (path.endsWith("/capture")) return json({ status: "requested" }, 202);
      if (path.endsWith("/frame.jpg"))
        return new Response(new Blob(["jpeg"], { type: "image/jpeg" }), { status: 200 });
      polls += 1;
      return json(stateFor(polls));
    }),
  );
  return calls;
}

beforeEach(() => {
  useAuthStore.setState({
    session: { token: "t", role: Role.OPERATOR, username: "op" },
    loginPromptOpen: false,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("LiveView", () => {
  it("켜고, 영상·검사 영역·초점을 보이고, 같은 프레임은 다시 받지 않고, 닫으면 끈다", async () => {
    const calls = stubApi((n) => ({ ...BASE, seq: n < 3 ? 1 : 2 }));
    const onClose = vi.fn();
    const { unmount } = render(<LiveView camId="PI-CAM1" onClose={onClose} pollMs={20} />);

    expect(await screen.findByTestId("live-frame")).toBeInTheDocument();
    expect(screen.getByTestId("live-roi")).toBeInTheDocument();
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "90");
    expect(screen.getByText(/최고 근처/)).toBeInTheDocument();
    await waitFor(() =>
      expect(calls.filter((c) => c.path.endsWith("/frame.jpg")).length).toBe(2),
    );
    await act(() => new Promise((r) => setTimeout(r, 80)));
    expect(calls.filter((c) => c.path.endsWith("/frame.jpg")).length).toBe(2);
    expect(calls[0]).toEqual({ method: "POST", path: "/system/live/PI-CAM1/start" });

    fireEvent.click(screen.getByTestId("live-close"));
    expect(onClose).toHaveBeenCalled();
    unmount();
    expect(calls.at(-1)).toEqual({ method: "POST", path: "/system/live/PI-CAM1/stop" });
  });

  it("포화 경고와 1장 촬영", async () => {
    const calls = stubApi(() => ({ ...BASE, saturated_pct: 4.5 }));
    render(<LiveView camId="PI-CAM1" onClose={() => undefined} pollMs={20} />);
    expect(await screen.findByText(/하얗게 날아간 곳 4\.5%/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("live-capture"));
    expect(await screen.findByText(/촬영 요청됨/)).toBeInTheDocument();
    expect(calls).toContainEqual({ method: "POST", path: "/system/live/PI-CAM1/capture" });
    expect(screen.getByTestId("live-capture")).toBeDisabled();
  });

  it("시간이 끝나면 다시 켜기 — stop 다음 start", async () => {
    const calls = stubApi(() => ({ ...BASE, requested: false, streaming: false }));
    render(<LiveView camId="PI-CAM1" onClose={() => undefined} pollMs={20} />);
    fireEvent.click(await screen.findByTestId("live-restart"));
    await waitFor(() => {
      const tail = calls.filter((c) => c.method === "POST").slice(-2).map((c) => c.path);
      expect(tail).toEqual(["/system/live/PI-CAM1/stop", "/system/live/PI-CAM1/start"]);
    });
  });

  it("격자 켜기·끄기", async () => {
    stubApi(() => BASE);
    render(<LiveView camId="PI-CAM1" onClose={() => undefined} pollMs={20} />);
    const btn = screen.getByTestId("live-grid");
    expect(btn).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-pressed", "true");
  });
});
