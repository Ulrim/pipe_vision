/**
 * 대시보드 라이브 뷰 (2026-10-20) — 실시간 현황 카드에서 원격으로 여는 카메라 영상.
 * 열면 start, 닫으면 stop, 프레임 번호가 바뀔 때만 JPEG, 검사 영역·초점·경고, 1장 촬영.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { LiveViewState } from "@aivis/shared-types";
import { LiveViewModal } from "./LiveViewModal";

const BASE: LiveViewState = {
  cam_id: "PI-CAM2",
  requested: true,
  streaming: true,
  seq: 3,
  capture_pending: false,
  focus: 200,
  focus_peak: 400,
  brightness: 30,
  saturated_pct: 0,
  dark_pct: 5,
  width: 640,
  height: 480,
  roi: [0.2, 0.2, 0.8, 0.8],
  session_left_s: 300,
};

function stubApi(st: LiveViewState) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url, "http://x").pathname;
      calls.push(`${init?.method ?? "GET"} ${path}`);
      if (path.endsWith("/frame.jpg"))
        return new Response(new Blob(["jpeg"], { type: "image/jpeg" }), { status: 200 });
      if (path.endsWith("/capture"))
        return new Response(JSON.stringify({ status: "requested" }), { status: 202 });
      return new Response(JSON.stringify(st), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LiveViewModal", () => {
  it("영상·검사 영역·초점·어두움 경고, 1장 촬영, 닫으면 끈다", async () => {
    const calls = stubApi(BASE);
    const onClose = vi.fn();
    const { unmount } = render(<LiveViewModal camId="PI-CAM2" onClose={onClose} pollMs={20} />);

    expect(await screen.findByTestId("live-frame")).toBeInTheDocument();
    expect(screen.getByTestId("live-roi")).toBeInTheDocument();
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getByText(/렌즈를 천천히 돌려/)).toBeInTheDocument();
    expect(screen.getByTestId("live-advice")).toHaveTextContent("너무 어둡습니다");
    expect(calls[0]).toBe("POST /system/live/PI-CAM2/start");
    await waitFor(() => expect(calls.filter((c) => c.endsWith("/frame.jpg")).length).toBe(1));

    await userEvent.click(screen.getByTestId("live-capture"));
    expect(await screen.findByText(/촬영 요청됨/)).toBeInTheDocument();
    expect(calls).toContain("POST /system/live/PI-CAM2/capture");

    await userEvent.click(screen.getByTestId("live-close"));
    expect(onClose).toHaveBeenCalled();
    unmount();
    expect(calls.at(-1)).toBe("POST /system/live/PI-CAM2/stop");
  });
});
