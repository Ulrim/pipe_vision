import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, within, waitFor } from "@testing-library/react";
import type { StationLive, StationsResponse } from "@/api/endpoints";
import { renderApp } from "@/test/utils";

const fetchStations = vi.fn();
const fetchInspectionImageBlob = vi.fn();
vi.mock("@/api/endpoints", () => ({
  fetchStations: (...a: unknown[]) => fetchStations(...a),
  fetchInspectionImageBlob: (...a: unknown[]) => fetchInspectionImageBlob(...a),
}));

import { LivePage, agoKo, gridCols } from "./LivePage";

const NOW = new Date().toISOString();

function station(over: Partial<StationLive> = {}): StationLive {
  return {
    cam_id: "PI-CAM1",
    state: "up",
    last_seen_s: 1.2,
    stage: "CUT_LENGTH",
    item_code: "HP12",
    expected: 20,
    detected: 20,
    mismatch: false,
    error: null,
    proc_time_ms: 140,
    host: {
      cpu_temp_c: 55, cpu_percent: 30, load_1m: 0.8, mem_percent: 40,
      disk_percent: 35, disk_free_gb: 40.2, throttled: false,
    },
    last_hour: { total: 120, ng: 3, ng_rate_pct: 2.5 },
    today: { total: 900, ng: 12, ng_rate_pct: 1.3 },
    latest: {
      id: 501, inspected_at: NOW, lot: "LOT-7", item_code: "HP12",
      inspection_stage: "CUT_LENGTH", final_verdict: "NG", defect_codes: ["LEN"],
      meas_length_mm: 430.18, deviation_mm: 0.18, length_verdict: "NG",
      oil_score: null, discolor_score: null, scratch_score: null, review_flag: false,
      has_result_image: true, has_raw_image: true, frame_total: 20, frame_ng: 1,
      limits: {
        ref_length_mm: 430, tol_plus_mm: 0.1, tol_minus_mm: 0.1, oil_threshold: 0.4,
        discolor_threshold: 0.3, scratch_threshold: 0.2, expected_count: 20,
      },
    },
    ...over,
  };
}

function resp(stations: StationLive[]): StationsResponse {
  return { ts: NOW, stations };
}

beforeEach(() => {
  fetchStations.mockReset();
  fetchInspectionImageBlob.mockReset();
  fetchInspectionImageBlob.mockResolvedValue(new Blob(["x"], { type: "image/jpeg" }));
});

describe("LivePage — 파이 여러 대를 한 화면에", () => {
  it("스테이션마다 카드 한 장: 모드·상태·판정·NG 사유 수치·사진·실적·파이 상태", async () => {
    fetchStations.mockResolvedValue(resp([
      station(),
      station({
        cam_id: "PI-CAM2", stage: "CRATE_COUNT", detected: 18, expected: 20, mismatch: true,
        latest: {
          ...station().latest!, id: 777, inspection_stage: "CRATE_COUNT",
          defect_codes: ["COUNT"], meas_length_mm: null, deviation_mm: null, frame_total: 1, frame_ng: 1,
        },
      }),
    ]));
    renderApp(<LivePage />);
    const c1 = await screen.findByTestId("station-card-PI-CAM1");
    const c2 = screen.getByTestId("station-card-PI-CAM2");

    expect(within(c1).getByTestId("card-stage")).toHaveTextContent("길이 검사");
    expect(within(c1).getByTestId("card-health")).toHaveTextContent("정상");
    expect(within(c1).getByTestId("card-verdict")).toHaveTextContent("NG");
    expect(within(c1).getByTestId("card-verdict")).toHaveTextContent("✕");
    expect(within(c1).getByTestId("card-reasons")).toHaveTextContent("길이 +0.18mm (허용 +0.10)");
    expect(within(c1).getByTestId("card-frame")).toHaveTextContent("한 장 20개 중 NG 1");
    expect(within(c1).getByTestId("card-stats")).toHaveTextContent("120 / NG 3");
    expect(within(c1).getByTestId("host-temp")).toHaveTextContent("온도 55℃");
    expect(within(c1).getByTestId("host-disk")).toHaveTextContent("남음 40.2GB");

    expect(within(c2).getByTestId("card-stage")).toHaveTextContent("개수 확인");
    expect(within(c2).getByTestId("card-reasons")).toHaveTextContent("개수 18 / 기준 20 (−2)");
    expect(within(c2).getByTestId("card-count")).toHaveTextContent("검출 18 / 기준 20");

    // 사진은 각 스테이션의 최신 결과 id 로 인증 조회.
    await waitFor(() => {
      expect(fetchInspectionImageBlob).toHaveBeenCalledWith(501, "result");
      expect(fetchInspectionImageBlob).toHaveBeenCalledWith(777, "result");
    });
    expect(await within(c1).findByTestId("card-image")).toBeInTheDocument();

    expect(screen.getByTestId("sum-stations")).toHaveTextContent("스테이션 2대");
    expect(screen.getByTestId("sum-hour")).toHaveTextContent("최근 1시간 240건 · NG 6");
  });

  it("정지한 파이는 빨간 테두리 + '정지 — 마지막 결과' 로 흐리게 — 지금도 도는 것처럼 보이면 안 된다", async () => {
    fetchStations.mockResolvedValue(resp([station({ state: "down", last_seen_s: 600 })]));
    renderApp(<LivePage />);
    const c = await screen.findByTestId("station-card-PI-CAM1");
    expect(c).toHaveAttribute("data-state", "down");
    expect(within(c).getByTestId("card-health")).toHaveTextContent("정지");
    expect(within(c).getByTestId("card-health")).toHaveTextContent("10분 전");
    const wrap = within(c).getByTestId("card-verdict-wrap");
    expect(wrap).toHaveTextContent("정지 — 마지막 결과");
    expect(wrap.className).toContain("opacity-50");
    expect(within(c).getByTestId("card-image-wrap").className).toContain("opacity-50");
    expect(screen.getByTestId("sum-down")).toHaveTextContent("정지 1");
  });

  it("취득 오류와 파이 이상(과열·전원 부족)은 색+기호로 드러난다", async () => {
    fetchStations.mockResolvedValue(resp([station({
      error: "frame timeout",
      host: { cpu_temp_c: 82, cpu_percent: 20, load_1m: 1, mem_percent: 30,
              disk_percent: 92, disk_free_gb: 1.1, throttled: true },
    })]));
    renderApp(<LivePage />);
    const c = await screen.findByTestId("station-card-PI-CAM1");
    expect(within(c).getByTestId("card-error")).toHaveTextContent("frame timeout");
    expect(within(c).getByTestId("host-temp")).toHaveAttribute("data-sev", "danger");
    expect(within(c).getByTestId("host-temp")).toHaveTextContent("✕");
    expect(within(c).getByTestId("host-disk")).toHaveAttribute("data-sev", "danger");
    expect(within(c).getByTestId("host-power")).toHaveTextContent("전원 부족");
  });

  it("사진이 서버에 없으면(2호기 디스크에만) 무엇을 설정하면 되는지 알려준다", async () => {
    fetchInspectionImageBlob.mockRejectedValue(new Error("404"));
    fetchStations.mockResolvedValue(resp([station({ cam_id: "PI-CAM2" })]));
    renderApp(<LivePage />);
    const c = await screen.findByTestId("station-card-PI-CAM2");
    expect(await within(c).findByTestId("card-image-missing")).toHaveTextContent(
      "AIVIS_STORAGE_BACKEND=api",
    );
  });

  it("결과에 사진 경로가 없으면 조회하지 않고 '사진 없음'", async () => {
    fetchStations.mockResolvedValue(resp([station({
      latest: { ...station().latest!, has_result_image: false },
    })]));
    renderApp(<LivePage />);
    const c = await screen.findByTestId("station-card-PI-CAM1");
    expect(within(c).getByTestId("card-image-none")).toBeInTheDocument();
    expect(fetchInspectionImageBlob).not.toHaveBeenCalled();
  });

  it("구 워커(파이 상태 미전송)와 결과 없는 스테이션도 깨지지 않는다", async () => {
    fetchStations.mockResolvedValue(resp([station({ host: null, latest: null })]));
    renderApp(<LivePage />);
    const c = await screen.findByTestId("station-card-PI-CAM1");
    expect(within(c).getByTestId("card-host-none")).toBeInTheDocument();
    expect(within(c).getByTestId("card-no-result")).toBeInTheDocument();
  });

  it("카메라가 하나도 없으면 안내", async () => {
    fetchStations.mockResolvedValue(resp([]));
    renderApp(<LivePage />);
    expect(await screen.findByTestId("live-empty")).toBeInTheDocument();
  });

  it("서버 연결 실패를 숨기지 않는다", async () => {
    fetchStations.mockRejectedValue(new Error("Failed to fetch"));
    renderApp(<LivePage />);
    expect(await screen.findByTestId("live-error")).toHaveTextContent("서버 연결 실패");
  });

  it("gridCols — 두 대면 2열(3열로 두면 화면 3분의 1이 빈다), 네 대는 2x2", () => {
    expect(gridCols(1)).toBe("");
    expect(gridCols(2)).toBe("lg:grid-cols-2");
    expect(gridCols(3)).toContain("2xl:grid-cols-3");
    expect(gridCols(4)).toBe("lg:grid-cols-2");
    expect(gridCols(6)).toContain("2xl:grid-cols-3");
  });

  it("agoKo — 초/분/시간, 기록 없음", () => {
    expect(agoKo(null)).toBe("응답 기록 없음");
    expect(agoKo(3.4)).toBe("3초 전");
    expect(agoKo(125)).toBe("2분 전");
    expect(agoKo(7300)).toBe("2시간 전");
  });
});
