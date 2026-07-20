import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { LiveStatusStrip } from "@/components/LiveStatusStrip";
import { useLiveStore } from "@/store/liveStore";
import type { StatusData } from "@/types/ws";

/** 스토어에 워커 하트비트를 직접 주입. statusAt 기본 = 지금(신선). */
function setStatus(over: Partial<StatusData> = {}, statusAt = Date.now()) {
  const status: StatusData = {
    cam_id: "CAM-1",
    item_code: "HP12",
    expected: 4,
    detected: 4,
    ng: 0,
    mismatch: false,
    proc_time_ms: 120,
    ts: "2026-07-20T10:00:00+09:00",
    error: null,
    ...over,
  };
  useLiveStore.setState({ status, statusAt });
}

beforeEach(() => {
  useLiveStore.setState({ status: null, statusAt: null });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("LiveStatusStrip (워커 라이브니스 표기, 색+아이콘 이중표기)", () => {
  it("status 없으면 회색 대기 메시지를 보여준다", () => {
    render(<LiveStatusStrip />);
    const strip = screen.getByTestId("live-status-strip");
    expect(strip).toHaveAttribute("data-tone", "idle");
    expect(screen.getByText("검사 대기 중…")).toBeInTheDocument();
  });

  it("detected=0 이면 튜브 미검출(주황) 메시지를 보여준다", () => {
    setStatus({ detected: 0, expected: 4, mismatch: true });
    render(<LiveStatusStrip />);
    expect(screen.getByTestId("live-status-strip")).toHaveAttribute(
      "data-tone",
      "orange",
    );
    expect(screen.getByText(/튜브 미검출/)).toBeInTheDocument();
  });

  it("error 가 있으면 취득/검사 오류(빨강) 메시지를 보여준다", () => {
    setStatus({ error: "카메라 취득 실패" });
    render(<LiveStatusStrip />);
    expect(screen.getByTestId("live-status-strip")).toHaveAttribute(
      "data-tone",
      "danger",
    );
    expect(screen.getByText(/취득\/검사 오류/)).toBeInTheDocument();
  });

  it("detected===expected, ng===0 이면 정상(초록) 메시지를 보여준다", () => {
    setStatus({ detected: 4, expected: 4, ng: 0 });
    render(<LiveStatusStrip />);
    expect(screen.getByTestId("live-status-strip")).toHaveAttribute(
      "data-tone",
      "ok",
    );
    expect(screen.getByText(/정상/)).toBeInTheDocument();
  });

  it("정상 검출이지만 NG 가 있으면 NG 수를 병기한다(초록/빨강 혼합)", () => {
    setStatus({ detected: 4, expected: 4, ng: 2 });
    render(<LiveStatusStrip />);
    expect(screen.getByTestId("live-status-strip")).toHaveAttribute(
      "data-tone",
      "ok-ng",
    );
    expect(screen.getByText("NG 2")).toBeInTheDocument();
  });

  it("개수 불일치(검출>0, detected!==expected)면 노랑 경고를 보여준다", () => {
    setStatus({ detected: 3, expected: 4, mismatch: true });
    render(<LiveStatusStrip />);
    expect(screen.getByTestId("live-status-strip")).toHaveAttribute(
      "data-tone",
      "yellow",
    );
    expect(screen.getByText(/개수 불일치/)).toBeInTheDocument();
  });

  it("6초 초과 무신호면 '워커 응답 없음'으로 전환된다(신선도 tick)", () => {
    vi.useFakeTimers();
    const base = new Date("2026-07-20T10:00:00+09:00").getTime();
    vi.setSystemTime(base);
    setStatus({ detected: 4, expected: 4, ng: 0 }, base);

    render(<LiveStatusStrip />);
    expect(screen.getByText(/정상/)).toBeInTheDocument();

    // 7초 경과 → 1초 tick 이 재렌더를 유발해 stale 로 전환.
    act(() => {
      vi.setSystemTime(base + 7000);
      vi.advanceTimersByTime(7000);
    });
    expect(screen.getByText(/워커 응답 없음/)).toBeInTheDocument();
    expect(screen.getByTestId("live-status-strip")).toHaveAttribute(
      "data-tone",
      "danger",
    );
  });
});
